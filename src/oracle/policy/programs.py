"""The program allowlist.

`docs/SECURITY.md#4b`: a tool never accepts a command *string*. It accepts a program
**name** that must appear in this allowlist, plus an argv list. Three rules do the work:

  * **Pinned once, at load.** A program is resolved to an absolute path when policy is
    read, never via `PATH` at call time. `PATH` is attacker-influenceable and on Windows
    the current directory participates in the search order, so a `git.exe` dropped into
    a project folder would otherwise win.
  * **Deny wins, and an unlisted subcommand is denied.** There is no implicit allow: a
    subcommand nobody wrote down is refused, naming the rule that refused it.
  * **Batch targets are argument-hostile.** `.cmd`/`.bat` are executed by `cmd.exe`,
    whose quoting rules cannot be satisfied from an argv list (CVE-2024-3566,
    "BatBadBut"). Arguments to a batch target are restricted to characters `cmd.exe`
    cannot reinterpret, rather than trusting escaping that is known not to work.

Argv built by an *intent-shaped* tool (`git.commit` -> `git commit -m ...`) is not
model-controlled and is checked only for the program pin and the argument shape. Argv
that the model supplies (`dev.execute`) is checked against the subcommand rules as well.
That split is the whole value of Rule 1 in docs/TOOLS.md: the narrow tool is the
promise, the escape hatch is the exception that gets inspected.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

from oracle.logsink import get_logger
from oracle.policy.model import Tier

log = get_logger(__name__)

#: Characters `cmd.exe` re-interprets after Python has finished quoting. Only enforced
#: for `.cmd`/`.bat` targets, where there is no correct escaping to apply.
_CMD_METACHARACTERS = frozenset('"%&|<>^\r\n')
_BATCH_SUFFIXES = (".cmd", ".bat")

#: An argv is not an unbounded channel. Nothing legitimate here needs more.
MAX_ARGS = 64
MAX_ARG_LEN = 4096


class ProgramRejected(Exception):
    """A program or argv was refused. Always names the rule — a denial that cannot
    explain itself is a support ticket (docs/SECURITY.md#2)."""

    def __init__(self, rule: str, detail: str) -> None:
        super().__init__(detail)
        self.rule = rule
        self.detail = detail


@dataclass(frozen=True)
class ProgramRule:
    name: str
    #: Absolute, pinned at load. `None` means the program is not installed here; every
    #: call refuses rather than falling back to a PATH lookup.
    path: Path | None
    allow: frozenset[str] = frozenset()
    confirm: frozenset[str] = frozenset()
    #: Token sequences. `("push", "--force")` matches `push origin main --force`: the
    #: first token must be the subcommand, the rest may appear anywhere after it.
    deny: tuple[tuple[str, ...], ...] = ()
    #: For programs with no subcommand grammar (`python -m pytest`).
    allow_args_matching: tuple[str, ...] = ()

    @property
    def installed(self) -> bool:
        return self.path is not None

    @property
    def is_batch(self) -> bool:
        return self.path is not None and self.path.suffix.lower() in _BATCH_SUFFIXES

    @property
    def deny_subcommands(self) -> frozenset[str]:
        return frozenset(p[0] for p in self.deny if len(p) == 1)


def _pin(name: str, declared: str | None) -> Path | None:
    """Resolve a program to an absolute real path, once.

    A declared path is used as written (it is policy, authored by a human). Otherwise we
    fall back to `which` **at load time only** — the result is pinned, so a later change
    to `PATH` cannot redirect a call.
    """
    if declared:
        candidate = Path(declared)
        return candidate if candidate.exists() else None
    found = shutil.which(name)
    return Path(os.path.realpath(found)) if found else None


@dataclass(frozen=True)
class ProgramAllowlist:
    rules: dict[str, ProgramRule] = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: dict[str, Any] | None) -> ProgramAllowlist:
        rules: dict[str, ProgramRule] = {}
        for name, body in (raw or {}).items():
            body = body or {}
            subs = body.get("subcommands") or {}
            rule = ProgramRule(
                name=str(name),
                path=_pin(str(name), body.get("path")),
                allow=frozenset(str(s) for s in subs.get("allow") or []),
                confirm=frozenset(str(s) for s in subs.get("confirm") or []),
                deny=tuple(tuple(str(s).split()) for s in subs.get("deny") or []),
                allow_args_matching=tuple(str(s) for s in body.get("allow_args_matching") or []),
            )
            rules[rule.name] = rule
            log.info(
                "programs.pinned",
                program=rule.name,
                path=str(rule.path) if rule.path else None,
                installed=rule.installed,
            )
        return cls(rules=rules)

    # ------------------------------------------------------------------ lookups

    @property
    def names(self) -> list[str]:
        return sorted(self.rules)

    def rule_for(self, name: str) -> ProgramRule:
        rule = self.rules.get(name)
        if rule is None:
            raise ProgramRejected(
                "programs.allowlist",
                f"{name!r} is not on the program allowlist "
                f"(allowed: {', '.join(self.names) or 'none'})",
            )
        return rule

    def path_of(self, name: str) -> Path:
        """The pinned absolute path, or a refusal. Never a PATH lookup."""
        rule = self.rule_for(name)
        if rule.path is None:
            raise ProgramRejected(
                f"programs.{name}.path",
                f"{name} is on the allowlist but was not found on this machine",
            )
        return rule.path

    # -------------------------------------------------------------------- checks

    def check(self, name: str, args: Sequence[str]) -> Tier:
        """Validate a **model-supplied** argv. Returns the tier floor it earns.

        Raises `ProgramRejected` for an unknown program, an unlisted subcommand, or a
        denied argv pattern. Deny is evaluated first and is not overridable.
        """
        rule = self.rule_for(name)
        if rule.path is None:
            raise ProgramRejected(
                f"programs.{name}.path", f"{name} is not installed on this machine"
            )
        self._check_shape(rule, args)

        for pattern in rule.deny:
            if _matches_deny(pattern, args):
                raise ProgramRejected(
                    f"programs.{name}.subcommands.deny",
                    f"{name} {' '.join(pattern)} is denied outright",
                )

        if rule.allow_args_matching:
            joined = " ".join(args)
            if any(fnmatch(joined, pat) for pat in rule.allow_args_matching):
                return Tier.T2
            raise ProgramRejected(
                f"programs.{name}.allow_args_matching",
                f"{name} {joined!r} matches no allowed argument pattern",
            )

        sub = args[0] if args else ""
        if sub in rule.confirm or sub in rule.allow:
            # `dev.execute` is T2 by policy regardless; the allowlist can raise the
            # floor but never lower it.
            return Tier.T2
        raise ProgramRejected(
            f"programs.{name}.subcommands",
            f"{name} {sub!r} is not an allowed subcommand "
            f"(allowed: {', '.join(sorted(rule.allow | rule.confirm)) or 'none'})",
        )

    def check_fixed(self, name: str, args: Sequence[str]) -> None:
        """Validate argv that ORACLE itself built for an intent-shaped tool.

        The subcommand grammar is not consulted — `git.commit` may commit, by
        construction — but the shape limits and the batch-argument rule still apply,
        because a *value* inside that argv (a commit message, a test filter) is still
        model-supplied.
        """
        self._check_shape(self.rule_for(name), args)

    @staticmethod
    def _check_shape(rule: ProgramRule, args: Sequence[str]) -> None:
        if len(args) > MAX_ARGS:
            raise ProgramRejected(
                f"programs.{rule.name}.max_args",
                f"{len(args)} arguments exceeds the cap of {MAX_ARGS}",
            )
        for a in args:
            if "\x00" in a:
                raise ProgramRejected(f"programs.{rule.name}.argv", "argument contains a NUL byte")
            if len(a) > MAX_ARG_LEN:
                raise ProgramRejected(
                    f"programs.{rule.name}.argv", f"argument exceeds {MAX_ARG_LEN} characters"
                )
            if rule.is_batch and (set(a) & _CMD_METACHARACTERS):
                # There is no correct escaping for these when the target is a batch
                # file, so the answer is refusal, not a cleverer quoting function.
                raise ProgramRejected(
                    f"programs.{rule.name}.batch_argv",
                    f"{rule.name} is a batch file; the argument {a!r} contains a character "
                    "cmd.exe would reinterpret",
                )


def _matches_deny(pattern: tuple[str, ...], args: Sequence[str]) -> bool:
    """`("push", "--force")` matches `push origin main --force`.

    The first token anchors to the subcommand; the remainder need only be present. A
    plain prefix match would miss `push origin main --force`, which is the spelling a
    real force-push actually takes.
    """
    if not pattern or not args or args[0] != pattern[0]:
        return False
    rest = set(args[1:])
    return all(tok in rest for tok in pattern[1:])


EMPTY_ALLOWLIST = ProgramAllowlist()

__all__ = [
    "EMPTY_ALLOWLIST",
    "MAX_ARGS",
    "MAX_ARG_LEN",
    "ProgramAllowlist",
    "ProgramRejected",
    "ProgramRule",
]
