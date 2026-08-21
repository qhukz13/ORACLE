"""Development actions: `dev.run_tests`, `dev.build`, `dev.lint`, `dev.execute`.

The interesting one is `dev.run_tests`, and what makes it interesting is rule 4 of
docs/TOOLS.md: **structured results, not scraped text.** "3 tests failed" is a fact the
model can reason about. A 40 KB wall of pytest output is a fact the model will
misread — and it costs the context window for the privilege.

So each runner is asked for machine-readable output where it has one:

| runner | how we ask                            | source     |
|--------|---------------------------------------|------------|
| pytest | `--junit-xml=<tmp>`                   | `junit-xml` |
| vitest | `--reporter=json --outputFile=<tmp>`  | `json`      |
| jest   | `--json --outputFile=<tmp>`           | `json`      |
| cargo  | nothing stable outside nightly        | `scraped`   |

`cargo` is the honest exception, and the result *says so* in `source`. A number scraped
out of prose and a number parsed from a report are not equally trustworthy, and hiding
which one you have is how a wrong count becomes a confident claim.

Which command to run is not guessed here — it comes from project detection
(`oracle.core.projects`), which classifies by marker file. A project with no declared
test script gets a clear refusal rather than an invented `npm test`.
"""

from __future__ import annotations

import json
import re
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field

from oracle.core.projects import ProjectInfo, Task, detect_project
from oracle.logsink import get_logger
from oracle.policy.model import Capability, Tier
from oracle.tools.contract import ToolArgs, ToolContext, ToolResult, tool
from oracle.tools.proc import Completed, ProcessTimeout, clip, run, write_blob

log = get_logger(__name__)

ScopedPath = Annotated[str, Field(description="Absolute path to the project directory")]

#: Every runner `dev.*` might need. All are pinned by the parent when installed; the
#: handler asks for the one the detected project actually uses.
RUNNERS = {"uv", "python", "npm", "cargo"}

TEST_TIMEOUT_S = 600
BUILD_TIMEOUT_S = 900
MAX_FAILURES_REPORTED = 20

#: Deterministic output, no colour codes, no interactive progress. Test runners that
#: detect a TTY produce ANSI escapes that make the log unreadable and the parse fragile.
DEV_ENV = {
    "CI": "1",
    "NO_COLOR": "1",
    "FORCE_COLOR": "0",
    "PYTHONUNBUFFERED": "1",
    "PYTHONIOENCODING": "utf-8",
    "npm_config_yes": "true",
}


class Failure(ToolResult):
    name: str
    message: str


class TestRunResult(ToolResult):
    project: str
    runner: str
    command: str
    passed: int
    failed: int
    skipped: int
    total: int
    duration_s: float
    failures: list[Failure]
    exit_code: int
    #: How the numbers were obtained. `scraped` means "parsed out of human output" and
    #: should be trusted accordingly.
    source: Literal["junit-xml", "json", "scraped", "exit-code"]
    log_path: str
    ok: bool


def _info(ctx: ToolContext) -> ProjectInfo:
    return detect_project(ctx.resolved["path"])


def _pick(ctx: ToolContext, tasks: tuple[Task, ...], what: str, info: ProjectInfo) -> Task:
    """The first task whose program is actually on this machine.

    Detection returns alternatives in preference order — `uv run pytest` before
    `python -m pytest` — because which one is *correct* depends on what is installed,
    and only this side knows. Root tasks come before nested ones: a sub-package's suite
    is a component of the project, not the project.
    """
    if not tasks:
        kinds = ", ".join(info.kinds)
        raise ValueError(
            f"{info.name} declares no {what} command (detected: {kinds}). "
            f"Nothing is guessed here — an invented command fails confusingly."
        )
    for task in tasks:
        if task.program in ctx.programs:
            return task
    wanted = ", ".join(sorted({t.program for t in tasks}))
    # Both causes are named because they need different fixes: install the program, or
    # add it to the allowlist. "Not available" alone leaves the user guessing.
    raise ValueError(
        f"{info.name} needs one of [{wanted}] to {what}, and none is usable here — "
        f"either it is not installed, or config/policy.yaml does not allow it"
    )


def _program(ctx: ToolContext, task: Task) -> Path:
    try:
        return ctx.program(task.program)
    except RuntimeError as exc:
        raise ValueError(
            f"{task.program} is required to {task.kind} this project but is not "
            f"available on this machine"
        ) from exc


async def _run_task(
    ctx: ToolContext, task: Task, extra: list[str], timeout_s: int
) -> tuple[Completed, Path]:
    cwd = ctx.resolved["path"] / task.subdir if task.subdir else ctx.resolved["path"]
    program = _program(ctx, task)
    try:
        completed = await run(
            program, [*task.args, *extra], cwd=cwd, timeout_s=timeout_s, env=DEV_ENV
        )
    except ProcessTimeout as exc:
        raise ValueError(str(exc)) from exc
    return completed, cwd


# ------------------------------------------------------------------- parsers


def _parse_junit(path: Path) -> tuple[int, int, int, list[Failure], float] | None:
    """pytest's `--junit-xml`. Counts come from attributes, not from a summary line.

    `xml.etree` is used rather than `defusedxml` because the input is a file this
    process just told pytest to write, in a directory only this process knows about —
    it is not attacker-supplied XML. Anything read from a project would need the
    hardened parser.
    """
    try:
        root = ET.parse(path).getroot()  # noqa: S314 - locally generated, see docstring
    except (OSError, ET.ParseError) as exc:
        log.warning("dev.junit_unreadable", path=str(path), error=str(exc))
        return None

    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    total = failed = skipped = 0
    duration = 0.0
    failures: list[Failure] = []
    for suite in suites:
        total += int(suite.get("tests", 0))
        failed += int(suite.get("failures", 0)) + int(suite.get("errors", 0))
        skipped += int(suite.get("skipped", 0))
        duration += float(suite.get("time", 0.0))
        for case in suite.iter("testcase"):
            for bad in (*case.findall("failure"), *case.findall("error")):
                name = f"{case.get('classname', '')}::{case.get('name', '')}".strip(":")
                failures.append(
                    Failure(name=name, message=(bad.get("message") or bad.text or "")[:500])
                )
    return total - failed - skipped, failed, skipped, failures[:MAX_FAILURES_REPORTED], duration


def _parse_js_json(path: Path) -> tuple[int, int, int, list[Failure], float] | None:
    """vitest and jest both write a report with the same shape in the parts we use."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        log.warning("dev.json_report_unreadable", path=str(path), error=str(exc))
        return None
    if not isinstance(data, dict):
        return None

    passed = int(data.get("numPassedTests", 0))
    failed = int(data.get("numFailedTests", 0))
    skipped = int(data.get("numPendingTests", 0)) + int(data.get("numTodoTests", 0))
    duration = 0.0
    start, end = data.get("startTime"), data.get("endTime")
    if isinstance(start, int | float) and isinstance(end, int | float):
        duration = max(0.0, (float(end) - float(start)) / 1000)

    failures: list[Failure] = []
    for suite in data.get("testResults") or []:
        if not isinstance(suite, dict):
            continue
        for case in suite.get("assertionResults") or []:
            if isinstance(case, dict) and case.get("status") == "failed":
                messages = case.get("failureMessages") or []
                failures.append(
                    Failure(
                        name=str(case.get("fullName") or case.get("title") or "?"),
                        message=str(messages[0] if messages else "")[:500],
                    )
                )
    return passed, failed, skipped, failures[:MAX_FAILURES_REPORTED], duration


_CARGO_SUMMARY = re.compile(r"test result:\s+\w+\.\s+(\d+) passed;\s+(\d+) failed;\s+(\d+) ignored")
_CARGO_FAILURE = re.compile(r"^\s{4}(\S+)$", re.MULTILINE)


def _parse_cargo(output: str) -> tuple[int, int, int, list[Failure], float]:
    """The honest fallback. `cargo test` has no stable machine format off nightly, so
    this reads the summary lines — and the result is labelled `scraped` so nobody
    mistakes it for a parsed report."""
    passed = failed = ignored = 0
    for match in _CARGO_SUMMARY.finditer(output):
        passed += int(match.group(1))
        failed += int(match.group(2))
        ignored += int(match.group(3))

    failures: list[Failure] = []
    section = output.split("failures:", 1)
    if failed and len(section) > 1:
        for name in _CARGO_FAILURE.findall(section[-1])[:MAX_FAILURES_REPORTED]:
            failures.append(Failure(name=name, message="see the log for the assertion"))
    return passed, failed, ignored, failures, 0.0


# -------------------------------------------------------------- dev.run_tests


class DevTestArgs(ToolArgs):
    path: ScopedPath
    #: Passed to the runner's own filter flag. Never concatenated into a command line.
    filter: str = ""


@tool(
    id="dev.run_tests",
    summary="Run the project's tests and return pass/fail counts and the failures.",
    args=DevTestArgs,
    result=TestRunResult,
    capabilities={Capability.FS_READ, Capability.PROC_SPAWN},
    scopes={"projects"},
    risk=Tier.T1,
    reversible=True,
    undo="nothing to undo; running tests does not change the project",
    timeout_s=TEST_TIMEOUT_S + 30,
    intents={"run", "investigate", "status"},
    side_effects="Runs the project's test suite. Test suites can write files of their own.",
    path_fields={"path"},
    programs=RUNNERS,
)
async def dev_run_tests(*, ctx: ToolContext, args: DevTestArgs) -> TestRunResult:
    info = _info(ctx)
    task = _pick(ctx, info.test, "test", info)

    # A report file the runner writes and we read. In TEMP, not in the project: a tool
    # that litters a repository with its own artefacts is a tool you stop trusting.
    fd, report_name = tempfile.mkstemp(prefix="oracle-tests-", suffix=".report")
    report = Path(report_name)
    import os

    os.close(fd)

    extra: list[str] = []
    source: Literal["junit-xml", "json", "scraped", "exit-code"] = "exit-code"
    runner = task.program

    if task.program in ("uv", "python"):
        runner = "pytest"
        source = "junit-xml"
        extra += [f"--junit-xml={report}"]
        if args.filter:
            extra += ["-k", args.filter]
    elif task.program == "npm":
        runner = _js_runner(info, task)
        source = "json"
        # `--` separates npm's own arguments from the script's. Without it npm eats them.
        extra += ["--"]
        extra += (
            ["--reporter=json", f"--outputFile={report}"]
            if runner == "vitest"
            else ["--json", f"--outputFile={report}"]
        )
        if args.filter:
            extra += ["-t", args.filter]
    elif task.program == "cargo":
        runner = "cargo"
        source = "scraped"
        if args.filter:
            extra += [args.filter]

    completed, _ = await _run_task(ctx, task, extra, TEST_TIMEOUT_S)
    log_path = write_blob(f"{info.name}-tests", completed.combined)

    parsed: tuple[int, int, int, list[Failure], float] | None = None
    if source == "junit-xml":
        parsed = _parse_junit(report)
    elif source == "json":
        parsed = _parse_js_json(report)
    elif source == "scraped":
        parsed = _parse_cargo(completed.combined)
    report.unlink(missing_ok=True)

    if parsed is None:
        # The runner did not produce the report it was asked for. Say so, and fall back
        # to the one fact that is still true: the exit code.
        log.warning("dev.report_missing", runner=runner, exit_code=completed.returncode)
        passed = failed = skipped = 0
        failures: list[Failure] = [
            Failure(
                name="(no machine-readable report)",
                message=(
                    f"{runner} did not write the report it was asked for; only the exit "
                    f"code is known. The full output is in {log_path}."
                ),
            )
        ]
        duration = completed.duration_ms / 1000
        source = "exit-code"
        failed = 0 if completed.ok else 1
    else:
        passed, failed, skipped, failures, duration = parsed
        duration = duration or completed.duration_ms / 1000

    return TestRunResult(
        project=info.name,
        runner=runner,
        command=completed.argv_display(),
        passed=passed,
        failed=failed,
        skipped=skipped,
        total=passed + failed + skipped,
        duration_s=round(duration, 2),
        failures=failures,
        exit_code=completed.returncode,
        source=source,
        log_path=log_path,
        # The exit code is the arbiter, not the parsed counts: a collection error can
        # produce zero failures and a non-zero exit, and calling that a pass would be
        # the single most damaging thing this tool could do.
        ok=completed.ok,
    )


def _js_runner(info: ProjectInfo, task: Task) -> str:
    """vitest and jest take different flags for the same thing."""
    pkg = info.root / task.subdir / "package.json" if task.subdir else info.root / "package.json"
    try:
        text = pkg.read_text(encoding="utf-8")
    except OSError:
        return "vitest"
    return "jest" if "jest" in text and "vitest" not in text else "vitest"


# ------------------------------------------------------------ dev.build / lint


class DevBuildArgs(ToolArgs):
    path: ScopedPath


class CommandResult(ToolResult):
    project: str
    command: str
    exit_code: int
    ok: bool
    duration_s: float
    #: First problems only. The whole output is in the blob.
    diagnostics: list[str]
    output: str
    truncated: bool
    log_path: str


def _diagnostics(text: str, limit: int = 20) -> list[str]:
    """Lines that look like a compiler or linter complaint.

    Deliberately a heuristic over a parser: every toolchain formats these differently,
    and the full log is one field away. This is a shortcut for the reader, not the
    result's source of truth — which is the exit code.
    """
    wanted = ("error", "warning", "failed", "cannot find", "ошибка")
    out: list[str] = []
    for line in text.splitlines():
        low = line.lower()
        if any(w in low for w in wanted) and line.strip():
            out.append(line.strip()[:300])
        if len(out) >= limit:
            break
    return out


def _command_result(info: ProjectInfo, completed: Completed, blob_tag: str) -> CommandResult:
    text, clipped = clip(completed.combined.strip())
    return CommandResult(
        project=info.name,
        command=completed.argv_display(),
        exit_code=completed.returncode,
        ok=completed.ok,
        duration_s=round(completed.duration_ms / 1000, 2),
        diagnostics=_diagnostics(completed.combined),
        output=text,
        truncated=clipped or completed.truncated,
        log_path=write_blob(f"{info.name}-{blob_tag}", completed.combined),
    )


@tool(
    id="dev.build",
    summary="Build the project using its own declared build command.",
    args=DevBuildArgs,
    result=CommandResult,
    # NOT fs.write: a build writes into its own output directory, and this contract
    # cannot name which files. `proc.spawn` is the capability that covers "the program
    # this starts may write inside the project" — see Capability.FS_WRITE. Declaring
    # fs.write here would promise a backup that nothing could take.
    capabilities={Capability.FS_READ, Capability.PROC_SPAWN},
    scopes={"projects"},
    risk=Tier.T1,
    # Build output is derived, not authored: the way back is to build again. There is
    # no previous version worth putting in the trash, so there is no undo to declare.
    reversible=False,
    timeout_s=BUILD_TIMEOUT_S + 30,
    intents={"run"},
    side_effects="Writes build output into the project's own output directory.",
    path_fields={"path"},
    programs=RUNNERS,
)
async def dev_build(*, ctx: ToolContext, args: DevBuildArgs) -> CommandResult:
    info = _info(ctx)
    task = _pick(ctx, info.build, "build", info)
    completed, _ = await _run_task(ctx, task, [], BUILD_TIMEOUT_S)
    return _command_result(info, completed, "build")


@tool(
    id="dev.lint",
    summary="Run the project's linter or type checker.",
    args=DevBuildArgs,
    result=CommandResult,
    capabilities={Capability.FS_READ, Capability.PROC_SPAWN},
    scopes={"projects"},
    risk=Tier.T1,
    reversible=True,
    undo="nothing to undo; linting does not change the project",
    timeout_s=BUILD_TIMEOUT_S + 30,
    intents={"run", "investigate"},
    side_effects="Reads the project and reports problems. Does not modify files.",
    path_fields={"path"},
    programs=RUNNERS,
)
async def dev_lint(*, ctx: ToolContext, args: DevBuildArgs) -> CommandResult:
    info = _info(ctx)
    task = _pick(ctx, info.lint, "lint", info)
    completed, _ = await _run_task(ctx, task, [], BUILD_TIMEOUT_S)
    return _command_result(info, completed, "lint")


# ------------------------------------------------------------------ dev.execute


class DevExecuteArgs(ToolArgs):
    path: ScopedPath
    #: An allowlist KEY, never a path. The model cannot name an executable.
    program: str
    args: list[str] = Field(default_factory=list)


class ExecuteResult(ToolResult):
    project: str
    #: Exactly what ran — or, in a dry run, exactly what would. The confirmation card
    #: shows this string, and the executed argv is built from the same arguments the
    #: approval was bound to.
    argv: str
    dry_run: bool
    exit_code: int
    ok: bool
    duration_s: float
    output: str
    truncated: bool
    log_path: str


@tool(
    id="dev.execute",
    summary="Run an allowlisted program with explicit arguments in a project.",
    args=DevExecuteArgs,
    result=ExecuteResult,
    # As with dev.build: what the spawned program writes is bounded by the scope, not
    # nameable by this contract.
    capabilities={Capability.FS_READ, Capability.PROC_SPAWN},
    scopes={"projects"},
    # The gated escape hatch (docs/TOOLS.md). T2 always: what it does is by definition
    # not something the contract can promise, so a human sees it first.
    risk=Tier.T2,
    reversible=False,
    dry_run=True,
    timeout_s=BUILD_TIMEOUT_S + 30,
    intents={"run"},
    side_effects="Runs a program. What it does is bounded by the allowlist, not by this contract.",
    path_fields={"path"},
    program_field="program",
)
async def dev_execute(*, ctx: ToolContext, args: DevExecuteArgs) -> ExecuteResult:
    info = _info(ctx)
    program = ctx.program(args.program)
    argv = [args.program, *args.args]

    if ctx.dry_run:
        return ExecuteResult(
            project=info.name,
            argv=" ".join(argv),
            dry_run=True,
            exit_code=0,
            ok=True,
            duration_s=0.0,
            output="(dry run — nothing was executed)",
            truncated=False,
            log_path="",
        )

    try:
        completed = await run(
            program,
            list(args.args),
            cwd=ctx.resolved["path"],
            timeout_s=BUILD_TIMEOUT_S,
            env=DEV_ENV,
        )
    except ProcessTimeout as exc:
        raise ValueError(str(exc)) from exc

    text, clipped = clip(completed.combined.strip())
    return ExecuteResult(
        project=info.name,
        argv=completed.argv_display(),
        dry_run=False,
        exit_code=completed.returncode,
        ok=completed.ok,
        duration_s=round(completed.duration_ms / 1000, 2),
        output=text,
        truncated=clipped or completed.truncated,
        log_path=write_blob(f"{info.name}-execute", completed.combined),
    )


DEV_TOOLS = [dev_run_tests, dev_build, dev_lint, dev_execute]
