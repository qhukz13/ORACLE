"""The shell ban, checked against the source itself.

`shell=True` is banned repo-wide (docs/SECURITY.md#4b, AGENTS.md hard rules). Ruff
enforces it at lint time via S602/S605 — but a lint rule is one `# noqa` away from
being advisory, and the whole tool-contract argument rests on this. So the ban is also
a test, over the actual files, where a suppression comment cannot hide it.

The related invariant is checked here too: **every program ORACLE spawns comes from the
allowlist.** A `subprocess` call built from a string literal, or from `shutil.which` at
call time, would bypass the pin that makes `git.exe`-in-a-project-folder a non-attack.
"""

from __future__ import annotations

import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src" / "oracle"

#: The string appears legitimately in prose explaining why it is banned. Only code
#: matters, so comment and docstring lines are excluded before matching.
_BANNED = (
    ("shell=True", re.compile(r"shell\s*=\s*True")),
    ("os.system", re.compile(r"\bos\.system\s*\(")),
    ("os.popen", re.compile(r"\bos\.popen\s*\(")),
    ("subprocess.getoutput", re.compile(r"\bsubprocess\.get(status)?output\s*\(")),
)


def _code_lines(path: Path) -> list[tuple[int, str]]:
    """Lines that are code, with block comments and docstrings dropped.

    Crude on purpose: a triple-quote toggle is enough to keep prose out, and anything
    fancier would be a parser with its own bugs sitting in a security test.
    """
    out: list[tuple[int, str]] = []
    in_doc = False
    for number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        ticks = line.count('"""') + line.count("'''")
        if in_doc:
            if ticks % 2:
                in_doc = False
            continue
        if ticks % 2:
            in_doc = True
            continue
        # An even count on one line is a complete string on that line — a one-line
        # docstring, which is prose and must not match.
        if ticks and line.startswith(('"""', "'''", 'r"""', "r'''")):
            continue
        if line.startswith("#"):
            continue
        out.append((number, raw.split("  #")[0]))
    return out


def _sources() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


class TestNoShell:
    def test_no_source_file_invokes_a_shell(self) -> None:
        offences: list[str] = []
        for path in _sources():
            for number, line in _code_lines(path):
                for name, pattern in _BANNED:
                    if pattern.search(line):
                        offences.append(f"{path.relative_to(SRC)}:{number} uses {name}")
        assert not offences, "a shell is never invoked:\n" + "\n".join(offences)

    def test_the_ban_is_not_silently_suppressed(self) -> None:
        """A `# noqa: S602` would turn the lint rule off without turning the risk off."""
        suppressed = [
            f"{path.relative_to(SRC)}:{number}"
            for path in _sources()
            for number, line in _code_lines(path)
            if re.search(r"noqa:.*\bS(602|604|605|606)\b", line)
        ]
        assert not suppressed, f"shell lint rules suppressed at: {suppressed}"

    def test_this_test_can_actually_fail(self, tmp_path: Path) -> None:
        """A guard that cannot fire is decoration. Prove the matcher works before
        trusting an empty result from it."""
        probe = tmp_path / "probe.py"
        probe.write_text(
            '"""A docstring mentioning shell=True, which must NOT match."""\n'
            "# a comment mentioning os.system, which must NOT match\n"
            "import subprocess\n"
            'subprocess.run("dir", shell=True)\n',
            encoding="utf-8",
        )
        hits = [
            (number, name)
            for number, line in _code_lines(probe)
            for name, pattern in _BANNED
            if pattern.search(line)
        ]
        assert hits == [(4, "shell=True")]


class TestProgramsComeFromTheAllowlist:
    def test_no_tool_resolves_a_program_at_call_time(self) -> None:
        """`shutil.which` inside a handler would defeat the pin entirely: PATH is
        attacker-influenceable and on Windows the current directory participates in the
        executable search order."""
        offenders: list[str] = []
        for path in _sources():
            if path.parent.name != "tools":
                continue
            for number, line in _code_lines(path):
                if re.search(r"\bshutil\.which\s*\(", line):
                    offenders.append(f"{path.relative_to(SRC)}:{number}")
        assert not offenders, f"a tool resolved a program via PATH at: {offenders}"

    def test_every_spawning_tool_declares_where_its_program_comes_from(self) -> None:
        """The registry enforces this at boot; asserting it here means a regression
        shows up as a named test failure rather than as a startup crash."""
        from oracle.policy.model import Capability
        from oracle.tools import build_registry

        for contract in build_registry().all():
            if Capability.PROC_SPAWN in contract.capabilities:
                assert contract.programs or contract.program_field or contract.app_field, (
                    f"{contract.id} spawns but names no allowlist entry"
                )
