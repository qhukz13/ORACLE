"""Structured logging with mandatory redaction and trace_id propagation.

docs/LOGGING.md. JSONL, one event per line, rotated. `trace_id` rides on a contextvar
so it propagates through async calls without threading it through every signature.
"""

from __future__ import annotations

import logging
import logging.handlers
import sys
import uuid
from contextvars import ContextVar
from pathlib import Path
from typing import cast

import structlog
from structlog.typing import EventDict, WrappedLogger

from oracle.logsink.redact import redact

__all__ = ["bind_trace", "configure", "get_logger", "new_trace_id", "trace_id_var"]

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="-")


def new_trace_id() -> str:
    return "tr_" + uuid.uuid4().hex[:12]


def bind_trace(trace_id: str | None = None) -> str:
    tid = trace_id or new_trace_id()
    trace_id_var.set(tid)
    return tid


def _add_trace(_logger: WrappedLogger, _name: str, ev: EventDict) -> EventDict:
    ev.setdefault("trace_id", trace_id_var.get())
    return ev


def _redact_processor(_logger: WrappedLogger, _name: str, ev: EventDict) -> EventDict:
    """The sink. Every record crosses this — there is no path around it."""
    return cast("EventDict", redact(ev))


def configure(log_dir: Path | None = None, level: str = "info") -> None:
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stderr)]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(
            log_dir / "app.jsonl", maxBytes=50 * 1024 * 1024, backupCount=5, encoding="utf-8"
        )
        handlers.append(fh)

    logging.basicConfig(
        format="%(message)s",
        handlers=handlers,
        level=getattr(logging, level.upper(), logging.INFO),
        force=True,
    )

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_trace,
            _redact_processor,
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, level.upper(), logging.INFO)
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    return structlog.get_logger(name)  # type: ignore[no-any-return]
