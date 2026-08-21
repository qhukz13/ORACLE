"""`term.*` — a real shell, in the toolhost, with the agent as a guest in it.

The PTY lives in the **toolhost child**, deliberately, and that has two consequences
worth stating plainly:

  * a runaway shell dies with HALT, because it is inside the Job Object. That is the
    correct behaviour for a shell — unlike `app.launch`, nobody has unsaved work in a
    `npm install` that is spinning;
  * if the toolhost restarts, sessions are lost. Honest and documented: a session is
    live state in a process that is allowed to be killed, not a durable object.

**`term.write` is T2 and confirmed every single time.** Typing into a live shell is
full user privilege with no scope — an allowlist cannot inspect what a shell will do
with a line of text, and there is no undo for it. That is not paranoia, it is the one
place where the entire tool-contract argument does not apply, so the human decides
each time (docs/SECURITY.md#4b).

## What the spike settled (OQ-09, 2026-08-21)

- `pywinpty` 3.0.5 has a Python 3.12 wheel. It works.
- **Cyrillic needs no `chcp`.** ConPTY normalises to UTF-8 regardless of the legacy
  console code page, so the mojibake this question existed to worry about does not
  happen on this Russian-locale machine.
- **Resize mid-stream is safe**; the session survives and keeps streaming.
- **Writing before the shell is ready is silently swallowed.** This is the trap. A
  fixed sleep is a coin flip — 1.5 s failed and 2.5 s worked, run to run. Waiting for
  first output followed by a quiet gap was 8/8 at ~0.33 s, so readiness is a *measured
  condition*, never a sleep.
"""

from __future__ import annotations

import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from oracle.logsink import get_logger
from oracle.policy.model import Capability, Tier
from oracle.tools.contract import ToolArgs, ToolContext, ToolResult, tool

log = get_logger(__name__)

ScopedPath = Annotated[str, Field(description="Absolute path to open the shell in")]

#: Ring buffer per session. Enough to hold a long build's tail, small enough that a
#: forgotten session cannot eat memory.
MAX_BUFFER_CHARS = 256 * 1024
MAX_SESSIONS = 8
#: Never hand more than this to the model in one read, whatever the buffer holds.
MAX_READ_CHARS = 16_000

#: How long the pump waits when the shell has nothing to say. Short, because the cost
#: of being late is lost output, not just latency.
POLL_IDLE_S = 0.005

READY_QUIET_S = 0.3
READY_TIMEOUT_S = 10.0

#: CSI sequences, OSC strings (which cmd.exe uses for the window title, terminated by
#: BEL or ST) and lone escapes. Stripped for the model; the UI gets the raw stream.
_ANSI = re.compile(
    r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)"  # OSC ... BEL / ST
    r"|\x1b\[[0-9;?]*[ -/]*[@-~]"  # CSI
    r"|\x1b[@-Z\\-_]"  # two-character escapes
)


def strip_ansi(text: str) -> str:
    return _ANSI.sub("", text).replace("\r\n", "\n").replace("\r", "\n")


class TerminalError(Exception):
    pass


@dataclass
class Session:
    """One PTY and the thread that keeps it drained.

    A reader thread is not optional: if nobody reads the pseudoconsole, the shell
    eventually blocks on its own output, and a `npm install` that stalls because ORACLE
    was not looking would be indistinguishable from a hung install.
    """

    id: str
    cwd: Path
    #: Named `shell_path`, not `shell`: a keyword argument called `shell` is what
    #: `subprocess` uses to mean "run this through cmd.exe", and the lint rule that
    #: bans that cannot tell the two apart. This is a path, so it says so.
    shell_path: str
    pty: Any
    buffer: deque[str] = field(default_factory=deque)
    lock: threading.Lock = field(default_factory=threading.Lock)
    reader: threading.Thread | None = None
    closed: bool = False
    opened_at: float = field(default_factory=time.time)
    #: Total characters ever produced, so a caller can tell "nothing new" from
    #: "buffer was trimmed".
    produced: int = 0
    #: Characters discarded because the ring filled before anyone read it. Reported on
    #: every read: a bounded scrollback is correct, but dropping *silently* would let a
    #: reader conclude that something near the start never appeared.
    dropped: int = 0
    #: Running total of what is buffered.
    #:
    #: MEASURED, and the reason this is a field rather than a `sum()`: recomputing the
    #: size on every append is O(n) per chunk and therefore O(n²) over a burst. A
    #: 2000-line flood arrives as thousands of small chunks, the pump thread fell
    #: further behind on each one, and ConPTY's own buffer overran — 558 of 2000 lines
    #: were lost with the ring reporting **zero** drops, because the data never reached
    #: us. Reading the same burst at a steady 5 ms loses nothing, which is what proved
    #: the fault was here and not in ConPTY.
    buffered: int = 0

    def append(self, chunk: str) -> None:
        with self.lock:
            self.buffer.append(chunk)
            self.produced += len(chunk)
            self.buffered += len(chunk)
            while self.buffered > MAX_BUFFER_CHARS and len(self.buffer) > 1:
                lost = self.buffer.popleft()
                self.buffered -= len(lost)
                self.dropped += len(lost)

    def take(self, limit: int | None = None) -> tuple[str, bool]:
        """Up to `limit` characters from the FRONT, leaving the rest buffered.

        MEASURED, and the bug this signature exists to prevent: the first version
        emptied the whole buffer and then returned only its last `MAX_READ_CHARS`. A
        2000-line burst read every 50 ms therefore lost 429 lines — always the OLDEST
        ones, always silently, with the ring reporting zero drops because the ring had
        not trimmed anything. The data was destroyed on the way out, not on the way in.

        Oldest-first with the remainder kept is also simply what a terminal does.
        """
        with self.lock:
            text = "".join(self.buffer)
            self.buffer.clear()
            if limit is None or len(text) <= limit:
                self.buffered = 0
                return text, False
            head, tail = text[:limit], text[limit:]
            self.buffer.append(tail)
            self.buffered = len(tail)
            return head, True

    @property
    def alive(self) -> bool:
        try:
            return not self.closed and bool(self.pty.isalive())
        except Exception:  # pragma: no cover - the PTY is gone, which is the answer
            return False


_SESSIONS: dict[str, Session] = {}


def _pump(session: Session) -> None:
    """Drain the PTY forever. Runs on a daemon thread, one per session."""
    while not session.closed:
        try:
            data = session.pty.read(blocking=False)
        except Exception:
            break
        if data:
            session.append(data)
        else:
            if not session.alive:
                break
            # Idle poll only. While output is flowing this loop never sleeps, which is
            # what keeps ConPTY's own buffer from overrunning during a build.
            time.sleep(POLL_IDLE_S)
    session.closed = True


def _wait_ready(session: Session, quiet: float = READY_QUIET_S) -> None:
    """Block until the shell has spoken and then paused.

    MEASURED (OQ-09): a fixed sleep is a coin flip — input written before the shell is
    reading is swallowed with no error anywhere. Quiescence after first output was
    reliable across every run.
    """
    started = time.time()
    last_seen = 0
    last_change = None
    while time.time() - started < READY_TIMEOUT_S:
        current = session.produced
        if current != last_seen:
            last_seen, last_change = current, time.time()
        elif last_change is not None and time.time() - last_change >= quiet:
            return
        time.sleep(POLL_IDLE_S)
    log.warning("term.ready_timeout", session=session.id)


def _get(session_id: str) -> Session:
    session = _SESSIONS.get(session_id)
    if session is None:
        raise TerminalError(f"no terminal session {session_id!r}")
    if not session.alive:
        raise TerminalError(f"terminal session {session_id} has exited")
    return session


# ------------------------------------------------------------------- term.open


class TermOpenArgs(ToolArgs):
    path: ScopedPath
    cols: int = 100
    rows: int = 30


class TermOpenResult(ToolResult):
    session_id: str
    shell: str
    cwd: str
    banner: str
    cols: int
    rows: int


@tool(
    id="term.open",
    summary="Open a shell session in a project directory and return its id.",
    args=TermOpenArgs,
    result=TermOpenResult,
    capabilities={Capability.FS_READ, Capability.PROC_SPAWN},
    scopes={"projects", "notes", "scratch"},
    risk=Tier.T1,
    reversible=False,
    intents={"run", "control"},
    side_effects="Starts a shell. It dies with the tool host, and with HALT.",
    path_fields={"path"},
    programs={"cmd"},
)
async def term_open(*, ctx: ToolContext, args: TermOpenArgs) -> TermOpenResult:
    try:
        import winpty  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - dependency is declared
        raise TerminalError("pywinpty is not installed, so ORACLE cannot open a terminal") from exc

    live = [s for s in _SESSIONS.values() if s.alive]
    if len(live) >= MAX_SESSIONS:
        # A cap, not a queue. Silently opening a ninth shell is how a machine ends up
        # with forty orphaned cmd.exe processes nobody asked for.
        raise TerminalError(f"{MAX_SESSIONS} terminal sessions are already open; close one first")

    cwd: Path = ctx.resolved["path"]
    shell = str(ctx.program("cmd"))
    pty = winpty.PTY(cols=max(20, args.cols), rows=max(5, args.rows))
    if not pty.spawn(shell, cwd=str(cwd)):
        raise TerminalError(f"could not start {shell} in {cwd}")

    session = Session(id="term_" + uuid.uuid4().hex[:10], cwd=cwd, shell_path=shell, pty=pty)
    session.reader = threading.Thread(target=_pump, args=(session,), daemon=True)
    session.reader.start()
    _SESSIONS[session.id] = session

    _wait_ready(session)
    banner = strip_ansi(session.take()[0]).strip()
    log.info("term.opened", session=session.id, cwd=str(cwd))

    return TermOpenResult(
        session_id=session.id,
        shell=shell,
        cwd=str(cwd),
        banner=banner[-2000:],
        cols=args.cols,
        rows=args.rows,
    )


# ------------------------------------------------------------------- term.read


class TermReadArgs(ToolArgs):
    session_id: str
    #: Keep the escape sequences. The UI wants them; the model does not.
    raw: bool = False


class TermReadResult(ToolResult):
    session_id: str
    text: str
    truncated: bool
    alive: bool
    produced: int
    #: Characters lost to scrollback trimming since the session opened. Non-zero means
    #: output arrived faster than it was read and the OLDEST output is gone — so a
    #: caller looking for something near the start must not conclude it never appeared.
    dropped: int = 0


@tool(
    id="term.read",
    summary="Read output produced by a terminal session since the last read.",
    args=TermReadArgs,
    result=TermReadResult,
    capabilities={Capability.FS_READ},
    scopes={"projects", "notes", "scratch"},
    risk=Tier.T0,
    reversible=True,
    intents={"run", "investigate", "status"},
    side_effects="None. Reading consumes the buffer, it does not touch the shell.",
)
async def term_read(*, ctx: ToolContext, args: TermReadArgs) -> TermReadResult:
    session = _SESSIONS.get(args.session_id)
    if session is None:
        raise TerminalError(f"no terminal session {args.session_id!r}")

    text, more = session.take(MAX_READ_CHARS)
    if not args.raw:
        text = strip_ansi(text)
    if session.dropped:
        log.warning("term.scrollback_trimmed", session=session.id, dropped=session.dropped)
    return TermReadResult(
        session_id=session.id,
        text=text,
        # "There is more waiting" — NOT "some was thrown away". The remainder is still
        # in the buffer and the next read returns it.
        truncated=more,
        alive=session.alive,
        produced=session.produced,
        dropped=session.dropped,
    )


# ------------------------------------------------------------------ term.write


class TermWriteArgs(ToolArgs):
    session_id: str
    text: str
    #: Append a newline, i.e. actually run the line. False types without submitting.
    submit: bool = True


class TermWriteResult(ToolResult):
    session_id: str
    wrote: str
    submitted: bool


@tool(
    id="term.write",
    summary="Type text into a terminal session. Always requires confirmation.",
    args=TermWriteArgs,
    result=TermWriteResult,
    # NOT proc.spawn. docs/SECURITY.md#4b makes this a separate capability on purpose:
    # a spawn is an argv the allowlist can inspect, and this is not.
    capabilities={Capability.TERM_WRITE},
    scopes={"projects", "notes", "scratch"},
    # T2 and never lower. A shell has all of the user's privileges and no scope: no
    # allowlist can inspect what a line of text will do once a shell reads it, and
    # there is no undo. This is the one place the tool-contract argument does not
    # reach, so a human decides every time (docs/SECURITY.md#4b).
    risk=Tier.T2,
    reversible=False,
    dry_run=True,
    intents={"run", "control"},
    side_effects="Runs whatever the text says, with full user privileges.",
)
async def term_write(*, ctx: ToolContext, args: TermWriteArgs) -> TermWriteResult:
    session = _get(args.session_id)

    if "\x00" in args.text:
        raise TerminalError("refusing to write a NUL byte into a shell")
    # One line per call. Otherwise a single approval covers a script, and the
    # confirmation card shows the first line of something much longer.
    if "\n" in args.text.strip("\r\n") or "\r" in args.text.strip("\r\n"):
        raise TerminalError("refusing to write more than one line; approve each command separately")

    if ctx.dry_run:
        return TermWriteResult(session_id=session.id, wrote=args.text, submitted=False)

    session.pty.write(args.text + ("\r\n" if args.submit else ""))
    log.info("term.write", session=session.id, chars=len(args.text))
    return TermWriteResult(session_id=session.id, wrote=args.text, submitted=args.submit)


# ------------------------------------------------------------------ term.close


class TermCloseArgs(ToolArgs):
    session_id: str


class TermCloseResult(ToolResult):
    session_id: str
    was_alive: bool
    lifetime_s: float


@tool(
    id="term.close",
    summary="Close a terminal session and stop its shell.",
    args=TermCloseArgs,
    result=TermCloseResult,
    capabilities={Capability.PROC_KILL},
    scopes={"projects", "notes", "scratch"},
    risk=Tier.T1,
    reversible=False,
    intents={"run", "control"},
    side_effects="Ends the shell. Anything running inside it stops.",
)
async def term_close(*, ctx: ToolContext, args: TermCloseArgs) -> TermCloseResult:
    session = _SESSIONS.pop(args.session_id, None)
    if session is None:
        raise TerminalError(f"no terminal session {args.session_id!r}")

    was_alive = session.alive
    session.closed = True
    try:
        session.pty.write("exit\r\n")
    except Exception as exc:
        # The shell is already gone, which is the state we were asking for. Worth one
        # line in the log and nothing more.
        log.info("term.close_write_failed", session=session.id, error=str(exc))
    log.info("term.closed", session=session.id, was_alive=was_alive)
    return TermCloseResult(
        session_id=session.id,
        was_alive=was_alive,
        lifetime_s=round(time.time() - session.opened_at, 1),
    )


def sessions() -> list[dict[str, Any]]:
    """What is open. Used by `oracle.status` and by the tests."""
    return [
        {
            "id": s.id,
            "cwd": str(s.cwd),
            "alive": s.alive,
            "produced": s.produced,
            "dropped": s.dropped,
        }
        for s in _SESSIONS.values()
    ]


TERM_TOOLS = [term_open, term_read, term_write, term_close]

__all__ = ["MAX_SESSIONS", "TERM_TOOLS", "TerminalError", "sessions", "strip_ansi"]
