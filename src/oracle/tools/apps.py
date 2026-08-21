"""`app.launch` — hand something to the user, not to the agent.

**This is the one tool that runs in the parent process, and the reason is the Job
Object.**

Every other tool executes inside the toolhost, which lives in a job with
`KILL_ON_JOB_CLOSE` so that HALT can take down a process tree including grandchildren.
That containment is the point of ADR-0003 and the thing P3 was ordered around. But an
application the user asked for must *outlive* the tool call: HALT means "stop what you
are doing", not "close my editor with unsaved work in it", and the toolhost restarts on
every crash and timeout.

The alternatives were considered and rejected:

  * `CREATE_BREAKAWAY_FROM_JOB` needs `JOB_OBJECT_LIMIT_BREAKAWAY_OK` on the job — which
    would let *anything* the child spawns escape HALT. Trading the containment guarantee
    for the ability to open Explorer is not a trade.
  * Launching from the child and accepting that the app dies with the toolhost. That is
    a data-loss bug wearing a security hat.
  * Shelling out to `explorer.exe` so the shell re-parents it. That is ShellExecute by
    another name, cannot pass arguments, and widens what can be started to every file
    association on the machine.

So the launch happens here, in the parent, and is kept as narrow as it can be:

  * the executable is **pinned from `config/apps.yaml`**, never chosen by the model;
  * the environment is **constructed**, using the same allowlist as the toolhost, so
    the API key is absent rather than merely unused;
  * it is **detached**: no pipes, no console, and never waited on. The only thing kept
    is the process handle, so that a window ORACLE opened can be named — nothing about
    the launched process comes back into the runtime.

What crosses to it is an alias and at most one canonicalised path.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Annotated

from pydantic import Field

from oracle.logsink import get_logger
from oracle.policy.model import Capability, Tier
from oracle.tools.contract import ToolArgs, ToolContext, ToolResult, tool

log = get_logger(__name__)

ScopedPath = Annotated[str, Field(description="Absolute path inside an allowed scope")]

#: `DETACHED_PROCESS` gives it no console at all; `CREATE_NEW_PROCESS_GROUP` keeps a
#: Ctrl-C in ORACLE's console from reaching it. Together they mean the launched app is
#: not a child we manage — it is the user's window.
_DETACHED = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP

#: Launched windows, kept referenced on purpose.
#:
#: We never wait on these — the whole point is that they outlive the call — but dropping
#: the `Popen` would let it be finalised while the process is still running, which emits
#: a `ResourceWarning` from `__del__`. Holding the reference is the honest fix: we did
#: start these, and this is the record of it. Exited entries are pruned on each launch
#: so the list tracks what is actually open rather than growing forever.
_LAUNCHED: list[subprocess.Popen[bytes]] = []
MAX_TRACKED = 64


def _track(proc: subprocess.Popen[bytes]) -> None:
    _LAUNCHED[:] = [p for p in _LAUNCHED if p.poll() is None][-MAX_TRACKED:]
    _LAUNCHED.append(proc)


def launched_pids() -> list[int]:
    """Windows ORACLE opened that are still open. Read by `oracle.status`."""
    return [p.pid for p in _LAUNCHED if p.poll() is None]


class AppLaunchArgs(ToolArgs):
    #: An alias from `config/apps.yaml`. Never a path — see the module docstring.
    app: str
    #: Optional file or folder to open with it. Canonicalised and scope-checked before
    #: it gets here, and only accepted for aliases that declare `accepts_path`.
    path: ScopedPath | None = None


class AppLaunchResult(ToolResult):
    app: str
    description: str
    executable: str
    opened: str | None
    pid: int
    detached: bool


@tool(
    id="app.launch",
    summary="Open an application by name, optionally on a file or folder.",
    args=AppLaunchArgs,
    result=AppLaunchResult,
    capabilities={Capability.FS_READ, Capability.PROC_SPAWN},
    scopes={"projects", "notes", "scratch"},
    risk=Tier.T1,
    # Closing a window is the undo, and it is the user's to do. There is nothing for the
    # journal to record and nothing it could reverse without taking the window away
    # from someone who may be typing in it.
    reversible=False,
    intents={"run", "control"},
    side_effects="Opens an application window. It keeps running after ORACLE stops.",
    path_fields={"path"},
    app_field="app",
)
async def app_launch(*, ctx: ToolContext, args: AppLaunchArgs) -> AppLaunchResult:
    entry = ctx.app
    if entry is None:  # pragma: no cover - the executor always resolves it first
        raise ValueError("app.launch was invoked without a resolved catalogue entry")

    argv: list[str] = [str(entry.path), *entry.args]
    opened: str | None = None
    target: Path | None = ctx.resolved.get("path")
    if target is not None:
        if not entry.accepts_path:
            raise ValueError(f"{entry.alias} does not take a path argument")
        argv.append(str(target))
        opened = str(target)

    # Popen, not create_subprocess_exec: there is nothing to await. Awaiting a window
    # the user is going to keep open would hold the call for hours.
    proc = subprocess.Popen(  # noqa: S603 - argv list, pinned path, constructed env
        argv,
        env=_launch_env(),
        creationflags=_DETACHED,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
    )
    _track(proc)
    log.info("app.launched", alias=entry.alias, pid=proc.pid, path=opened)

    return AppLaunchResult(
        app=entry.alias,
        description=entry.description,
        executable=str(entry.path),
        opened=opened,
        pid=proc.pid,
        detached=True,
    )


def _launch_env() -> dict[str, str]:
    """The same constructed environment the toolhost child gets.

    Reused rather than reimplemented: two definitions of "what the environment may
    contain" is one definition too many, and the one that drifts is the one that leaks.
    """
    from oracle.toolhost.host import child_env

    return child_env()


APP_TOOLS = [app_launch]

__all__ = ["APP_TOOLS", "app_launch", "launched_pids"]
