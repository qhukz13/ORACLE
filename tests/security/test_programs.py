"""The program allowlist.

The claim being tested is narrow and load-bearing: **ORACLE cannot spawn a program
nobody wrote down, and cannot be redirected to a different binary after startup.**

Windows makes the second half non-obvious. The current directory participates in the
executable search order, so a `git.exe` dropped into a project folder is a real attack
rather than a theoretical one — which is why the pin happens once, at load.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from oracle.policy.engine import PolicyEngine, load_policy
from oracle.policy.model import Tier
from oracle.policy.programs import ProgramAllowlist, ProgramRejected

POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
programs:
  git:
    subcommands:
      allow:   [status, log]
      confirm: [commit, push]
      deny:
        - "push --force"
        - filter-branch
  faux:
    path: "{batch}"
    subcommands:
      allow: [go]
  absent:
    path: "C:/nowhere/definitely-not-here.exe"
tools:
  fs.read: {{ tier: T0, scopes: [projects] }}
"""


@pytest.fixture
def engine(tmp_path: Path) -> PolicyEngine:
    root = tmp_path / "Projects"
    root.mkdir()
    batch = tmp_path / "faux.cmd"
    batch.write_text("@echo off\n", encoding="utf-8")
    p = tmp_path / "policy.yaml"
    p.write_text(
        POLICY.format(root=root.as_posix(), batch=batch.as_posix()),
        encoding="utf-8",
    )
    return PolicyEngine(load_policy(p))


class TestNothingUnlistedRuns:
    def test_a_program_not_on_the_allowlist_is_refused_by_name(self, engine: PolicyEngine) -> None:
        with pytest.raises(ProgramRejected) as exc:
            engine.resolve_program("curl")
        # A denial that cannot say which rule refused it is a support ticket.
        assert exc.value.rule == "programs.allowlist"
        assert "curl" in exc.value.detail

    def test_an_allowlisted_program_that_is_not_installed_refuses(
        self, engine: PolicyEngine
    ) -> None:
        with pytest.raises(ProgramRejected) as exc:
            engine.resolve_program("absent")
        assert exc.value.rule == "programs.absent.path"

    def test_lockdown_can_spawn_nothing(self, tmp_path: Path) -> None:
        """Unloadable policy means no scopes AND no programs. A fail-open here would
        leave process execution wide open while the filesystem was locked down."""
        engine = PolicyEngine(load_policy(tmp_path / "does-not-exist.yaml"))
        assert engine.policy.read_only
        for name in ("git", "npm", "python", "cmd"):
            with pytest.raises(ProgramRejected):
                engine.resolve_program(name)


class TestSubcommandRules:
    def test_unlisted_subcommand_is_denied_by_default(self, engine: PolicyEngine) -> None:
        with pytest.raises(ProgramRejected) as exc:
            engine.check_program("git", ["gc"])
        assert exc.value.rule == "programs.git.subcommands"

    def test_allowed_subcommand_passes(self, engine: PolicyEngine) -> None:
        assert engine.check_program("git", ["status", "--porcelain"]) is Tier.T2

    def test_force_push_is_denied_even_when_the_flag_is_last(self, engine: PolicyEngine) -> None:
        """`push origin main --force` is how a force-push is actually spelled.

        A prefix match on `("push", "--force")` would sail straight past it, which is
        why the first token anchors and the rest need only be present.
        """
        with pytest.raises(ProgramRejected) as exc:
            engine.check_program("git", ["push", "origin", "main", "--force"])
        assert exc.value.rule == "programs.git.subcommands.deny"

    def test_deny_beats_confirm(self, engine: PolicyEngine) -> None:
        # `push` is on the confirm list; `push --force` is denied. Deny wins.
        assert engine.check_program("git", ["push", "origin", "main"]) is Tier.T2
        with pytest.raises(ProgramRejected):
            engine.check_program("git", ["push", "--force"])

    def test_a_denied_bare_subcommand_is_refused(self, engine: PolicyEngine) -> None:
        with pytest.raises(ProgramRejected) as exc:
            engine.check_program("git", ["filter-branch", "--all"])
        assert exc.value.rule == "programs.git.subcommands.deny"


class TestArgumentShape:
    def test_nul_byte_in_an_argument_is_refused(self, engine: PolicyEngine) -> None:
        with pytest.raises(ProgramRejected) as exc:
            engine.check_program("git", ["status", "a\x00b"])
        assert exc.value.rule == "programs.git.argv"

    def test_batch_targets_refuse_cmd_metacharacters(self, engine: PolicyEngine) -> None:
        """CVE-2024-3566: there is no correct argv escaping for a `.cmd` target, because
        cmd.exe re-parses the line after Python has quoted it. The answer is refusal,
        not a cleverer quoting function."""
        assert engine.check_program("faux", ["go", "plain"]) is Tier.T2
        with pytest.raises(ProgramRejected) as exc:
            engine.check_program("faux", ["go", 'x" & calc.exe'])
        assert exc.value.rule == "programs.faux.batch_argv"

    def test_shape_rules_also_apply_to_argv_oracle_builds_itself(
        self, engine: PolicyEngine
    ) -> None:
        """`git.commit` builds its own argv, but the commit MESSAGE came from the model.
        The subcommand grammar does not apply; the shape rules still do."""
        engine.check_fixed_program("git", ["a normal commit message"])
        with pytest.raises(ProgramRejected):
            engine.check_fixed_program("faux", ["message with a % in it"])

    def test_argument_count_is_bounded(self, engine: PolicyEngine) -> None:
        with pytest.raises(ProgramRejected) as exc:
            engine.check_program("git", ["status", *["x"] * 100])
        assert exc.value.rule == "programs.git.max_args"


class TestPinning:
    def test_path_is_pinned_at_load_and_survives_a_hostile_path(
        self, engine: PolicyEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The Windows attack this exists for: drop `git.exe` somewhere that comes
        earlier on PATH, and every later `which("git")` finds yours instead."""
        before = engine.resolve_program("faux")

        decoy_dir = tmp_path / "decoy"
        decoy_dir.mkdir()
        (decoy_dir / "faux.cmd").write_text("@echo pwned\n", encoding="utf-8")
        monkeypatch.setenv("PATH", str(decoy_dir) + os.pathsep + os.environ.get("PATH", ""))

        assert engine.resolve_program("faux") == before
        assert decoy_dir not in engine.resolve_program("faux").parents

    def test_an_empty_allowlist_names_nothing(self) -> None:
        empty = ProgramAllowlist.parse(None)
        assert empty.names == []
        with pytest.raises(ProgramRejected):
            empty.path_of("git")
