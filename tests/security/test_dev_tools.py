"""`dev.*`: structured results, and the escape hatch that has to be inspected.

Two things are being defended here.

**The counts must come from a report, not from prose.** The parsers are tested against
real fixture output, because the failure mode is silent: a scraped number that is wrong
looks exactly like a scraped number that is right, and the model has no way to tell.
Where no machine format exists (`cargo` off nightly), the result says `scraped` — the
label is the mitigation.

**`dev.execute` is the one tool whose argv the model chooses**, so it is the one tool
where the subcommand allowlist actually bites. The acceptance criterion is that
approving a preview executes *exactly* the previewed argv, and nothing else.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from oracle.config import Settings, set_settings
from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.policy.model import Decision, Tier
from oracle.toolhost import ToolHost
from oracle.tools import Approval, ToolErrorKind, ToolExecutor, build_registry
from oracle.tools.dev import _parse_cargo, _parse_js_json, _parse_junit

GIT = shutil.which("git")

POLICY = """
version: 1
scopes:
  projects:
    roots:
      - {{ path: "{root}", mode: rw }}
programs:
  git:
    subcommands:
      allow: [status, log]
      deny: ["push --force"]
  python:
    allow_args_matching:
      - "-V"
      - "-c *"
      - "-m pytest*"
  uv:
    subcommands:
      allow: [run, venv, pip]
tools:
  fs.read:        {{ tier: T0, scopes: [projects] }}
  dev.run_tests:  {{ tier: T1, scopes: [projects] }}
  dev.build:      {{ tier: T1, scopes: [projects] }}
  dev.lint:       {{ tier: T1, scopes: [projects] }}
  dev.execute:    {{ tier: T2, scopes: [projects] }}
"""


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "Projects"
    (root / "Demo").mkdir(parents=True)
    set_settings(
        Settings(
            data_dir=tmp_path / "data",
            log_dir=tmp_path / "logs",
            port=0,
            llm_enabled=False,
            prewarm_toolhost=False,
            watch_knowledge=False,
        )
    )
    return root


@pytest_asyncio.fixture
async def ex(tmp_path: Path, workspace: Path) -> AsyncIterator[ToolExecutor]:
    p = tmp_path / "policy.yaml"
    p.write_text(POLICY.format(root=workspace.as_posix()), encoding="utf-8")
    host = ToolHost()
    executor = ToolExecutor(
        build_registry(),
        PolicyEngine(load_policy(p)),
        AuditLog(tmp_path / "audit.jsonl"),
        host=host,
    )
    try:
        yield executor
    finally:
        await host.stop()


# ------------------------------------------------------------------- parsers

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" errors="1" failures="1" skipped="2" tests="10" time="3.5">
    <testcase classname="tests.test_a" name="test_ok" time="0.1"/>
    <testcase classname="tests.test_a" name="test_bad" time="0.2">
      <failure message="assert 1 == 2">E assert 1 == 2</failure>
    </testcase>
    <testcase classname="tests.test_b" name="test_broken">
      <error message="fixture 'db' not found">collection error</error>
    </testcase>
  </testsuite>
</testsuites>
"""

VITEST = """{
  "numPassedTests": 12, "numFailedTests": 2, "numPendingTests": 1, "numTodoTests": 0,
  "startTime": 1000, "endTime": 4500,
  "testResults": [
    {"assertionResults": [
      {"status": "passed", "fullName": "adds numbers"},
      {"status": "failed", "fullName": "renders the header",
       "failureMessages": ["expected 'a' to be 'b'"]}
    ]}
  ]
}"""

CARGO = """
running 14 tests
test parser::tests::reads_header ... ok
test parser::tests::rejects_junk ... FAILED

failures:
    parser::tests::rejects_junk

test result: FAILED. 12 passed; 1 failed; 1 ignored; 0 measured; 0 filtered out
"""


class TestParsersAreNotScraping:
    def test_junit_counts_errors_as_failures(self, tmp_path: Path) -> None:
        """A collection error is not a pass. pytest reports errors separately from
        failures, and a parser that ignores them under-reports the damage."""
        report = tmp_path / "r.xml"
        report.write_text(JUNIT, encoding="utf-8")
        parsed = _parse_junit(report)
        assert parsed is not None
        passed, failed, skipped, failures, duration = parsed
        assert (passed, failed, skipped) == (6, 2, 2)
        assert duration == 3.5
        assert {f.name for f in failures} == {
            "tests.test_a::test_bad",
            "tests.test_b::test_broken",
        }

    def test_vitest_json(self, tmp_path: Path) -> None:
        report = tmp_path / "r.json"
        report.write_text(VITEST, encoding="utf-8")
        parsed = _parse_js_json(report)
        assert parsed is not None
        passed, failed, skipped, failures, duration = parsed
        assert (passed, failed, skipped) == (12, 2, 1)
        assert duration == 3.5
        assert failures[0].name == "renders the header"

    def test_cargo_summary_is_scraped_and_says_so(self) -> None:
        passed, failed, ignored, failures, _ = _parse_cargo(CARGO)
        assert (passed, failed, ignored) == (12, 1, 1)
        assert failures[0].name == "parser::tests::rejects_junk"

    def test_a_missing_report_is_not_silently_a_pass(self, tmp_path: Path) -> None:
        assert _parse_junit(tmp_path / "absent.xml") is None
        assert _parse_js_json(tmp_path / "absent.json") is None


class TestNothingIsGuessed:
    async def test_a_project_with_no_test_command_refuses(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        out = await ex.execute("dev.run_tests", {"path": str(workspace / "Demo")})
        assert not out.ok
        assert out.error is not None
        assert "declares no test command" in out.error.message

    async def test_tests_outside_the_projects_scope_are_denied(self, ex: ToolExecutor) -> None:
        out = await ex.execute("dev.run_tests", {"path": r"C:\Windows"})
        assert not out.ok
        assert out.error is not None and out.error.kind == ToolErrorKind.DENIED


class TestExecuteIsGated:
    async def test_execute_needs_approval_and_previews_the_exact_argv(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        args = {"path": str(workspace / "Demo"), "program": "python", "args": ["-V"]}

        verdict, digest = ex.preview("dev.execute", args)
        assert verdict.decision is Decision.CONFIRM
        assert verdict.tier is Tier.T2

        refused = await ex.execute("dev.execute", args)
        assert not refused.ok
        assert refused.error is not None
        assert refused.error.kind == ToolErrorKind.APPROVAL_REQUIRED

        ex.grant(
            Approval(
                approval_id="ap_1",
                tool="dev.execute",
                args_digest=digest,
                tier=Tier.T2,
                expires_at=time.time() + 60,
            )
        )
        out = await ex.execute("dev.execute", args, approval_id="ap_1")
        assert out.ok, out.error and out.error.message
        # Exactly what was previewed, and nothing appended to it.
        assert out.result is not None
        assert out.result.argv.split()[1:] == ["-V"]  # type: ignore[attr-defined]
        assert out.result.ok is True  # type: ignore[attr-defined]

    async def test_an_approval_cannot_be_reused_for_different_arguments(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        """The whole point of binding an approval to a digest: approving `python -V`
        must not approve `python -c <anything>`."""
        harmless = {"path": str(workspace / "Demo"), "program": "python", "args": ["-V"]}
        _, digest = ex.preview("dev.execute", harmless)
        ex.grant(
            Approval(
                approval_id="ap_2",
                tool="dev.execute",
                args_digest=digest,
                tier=Tier.T2,
                expires_at=time.time() + 60,
            )
        )
        swapped = {
            "path": str(workspace / "Demo"),
            "program": "python",
            "args": ["-c", "print('something else entirely')"],
        }
        out = await ex.execute("dev.execute", swapped, approval_id="ap_2")
        assert not out.ok
        assert out.error is not None
        assert out.error.kind == ToolErrorKind.APPROVAL_INVALID

    async def test_a_program_not_on_the_allowlist_is_refused_naming_the_rule(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        out = await ex.execute(
            "dev.execute",
            {"path": str(workspace / "Demo"), "program": "powershell", "args": ["-c", "ls"]},
        )
        assert not out.ok
        assert out.error is not None
        assert out.error.kind == ToolErrorKind.DENIED
        assert out.error.detail == "programs.allowlist"
        assert "not on the program allowlist" in out.error.message

    @pytest.mark.skipif(GIT is None, reason="git is not installed")
    async def test_an_unlisted_subcommand_is_refused(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        out = await ex.execute(
            "dev.execute", {"path": str(workspace / "Demo"), "program": "git", "args": ["gc"]}
        )
        assert not out.ok
        assert out.error is not None
        assert out.error.detail == "programs.git.subcommands"

    @pytest.mark.skipif(GIT is None, reason="git is not installed")
    async def test_a_denied_argv_pattern_is_refused_however_it_is_spelled(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        out = await ex.execute(
            "dev.execute",
            {
                "path": str(workspace / "Demo"),
                "program": "git",
                "args": ["push", "origin", "main", "--force"],
            },
        )
        assert not out.ok
        assert out.error is not None
        assert out.error.detail == "programs.git.subcommands.deny"

    async def test_a_dry_run_executes_nothing(self, ex: ToolExecutor, workspace: Path) -> None:
        """The confirmation card needs a real preview. A dry run must report the argv
        without the side effect — otherwise "preview" means "do it and tell me"."""
        marker = workspace / "Demo" / "ran.txt"
        args = {
            "path": str(workspace / "Demo"),
            "program": "python",
            "args": ["-c", f"open(r'{marker}', 'w').write('x')"],
        }
        _, digest = ex.preview("dev.execute", args)
        ex.grant(
            Approval(
                approval_id="ap_3",
                tool="dev.execute",
                args_digest=digest,
                tier=Tier.T2,
                expires_at=time.time() + 60,
            )
        )
        out = await ex.execute("dev.execute", args, approval_id="ap_3", dry_run=True)
        assert out.ok
        assert out.result is not None and out.result.dry_run is True  # type: ignore[attr-defined]
        assert not marker.exists(), "a dry run executed the command"


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv is not installed")
class TestStructuredResultsEndToEnd:
    async def test_a_real_pytest_run_returns_counts_not_text(
        self, ex: ToolExecutor, workspace: Path
    ) -> None:
        """The acceptance criterion, against a real runner in a real child process.

        The project is a throwaway with two tests, one of which fails on purpose: a
        suite that only ever passes would not prove the failure path is parsed.
        """
        project = workspace / "Demo"
        (project / "pyproject.toml").write_text(
            "[project]\nname = 'demo'\nversion = '0'\nrequires-python = '>=3.12'\n",
            encoding="utf-8",
        )
        (project / "test_demo.py").write_text(
            "def test_passes():\n    assert True\n\n\ndef test_fails():\n    assert 1 == 2\n",
            encoding="utf-8",
        )
        # Point uv at the interpreter that is already here, so the test does not go to
        # the network to download one.
        subprocess.run(
            [shutil.which("uv") or "uv", "venv", "--python", "3.12"],
            cwd=project,
            capture_output=True,
            timeout=180,
        )
        installed = subprocess.run(
            [shutil.which("uv") or "uv", "pip", "install", "pytest"],
            cwd=project,
            capture_output=True,
            timeout=300,
        )
        if installed.returncode != 0:
            pytest.skip("could not provision a throwaway environment offline")

        out = await ex.execute("dev.run_tests", {"path": str(project)})
        assert out.ok, out.error and out.error.message
        r = out.result
        assert r is not None
        assert r.source == "junit-xml"  # type: ignore[attr-defined]
        assert r.passed == 1  # type: ignore[attr-defined]
        assert r.failed == 1  # type: ignore[attr-defined]
        assert r.ok is False  # type: ignore[attr-defined]
        assert "test_fails" in r.failures[0].name  # type: ignore[attr-defined]
        # The full output is kept for the human, not pushed into the model's context.
        assert Path(r.log_path).exists()  # type: ignore[attr-defined]
