"""Running an external program, from inside the toolhost.

Every process ORACLE spawns goes through here. The rules are short and none of them are
negotiable:

  * **argv lists only.** No `shell=True`, no `os.system`, no string-built command line.
    A shell string is not a promise about what can happen (docs/TOOLS.md rule 1).
  * **The program is a pinned absolute path**, handed down by the parent from the
    allowlist. Nothing here looks a program up.
  * **A timeout kills the process, and the call is not retried.** The side effect may
    already have happened; retrying is how an agent commits twice and calls it
    resilience.
  * **Output is captured, capped, and written to a blob** — the model gets structured
    fields, the human gets the full log (docs/TOOLS.md rule 4).

Note the process tree is *not* reaped by hand on timeout. This code runs inside the
toolhost child, which lives in a Job Object with `KILL_ON_JOB_CLOSE`; killing the child
kills everything it spawned, including grandchildren. That is the whole reason the
toolhost had to exist before any of these tools.
"""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from oracle.config import get_settings
from oracle.logsink import get_logger

log = get_logger(__name__)

#: What we are willing to hold in memory from one process. Anything beyond this is
#: truncated in the result and preserved whole in the blob.
MAX_CAPTURE_BYTES = 2 * 1024 * 1024
#: What may appear in a tool result at all. The model does not need 2 MB of npm output,
#: and paying to put it in the context window is how a small model loses the plot.
MAX_INLINE_CHARS = 8_000


class ProcessTimeout(Exception):
    """Deliberately not a subclass of anything retryable."""


@dataclass(frozen=True)
class Completed:
    program: str
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str
    duration_ms: int
    truncated: bool

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def combined(self) -> str:
        return self.stdout + (("\n" + self.stderr) if self.stderr else "")

    def argv_display(self) -> str:
        """What the confirmation card and the audit log show. Never re-executed —
        it is a rendering of the argv, not a source of one."""
        return " ".join([Path(self.program).name, *self.args])


def _decode(raw: bytes) -> str:
    """UTF-8, falling back to the ANSI code page.

    Git and the test runners are configured for UTF-8 below, but a Windows tool that
    ignores that still emits the console code page — cp1251 on this machine. Replacing
    undecodable bytes beats raising: partial output is diagnostic, an exception is not.
    """
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace")


async def run(
    program: Path,
    args: list[str],
    *,
    cwd: Path,
    timeout_s: int,
    env: dict[str, str] | None = None,
) -> Completed:
    """Spawn, wait, capture. Raises `ProcessTimeout` if the deadline passes."""
    import os
    import time

    started = time.perf_counter()
    child_env = {**os.environ, **(env or {})}

    proc = await asyncio.create_subprocess_exec(
        str(program),
        *args,
        cwd=str(cwd),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=child_env,
        # Never open a console window, and never inherit this process's stdin: a
        # program that decides to prompt must hit EOF, not block the toolhost forever.
        stdin=asyncio.subprocess.DEVNULL,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except TimeoutError:
        # Kill, then wait: an unwaited kill leaves a zombie and hides the exit.
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.kill()
        with contextlib.suppress(TimeoutError, ProcessLookupError, OSError):
            await asyncio.wait_for(proc.wait(), timeout=5)
        raise ProcessTimeout(
            f"{Path(program).name} {' '.join(args)} exceeded {timeout_s}s and was terminated. "
            "The action may or may not have completed — it will not be retried."
        ) from None

    truncated = len(out) > MAX_CAPTURE_BYTES or len(err) > MAX_CAPTURE_BYTES
    return Completed(
        program=str(program),
        args=tuple(args),
        returncode=proc.returncode if proc.returncode is not None else -1,
        stdout=_decode(out[:MAX_CAPTURE_BYTES]),
        stderr=_decode(err[:MAX_CAPTURE_BYTES]),
        duration_ms=int((time.perf_counter() - started) * 1000),
        truncated=truncated,
    )


def clip(text: str, limit: int = MAX_INLINE_CHARS) -> tuple[str, bool]:
    """Cut text down to what may cross into a prompt. Returns (text, was_clipped)."""
    if len(text) <= limit:
        return text, False
    return text[:limit] + f"\n... [{len(text) - limit} more characters in the log]", True


def write_blob(name: str, content: str) -> str:
    """Persist full output and return its path.

    docs/TOOLS.md rule 4: the model gets structured fields, the human gets the whole
    log. Keeping both means neither audience is served a summary of the other's needs.
    """
    day = datetime.now(UTC).strftime("%Y-%m-%d")
    folder = get_settings().blobs_dir / day
    folder.mkdir(parents=True, exist_ok=True)
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)[:60]
    blob = folder / f"{uuid.uuid4().hex[:12]}__{safe}.log"
    blob.write_text(content, encoding="utf-8", errors="replace")
    return str(blob)


__all__ = [
    "MAX_CAPTURE_BYTES",
    "MAX_INLINE_CHARS",
    "Completed",
    "ProcessTimeout",
    "clip",
    "run",
    "write_blob",
]
