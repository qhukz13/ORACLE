"""Parent-side supervision of `oracle-toolhost`.

Owns the Job Object, the pipe, the timeouts and the restart policy.

The rule that shapes everything here: **a step whose side effect may already have
happened is never silently retried.** A timeout on `git commit` does not mean the
commit did not happen — it means we stopped waiting. Retrying would be how ORACLE
creates two commits, or two pushes, and calls it resilience.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from oracle.logsink import get_logger
from oracle.toolhost.jobobject import JobObject, JobObjectError
from oracle.toolhost.protocol import Invocation, Response

log = get_logger(__name__)

#: Environment handed to the child. Constructed, never inherited: secrets are absent
#: rather than merely unused (docs/SECURITY.md#4b).
_ENV_ALLOW = ("SystemRoot", "windir", "TEMP", "TMP", "PATHEXT", "NUMBER_OF_PROCESSORS", "OS")

START_TIMEOUT_S = 20.0
STOP_GRACE_S = 3.0


class ToolHostError(RuntimeError):
    pass


class ToolHostUnavailable(ToolHostError):
    """The host could not be started or has died and could not be restarted."""


@dataclass
class HostStats:
    starts: int = 0
    crashes: int = 0
    timeouts: int = 0
    calls: int = 0
    tools: list[str] = field(default_factory=list)

    def snapshot(self) -> dict[str, Any]:
        return {
            "starts": self.starts,
            "crashes": self.crashes,
            "timeouts": self.timeouts,
            "calls": self.calls,
            "tools": len(self.tools),
        }


def _child_env() -> dict[str, str]:
    env = {k: os.environ[k] for k in _ENV_ALLOW if k in os.environ}
    # A minimal PATH: enough for the interpreter to start, not the user's full PATH.
    system_root = env.get("SystemRoot", r"C:\Windows")
    env["PATH"] = os.pathsep.join(
        [str(Path(sys.executable).parent), rf"{system_root}\System32", system_root]
    )
    env["PYTHONUNBUFFERED"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


class ToolHost:
    """Supervises one child process. Serialises calls: the child handles one
    invocation at a time, which keeps ordering and cancellation simple and is nowhere
    near a bottleneck at a single user's request rate."""

    def __init__(self, *, cwd: Path | None = None, max_restarts: int = 5) -> None:
        self._cwd = cwd
        self._proc: asyncio.subprocess.Process | None = None
        self._job: JobObject | None = None
        self._lock = asyncio.Lock()
        self._max_restarts = max_restarts
        self.stats = HostStats()

    # ------------------------------------------------------------------ lifecycle

    @property
    def running(self) -> bool:
        return self._proc is not None and self._proc.returncode is None

    async def start(self) -> None:
        if self.running:
            return

        job = JobObject()
        try:
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-m",
                "oracle.toolhost",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self._cwd) if self._cwd else None,
                env=_child_env(),
                # No new console window; the child is headless.
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
        except Exception as exc:
            job.close()
            raise ToolHostUnavailable(f"could not spawn toolhost: {exc}") from exc

        # Assign before the child does anything. Its first action is to read stdin, so
        # there is no window in which it could spawn an unassigned grandchild.
        try:
            handle = _process_handle(proc)
            job.assign(handle)
        except (JobObjectError, OSError, ToolHostError) as exc:
            # Refuse to run a child we cannot guarantee to kill — and reap it fully.
            # An earlier version killed it without waiting or closing the transport,
            # which leaked a process and a pipe on every failed start.
            await _reap(proc)
            job.close()
            raise ToolHostUnavailable(f"could not isolate toolhost: {exc}") from exc

        self._proc, self._job = proc, job
        self.stats.starts += 1

        try:
            ready = await asyncio.wait_for(self._read_frame(), timeout=START_TIMEOUT_S)
        except (TimeoutError, ToolHostError) as exc:
            await self.stop()
            raise ToolHostUnavailable(f"toolhost did not become ready: {exc}") from exc

        self.stats.tools = list(ready.get("tools", []))
        log.info("toolhost.started", pid=proc.pid, tools=len(self.stats.tools))

    async def stop(self) -> None:
        proc, job = self._proc, self._job
        self._proc, self._job = None, None

        if proc is not None:
            await _reap(proc, graceful=True)

        # The job goes last and is the real guarantee: closing it kills anything the
        # child spawned, however the child itself ended.
        if job is not None:
            job.terminate()
            job.close()

    async def kill_tree(self) -> None:
        """HALT path. Terminates the whole job immediately, no grace period."""
        if self._job is not None:
            self._job.terminate()
        await self.stop()

    # -------------------------------------------------------------------- dispatch

    async def call(
        self,
        tool: str,
        args: dict[str, Any],
        *,
        resolved: dict[str, str] | None = None,
        timeout_s: int = 30,
        cwd: Path | None = None,
        dry_run: bool = False,
        invocation_id: str = "inv",
    ) -> Response:
        async with self._lock:
            if not self.running:
                if self.stats.crashes > self._max_restarts:
                    raise ToolHostUnavailable(
                        f"toolhost crashed {self.stats.crashes} times; not restarting again"
                    )
                await self.start()

            proc = self._proc
            if proc is None or proc.stdin is None:  # pragma: no cover - defensive
                raise ToolHostUnavailable("toolhost is not running")

            inv = Invocation(
                id=invocation_id,
                tool=tool,
                args=args,
                resolved=resolved or {},
                cwd=str(cwd) if cwd else None,
                timeout_s=timeout_s,
                dry_run=dry_run,
            )
            self.stats.calls += 1

            proc.stdin.write((inv.model_dump_json() + "\n").encode("utf-8"))
            await proc.stdin.drain()

            # Parent-side deadline sits above the child's own, so a wedged child is
            # still bounded.
            try:
                frame = await asyncio.wait_for(self._read_frame(), timeout=timeout_s + 5)
            except TimeoutError:
                self.stats.timeouts += 1
                log.error("toolhost.timeout", tool=tool, timeout_s=timeout_s)
                await self.kill_tree()
                # NOT retried: the side effect may already have happened.
                return Response(
                    id=invocation_id,
                    ok=False,
                    error_kind="timeout",
                    error_message=(
                        f"{tool} exceeded {timeout_s}s; the toolhost was terminated. "
                        "The action may or may not have completed — it will not be retried."
                    ),
                )
            except ToolHostError as exc:
                self.stats.crashes += 1
                log.error("toolhost.crashed", tool=tool, error=str(exc))
                await self.stop()
                return Response(
                    id=invocation_id,
                    ok=False,
                    error_kind="execution_failed",
                    error_message=(
                        f"the toolhost died while running {tool}. "
                        "The action may or may not have completed — it will not be retried."
                    ),
                )

            return Response.model_validate(frame)

    # --------------------------------------------------------------------- private

    async def _read_frame(self) -> dict[str, Any]:
        proc = self._proc
        if proc is None or proc.stdout is None:  # pragma: no cover - defensive
            raise ToolHostError("toolhost is not running")
        while True:
            line = await proc.stdout.readline()
            if not line:
                raise ToolHostError("toolhost closed its output pipe")
            text = line.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            try:
                return dict(json.loads(text))
            except json.JSONDecodeError:
                log.warning("toolhost.unparseable_frame", frame=text[:200])
                continue


async def _reap(proc: asyncio.subprocess.Process, *, graceful: bool = False) -> None:
    """Fully release a child: stop it, wait for it, and close its pipe transports.

    All three steps are needed. Skipping the wait leaves a zombie `Popen`; skipping the
    transport close leaves pipe handles that asyncio only reclaims in `__del__`, which
    in a long-lived process that restarts the toolhost repeatedly is a slow leak rather
    than a warning.
    """
    if proc.returncode is None:
        if graceful:
            with contextlib.suppress(ProcessLookupError, OSError):
                if proc.stdin is not None and not proc.stdin.is_closing():
                    proc.stdin.close()
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(proc.wait(), timeout=STOP_GRACE_S)
        if proc.returncode is None:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()
            with contextlib.suppress(TimeoutError, ProcessLookupError, OSError):
                await asyncio.wait_for(proc.wait(), timeout=STOP_GRACE_S)

    transport = getattr(proc, "_transport", None)
    if transport is not None:
        with contextlib.suppress(Exception):
            transport.close()


def _process_handle(proc: asyncio.subprocess.Process) -> int:
    """Get the Win32 HANDLE for an asyncio subprocess.

    asyncio wraps a `subprocess.Popen`; on Windows its `_handle` is the process handle
    we need for `AssignProcessToJobObject`. Reaching for a private attribute is
    unpleasant, but the alternative — re-implementing process spawning — is worse, and
    a failure here is loud rather than silent: `start()` refuses to run a child it
    cannot isolate.
    """
    popen = getattr(proc, "_transport", None)
    popen = getattr(popen, "_proc", None) if popen is not None else None
    handle = getattr(popen, "_handle", None)
    if handle is None:
        raise ToolHostError("could not obtain the child process handle for job assignment")
    return int(handle)


__all__ = ["HostStats", "ToolHost", "ToolHostError", "ToolHostUnavailable"]
