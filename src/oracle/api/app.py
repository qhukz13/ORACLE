"""FastAPI application: REST for named state, WS for streams.

Bound to loopback (docs/SECURITY.md#8-network-and-device-authentication). LAN exposure
is a later, explicit opt-in — it is not a flag we forget to turn off.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

import aiosqlite
from fastapi import APIRouter, FastAPI, Query, WebSocket, WebSocketDisconnect

from oracle import __version__
from oracle.config import Settings, get_settings
from oracle.core.eventlog import EventLog
from oracle.core.events import PROTOCOL_VERSION, ClientCommand, Event
from oracle.core.projects import discover_projects
from oracle.core.sessions import SessionStore
from oracle.llm.ollama import OllamaProvider
from oracle.llm.structured import StructuredStats
from oracle.llm.types import ProviderUnavailable
from oracle.logsink import bind_trace, configure, get_logger
from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.router.intent import IntentClassifier
from oracle.router.pipeline import TurnPipeline
from oracle.storage.db import connect, migrate
from oracle.tools import ToolExecutor, ToolRegistry, build_registry

log = get_logger(__name__)


@dataclass
class AppState:
    settings: Settings
    conn: aiosqlite.Connection
    eventlog: EventLog
    sessions: SessionStore
    agent: TurnPipeline
    provider: OllamaProvider | None
    policy: PolicyEngine
    audit: AuditLog
    registry: ToolRegistry
    executor: ToolExecutor
    schema_version: int = 0
    projects: list[str] = field(default_factory=list)
    tasks: set[asyncio.Task[None]] = field(default_factory=set)

    def spawn(self, coro: Any) -> None:
        """Track background work so shutdown can cancel it instead of orphaning it."""
        task: asyncio.Task[None] = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)


def state_of(app: FastAPI) -> AppState:
    return app.state.oracle  # type: ignore[no-any-return]


async def _build_state(settings: Settings) -> AppState:
    settings.ensure_dirs()
    conn = await connect(settings.db_path)
    version = await migrate(conn)
    eventlog = EventLog(conn, queue_size=settings.ws_queue_size)
    await eventlog.load_head()

    projects = discover_projects(settings.projects_root)
    stats = StructuredStats()

    # The policy gate. load_policy NEVER raises: bad or missing policy yields
    # read-only lockdown, loudly (docs/SECURITY.md#2).
    policy = load_policy(settings.policy_path)
    if policy.read_only:
        log.error(
            "policy.lockdown",
            source=policy.source,
            effect="read-only; no tool that changes anything will run",
        )
    engine = PolicyEngine(policy)
    audit = AuditLog(settings.audit_path)
    registry = build_registry()
    executor = ToolExecutor(registry, engine, audit)
    log.info("tools.registered", count=len(registry), tools=[c.id for c in registry.all()])

    provider: OllamaProvider | None = None
    classifier: IntentClassifier | None = None
    degraded: str | None = None

    if not settings.llm_enabled:
        degraded = "llm disabled by configuration"
    else:
        provider = OllamaProvider(model=settings.router_model, num_ctx=settings.router_ctx)
        try:
            await provider.preflight()
            classifier = IntentClassifier(provider, projects=projects, stats=stats)
        except ProviderUnavailable as exc:
            # Start anyway. Deterministic paths work without a model, and a banner is
            # a better first-run experience than a crash loop.
            degraded = exc.reason
            log.warning("llm.unavailable", reason=exc.reason, remedy=exc.remedy)

    state = AppState(
        settings=settings,
        conn=conn,
        eventlog=eventlog,
        sessions=SessionStore(conn),
        agent=TurnPipeline(eventlog, provider, classifier, projects=projects, stats=stats),
        provider=provider,
        policy=engine,
        audit=audit,
        registry=registry,
        executor=executor,
        schema_version=version,
        projects=projects,
    )
    state.agent.degraded = degraded
    return state


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure(settings.log_dir, settings.log_level)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        st = await _build_state(settings)
        app.state.oracle = st
        log.info(
            "oracled.started",
            port=settings.port,
            db=str(settings.db_path),
            schema_version=st.schema_version,
            last_seq=st.eventlog.last_seq,
        )
        try:
            yield
        finally:
            for task in list(st.tasks):
                task.cancel()
            for task in list(st.tasks):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            if st.provider is not None:
                await st.provider.aclose()
            await st.conn.close()
            log.info("oracled.stopped")

    app = FastAPI(title="ORACLE", version=__version__, lifespan=lifespan)
    app.include_router(_health_router())
    _register_routes(app)
    return app


# --------------------------------------------------------------------------- REST


def _health_router() -> APIRouter:
    """Liveness only — no auth, no app state, safe before startup completes."""
    r = APIRouter()

    @r.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return r


def _register_routes(app: FastAPI) -> None:
    # Defined here so the handlers can reach app state without a global.
    @app.get("/api/v1/status")
    async def status() -> dict[str, Any]:
        st = state_of(app)
        return {
            "version": __version__,
            "protocol": PROTOCOL_VERSION,
            "schema_version": st.schema_version,
            "last_seq": st.eventlog.last_seq,
            "subscribers": st.eventlog.subscriber_count,
            "agent": {
                "kind": "router",
                "model": st.provider.model if st.provider else None,
                "degraded": st.agent.degraded,
                "structured_output": st.agent.stats.snapshot(),
            },
            "projects": st.projects,
            "policy": {
                "source": st.policy.policy.source,
                "read_only": st.policy.policy.read_only,
                "halted": st.policy.halted,
                "halt_reason": st.policy.halt_reason,
                "scopes": sorted({s.name for s in st.policy.policy.scopes}),
            },
            "tools": [
                {"id": c.id, "risk": c.risk.label, "summary": c.summary} for c in st.registry.all()
            ],
            "audit": {"seq": st.audit.seq, "path": str(st.audit.path)},
        }

    @app.get("/api/v1/sessions")
    async def list_sessions() -> dict[str, Any]:
        st = state_of(app)
        return {"sessions": await st.sessions.list()}

    @app.post("/api/v1/sessions")
    async def create_session(origin: str = "api") -> dict[str, Any]:
        st = state_of(app)
        sid = await st.sessions.create(origin=origin)
        return {"session_id": sid}

    @app.get("/api/v1/sessions/{session_id}/events")
    async def session_events(
        session_id: str, since_seq: int = Query(0, ge=0), limit: int = Query(500, ge=1, le=2000)
    ) -> dict[str, Any]:
        st = state_of(app)
        events = await st.eventlog.read_range(since_seq, st.eventlog.last_seq, limit)
        return {"events": [e.wire() for e in events if e.session_id == session_id]}

    @app.websocket("/api/v1/stream")
    async def stream(ws: WebSocket, since_seq: int = Query(0, ge=0)) -> None:
        await _ws_handler(app, ws, since_seq)


# ----------------------------------------------------------------------------- WS


async def _ws_handler(app: FastAPI, ws: WebSocket, since_seq: int) -> None:
    st = state_of(app)
    await ws.accept()
    trace = bind_trace()

    # Two ways a client's since_seq can be unusable, both resolved by a resync:
    #  * older than retention  -> we cannot replay the gap
    #  * AHEAD of our last_seq -> the client is from a previous database. Without this
    #    branch the stream filters out every subsequent event as a "duplicate" and the
    #    connection hangs forever, live but silent. Found by a live smoke test.
    head = st.eventlog.last_seq
    floor = max(0, head - st.settings.resume_window)
    stale = since_seq > 0 and since_seq < floor
    ahead = since_seq > head
    resync = stale or ahead
    if resync:
        floor = floor if stale else head
        await ws.send_json(
            Event(
                type="session.resync",
                trace_id=trace,
                payload={
                    "reason": "since_seq ahead of server"
                    if ahead
                    else "since_seq older than retention",
                    "baseline": floor,
                },
            ).wire()
        )
        since_seq = floor

    log.info("ws.connected", since_seq=since_seq, resync=resync)

    async def pump() -> None:
        async for ev in st.eventlog.stream(since_seq):
            await ws.send_json(ev.wire())

    pump_task = asyncio.create_task(pump())
    try:
        while True:
            raw = await ws.receive_json()
            await _handle_command(st, raw)
    except WebSocketDisconnect:
        pass
    except Exception:
        log.exception("ws.error")
    finally:
        pump_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await pump_task
        log.info("ws.disconnected")


async def _handle_command(st: AppState, raw: dict[str, Any]) -> None:
    try:
        cmd = ClientCommand.model_validate(raw)
    except Exception:
        log.warning("ws.bad_command", keys=sorted(raw) if isinstance(raw, dict) else None)
        return

    if cmd.type == "session.message":
        session_id = str(cmd.payload.get("session_id") or "")
        text = str(cmd.payload.get("text") or "")
        if not text:
            return
        if not session_id or not await st.sessions.exists(session_id):
            session_id = await st.sessions.create(origin="ws")
            await st.eventlog.append(
                Event(
                    type="session.created",
                    session_id=session_id,
                    trace_id=bind_trace(),
                    payload={"origin": "ws"},
                )
            )
        await st.sessions.touch(session_id)
        st.spawn(st.agent.run(session_id, text))

    elif cmd.type == "halt":
        # Real now, not a stub. Order matters: flip policy to deny-all FIRST, so a
        # task that is mid-flight cannot slip one more tool call through while we are
        # still cancelling (docs/SECURITY.md#emergency-stop-halt).
        reason = str(cmd.payload.get("reason", "user requested halt"))
        st.policy.halt(reason)
        st.audit.append(actor="user", tool="oracle.halt", decision="halt", reason=reason)
        for task in list(st.tasks):
            task.cancel()
        st.agent.halted = True
        await st.eventlog.append(
            Event(
                type="agent.state",
                trace_id=bind_trace(),
                payload={"state": "halted", "reason": reason},
            )
        )

    elif cmd.type == "resume":
        # Never automatic. A human decides when a halt is over.
        st.policy.resume()
        st.agent.halted = False
        st.audit.append(actor="user", tool="oracle.resume", decision="resume")
        await st.eventlog.append(
            Event(type="agent.state", trace_id=bind_trace(), payload={"state": "idle"})
        )

    else:
        log.info("ws.unknown_command", type=cmd.type)
