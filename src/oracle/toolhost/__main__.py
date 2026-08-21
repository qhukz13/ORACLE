"""`oracle-toolhost` — the low-privilege executor process.

Runs inside a Job Object created by the parent. Reads newline-delimited `Invocation`
frames from stdin and writes `Response` frames to stdout; stderr is for logs only.

**This process holds nothing.** No policy, no secrets, no database handle, no way back
into the runtime. Everything it is permitted to do was decided before the invocation
crossed the pipe (ADR-0003). If this process is compromised, the blast radius is one
tool call's worth of already-granted permission.

It is deliberately started with a *constructed* environment rather than an inherited
one, so `ANTHROPIC_API_KEY` and friends are not merely unused here — they are absent.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from typing import Any

from oracle.logsink import configure
from oracle.toolhost.protocol import HostEvent, Invocation, Response

#: The protocol channel, captured before anything else can touch it.
#:
#: MEASURED: structlog's default logger writes to **stdout**, which is this pipe. Any
#: tool that logs — every `term.*` call does — emitted a line the parent then tried to
#: parse as a `Response`, visible as `toolhost.unparseable_frame`. That was benign only
#: by luck: a log line that happened to be valid JSON would have been read as a reply.
#:
#: So stdout is taken away from everything but this function, and `sys.stdout` is
#: pointed at stderr in `main()`.
_PROTOCOL = sys.stdout


def _emit(payload: dict[str, Any]) -> None:
    _PROTOCOL.write(json.dumps(payload, ensure_ascii=False) + "\n")
    _PROTOCOL.flush()


def _log(message: str) -> None:
    sys.stderr.write(message + "\n")
    sys.stderr.flush()


async def _handle(inv: Invocation, registry: Any) -> Response:
    started = time.perf_counter()
    try:
        contract = registry.get(inv.tool)
    except Exception:
        return Response(
            id=inv.id, ok=False, error_kind="not_found", error_message=f"unknown tool {inv.tool!r}"
        )

    try:
        args = contract.args_model.model_validate(inv.args)
    except Exception as exc:
        return Response(
            id=inv.id, ok=False, error_kind="invalid_args", error_message=str(exc)[:400]
        )

    from pathlib import Path

    from oracle.tools.contract import ToolContext

    ctx = ToolContext(
        resolved={k: Path(v) for k, v in inv.resolved.items()},
        programs={k: Path(v) for k, v in inv.programs.items()},
        cwd=Path(inv.cwd) if inv.cwd else None,
        dry_run=inv.dry_run,
    )

    try:
        result = await asyncio.wait_for(contract.handler(ctx=ctx, args=args), timeout=inv.timeout_s)
    except TimeoutError:
        # Same wording as the parent-side timeout on purpose. Which side noticed the
        # deadline is an implementation detail; the uncertainty for the caller is
        # identical, and a partially-completed side effect must never look retryable.
        return Response(
            id=inv.id,
            ok=False,
            error_kind="timeout",
            error_message=(
                f"{inv.tool} exceeded {inv.timeout_s}s. "
                "The action may or may not have completed — it will not be retried."
            ),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
    except Exception as exc:
        return Response(
            id=inv.id,
            ok=False,
            error_kind="execution_failed",
            error_message=str(exc)[:400],
            duration_ms=int((time.perf_counter() - started) * 1000),
        )

    return Response(
        id=inv.id,
        ok=True,
        result=result.model_dump(mode="json"),
        duration_ms=int((time.perf_counter() - started) * 1000),
    )


async def serve() -> int:
    from oracle.tools import build_registry

    registry = build_registry()
    _emit(HostEvent(type="ready", tools=[c.id for c in registry.all()]).model_dump(mode="json"))

    # MEASURED: `loop.connect_read_pipe(sys.stdin)` does not work on Windows' Proactor
    # event loop — it raises inside `_ProactorReadPipeTransport._loop_reading` and the
    # reader dies silently, so the child looks alive, answers `ready`, and then never
    # responds to anything. Every call times out at 30 s with no error to point at.
    #
    # Reading on a worker thread is the portable way to do this on Windows. The child
    # handles one invocation at a time, so a thread per read is not a cost that matters.
    while True:
        line = await asyncio.to_thread(sys.stdin.buffer.readline)
        if not line:
            return 0  # parent closed the pipe
        raw = line.decode("utf-8", errors="replace").strip()
        if not raw:
            continue
        try:
            inv = Invocation.model_validate_json(raw)
        except Exception as exc:
            _log(f"toolhost: unparseable frame: {exc}")
            continue

        response = await _handle(inv, registry)
        _emit(response.model_dump(mode="json"))


def main() -> int:
    # Refuse to run with an inherited environment that still holds secrets. The parent
    # always constructs a minimal env; if this fires, something is spawning us wrongly.
    for leaky in ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GITHUB_TOKEN"):
        if leaky in os.environ:
            _log(f"toolhost: refusing to start — {leaky} is present in the environment")
            return 2

    # Logs to stderr, no log file: this process holds nothing durable, and the parent
    # owns the log directory. Then stdout is pointed at stderr so that a stray `print()`
    # in any tool goes somewhere harmless instead of onto the protocol channel.
    configure(None, os.environ.get("ORACLE_LOG_LEVEL", "info"))
    sys.stdout = sys.stderr

    try:
        return asyncio.run(serve())
    except KeyboardInterrupt:  # pragma: no cover
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
