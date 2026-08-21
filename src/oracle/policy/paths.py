"""Path canonicalisation — the foundation of the filesystem sandbox.

This is where filesystem sandboxes actually break, and Windows offers more ways to
break than POSIX. Implements the algorithm in docs/SECURITY.md#4-path-safety-windows-specific.

Every rule below is backed by a measurement on this machine, recorded in
logs/development/2026-08-21-oq04-windows-paths.md. The four that matter:

  * **Junctions are invisible to `is_symlink()`.** `Path.is_symlink()` and
    `os.path.islink()` both return False for a junction. Any "resolve only if it's a
    link" shortcut walks straight past one. Detection uses the reparse-point attribute.
  * **`realpath` does NOT strip an alternate data stream.** `normal.txt:hidden` survives
    resolution, so ADS must be rejected by inspection *before* resolving.
  * **Windows silently strips trailing dots and spaces.** `.env.` opens `.env`. A deny
    rule matched against the raw string misses it; matching must happen after resolution.
  * **8.3 short names are enabled on this volume** (`PROGRA~1` resolves), and `realpath`
    expands them — but only if you resolve before matching, never after.

`realpath` handles junctions, symlinks, 8.3 aliases, trailing dots and `..` correctly.
`abspath` does none of it and must never be substituted.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePath

FILE_ATTRIBUTE_REPARSE_POINT = 0x400

#: Rejected outright: they bypass normalisation or address devices rather than files.
_DEVICE_PREFIXES = ("\\\\", "//")
_WILDCARDS = frozenset("*?")
#: Env-var syntax must never be expanded from model-supplied text.
_ENV_RE = re.compile(r"[%$]")


class Reason(StrEnum):
    OK = "ok"
    EMPTY = "empty"
    NUL_BYTE = "nul_byte"
    WILDCARD = "wildcard"
    DEVICE_PATH = "device_path"
    ALTERNATE_DATA_STREAM = "alternate_data_stream"
    ENV_EXPANSION = "env_expansion"
    NOT_ABSOLUTE = "not_absolute"
    OUTSIDE_SCOPE = "outside_scope"
    DENIED = "denied"
    UNRESOLVABLE = "unresolvable"
    CHANGED_UNDER_US = "changed_under_us"


class PathRejected(Exception):
    def __init__(self, reason: Reason, detail: str) -> None:
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Scope:
    """A named region of the filesystem a capability may act in."""

    name: str
    root: Path
    writable: bool = False

    @property
    def key(self) -> str:
        return str(self.root).rstrip("\\/").lower()


@dataclass(frozen=True)
class ResolvedPath:
    """A path that has survived canonicalisation. Tools receive one of these, never a
    `str` — a bare string in a tool signature bypasses everything in this module."""

    original: str
    real: Path
    scope: Scope
    existed: bool

    @property
    def writable(self) -> bool:
        return self.scope.writable

    def __str__(self) -> str:
        return str(self.real)


def _reject_syntax(raw: str) -> None:
    """Checks that must run BEFORE resolution, because resolution won't save us.

    ORDER MATTERS. Device paths are checked before wildcards and env syntax, because
    `\\\\?\\C:\\...` legitimately contains `?` and `\\\\host\\C$\\...` contains `$`.
    Reporting those as "wildcard" would be a confusing and slightly wrong denial.
    """
    if not raw or not raw.strip():
        raise PathRejected(Reason.EMPTY, "empty path")
    if "\x00" in raw:
        raise PathRejected(Reason.NUL_BYTE, "path contains a NUL byte")

    normalised = raw.replace("/", "\\")
    if normalised.startswith(_DEVICE_PREFIXES):
        raise PathRejected(Reason.DEVICE_PATH, f"UNC or device path: {raw!r}")

    # MEASURED: realpath does NOT strip an ADS. `normal.txt:hidden` writes a hidden
    # stream to a file whose size never changes, so only an explicit check catches it.
    #
    # Done by slicing, not by regex: an earlier `^(?:[A-Za-z]:)?[^:]*:(.*)$` looked
    # right but the optional group let it backtrack and match the drive-letter colon
    # itself, so it rejected EVERY absolute Windows path. Caught by the suite.
    tail = normalised[2:] if len(normalised) > 1 and normalised[1] == ":" else normalised
    if ":" in tail:
        raise PathRejected(Reason.ALTERNATE_DATA_STREAM, f"alternate data stream: {raw!r}")

    if any(c in normalised for c in _WILDCARDS):
        raise PathRejected(Reason.WILDCARD, f"wildcards are not accepted here: {raw!r}")
    if _ENV_RE.search(normalised):
        # We never expand these; refusing is clearer than passing them through as
        # literals that something downstream may or may not expand.
        raise PathRejected(Reason.ENV_EXPANSION, f"path contains % or $: {raw!r}")


def _is_reparse_point(p: Path) -> bool:
    """MEASURED: `is_symlink()` is False for junctions. Use the attribute bit."""
    try:
        st = os.lstat(p)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT)


def contains(root: Path, candidate: Path) -> bool:
    """Case-insensitive containment on a resolved path.

    Uses path components, not string prefixes: a plain `startswith` would accept
    `C:\\Projects-evil` as inside `C:\\Projects`.
    """
    try:
        rp = PurePath(str(root).rstrip("\\/").lower())
        cp = PurePath(str(candidate).lower())
    except ValueError:  # pragma: no cover
        return False
    return cp == rp or rp in cp.parents


class PathResolver:
    def __init__(self, scopes: list[Scope], deny: list[str] | None = None) -> None:
        self._scopes = scopes
        #: Glob patterns evaluated against the RESOLVED path, never the raw one.
        self._deny = [d.replace("/", "\\").lower() for d in (deny or [])]

    def resolve(
        self, raw: str, *, cwd: Path | None = None, must_exist: bool = False
    ) -> ResolvedPath:
        _reject_syntax(raw)

        candidate = Path(raw)
        if not candidate.is_absolute():
            if cwd is None:
                raise PathRejected(
                    Reason.NOT_ABSOLUTE, f"relative path with no pinned cwd: {raw!r}"
                )
            candidate = Path(cwd) / candidate

        try:
            # realpath resolves junctions, symlinks, 8.3 aliases, trailing dots and '..'.
            # strict=False so we can resolve a not-yet-existing file inside a real dir.
            real = Path(os.path.realpath(candidate))
        except OSError as exc:
            raise PathRejected(Reason.UNRESOLVABLE, f"{raw!r}: {exc}") from exc

        # Re-run syntax checks on the RESOLVED path: a reparse point can point at a
        # device path or reintroduce a stream.
        _reject_syntax(str(real))

        existed = real.exists()
        if must_exist and not existed:
            raise PathRejected(Reason.UNRESOLVABLE, f"does not exist: {real}")

        # Deny wins over any allow, and is matched post-resolution so trailing-dot and
        # 8.3 tricks cannot slip past it.
        low = str(real).lower()
        for pattern in self._deny:
            if PurePath(low).match(pattern):
                raise PathRejected(Reason.DENIED, f"{real} matches deny rule {pattern!r}")

        # Longest matching root wins, so a nested read-only scope beats a broad rw one.
        best: Scope | None = None
        for scope in self._scopes:
            if contains(scope.root, real) and (best is None or len(scope.key) > len(best.key)):
                best = scope
        if best is None:
            raise PathRejected(Reason.OUTSIDE_SCOPE, f"{real} is not inside any allowed scope")

        return ResolvedPath(original=raw, real=real, scope=best, existed=existed)

    def recheck(self, resolved: ResolvedPath) -> None:
        """TOCTOU guard, called immediately before execution.

        Closes the window between "the user approved this" and "we ran it": a path can
        be swapped for a reparse point in between, and an approval must not survive
        that.
        """
        if _is_reparse_point(resolved.real):
            raise PathRejected(
                Reason.CHANGED_UNDER_US, f"{resolved.real} became a reparse point after approval"
            )
        try:
            again = Path(os.path.realpath(resolved.real))
        except OSError as exc:
            raise PathRejected(Reason.UNRESOLVABLE, str(exc)) from exc
        if str(again).lower() != str(resolved.real).lower():
            raise PathRejected(
                Reason.CHANGED_UNDER_US,
                f"{resolved.real} now resolves to {again}",
            )
