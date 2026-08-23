"""FastAPI application: REST for named state, WS for streams.

Bound to loopback (docs/SECURITY.md#8-network-and-device-authentication). LAN exposure
is a later, explicit opt-in — it is not a flag we forget to turn off.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
from fastapi import APIRouter, FastAPI, Query, WebSocket, WebSocketDisconnect

from oracle import __version__
from oracle.config import Settings, get_settings
from oracle.core.approvals import ApprovalStore
from oracle.core.eventlog import EventLog
from oracle.core.events import PROTOCOL_VERSION, ClientCommand, Event, new_id
from oracle.core.projects import discover_projects
from oracle.core.sessions import SessionStore
from oracle.core.terminal import TerminalBridge
from oracle.delegation.service import DelegationService, PacketInputs
from oracle.handoff.gather import gather_git_state
from oracle.integrations.claude import ClaudeCodeAdapter
from oracle.integrations.types import HandoffPacket
from oracle.llm.ollama import OllamaProvider
from oracle.llm.structured import StructuredStats
from oracle.llm.types import ProviderUnavailable
from oracle.logsink import bind_trace, configure, get_logger
from oracle.mcp.calls import McpCallHandler
from oracle.mcp.tokens import TokenStore
from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.router.intent import IntentClassifier
from oracle.router.pipeline import TurnPipeline
from oracle.router.selection import ToolSelector
from oracle.storage.db import connect, migrate
from oracle.toolhost import ToolHost
from oracle.tools import ToolExecutor, ToolRegistry, build_registry, git_undo_runner
from oracle.tools.undo import UndoJournal

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
    host: ToolHost
    undo: UndoJournal
    approvals: ApprovalStore
    terminals: TerminalBridge
    delegations: DelegationService
    #: Delegation capabilities and the inbound MCP call path (INTEGRATIONS.md §4).
    tokens: TokenStore
    mcp: McpCallHandler
    schema_version: int = 0
    projects: list[str] = field(default_factory=list)
    indexer: Any = None
    tasks: set[asyncio.Task[None]] = field(default_factory=set)

    def spawn(self, coro: Any) -> None:
        """Track background work so shutdown can cancel it instead of orphaning it."""
        task: asyncio.Task[None] = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)


def state_of(app: FastAPI) -> AppState:
    return app.state.oracle  # type: ignore[no-any-return]


def _curate(st: AppState, repo: Path, project: str, task_text: str) -> PacketInputs:
    """§6 curation for a delegation: orientation docs, retrieval scoped to the project,
    git state. Runs off the event loop (`to_thread`) — the embedder alone is seconds.

    Degradable on purpose: no knowledge index or no embedding model means a thinner
    packet (docs + git state), never a failed delegation. The taint from retrieval
    provenance rides into `PacketInputs`, so the egress approval escalates when the
    packet carries `local_foreign` text (SECURITY.md §6)."""
    from oracle.handoff.gather import gather_project_docs, gather_retrieval

    excerpts = list(gather_project_docs(repo))
    tainted: tuple[str, ...] = ()
    try:
        from oracle.rag.embedding import DEFAULT, Embedder
        from oracle.rag.store import KnowledgeStore

        store = KnowledgeStore(st.settings.data_dir / "knowledge.db", DEFAULT.out_dim)
        try:
            store.bind(DEFAULT.name, DEFAULT.out_dim)
            hits, tainted = gather_retrieval(task_text, store, Embedder(DEFAULT), project=project)
            excerpts.extend(hits)
        finally:
            store.close()
    except Exception as exc:
        log.info("delegate.curation_degraded", reason=str(exc))
    return PacketInputs(
        excerpts=tuple(excerpts),
        state=gather_git_state(repo),
        tainted_sources=tainted,
    )


def _worktree_verifier(
    executor: ToolExecutor,
) -> Callable[[Path], Awaitable[dict[str, Any] | None]]:
    """The independent half of result collection: `dev.run_tests` in the delegate's
    worktree, through the gate like any other tool call — ORACLE's evidence, not the
    agent's claim (INTEGRATIONS.md §7)."""

    async def verify(path: Path) -> dict[str, Any] | None:
        outcome = await executor.execute("dev.run_tests", {"path": str(path)})
        if not outcome.ok:
            reason = str(outcome.error) if outcome.error else "run failed"
            return {"ran": False, "reason": reason}
        return {"ran": True, **(outcome.result.model_dump() if outcome.result else {})}

    return verify


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
    policy = load_policy(settings.policy_path, settings.apps_path)
    if policy.read_only:
        log.error(
            "policy.lockdown",
            source=policy.source,
            effect="read-only; no tool that changes anything will run",
        )
    engine = PolicyEngine(policy)
    audit = AuditLog(settings.audit_path)
    registry = build_registry()
    # Tools execute across a process boundary (ADR-0003). Pre-warmed in the background
    # rather than started lazily: a cold start costs ~1.2 s, which the user would
    # otherwise pay on their first tool call. A failure here must not stop the agent
    # from starting and explaining itself, so it is fire-and-forget.
    host = ToolHost(cwd=settings.projects_root)
    undo = UndoJournal(settings.undo_journal)
    executor = ToolExecutor(registry, engine, audit, host=host, undo=undo)
    # Reversing a git mutation means spawning git, which this process must not do — so
    # the journal dispatches those back through the gate as `git.undo` (ADR-0003).
    undo.set_git_runner(git_undo_runner(executor))
    approvals = ApprovalStore(eventlog, executor)
    terminals = TerminalBridge(eventlog, executor)
    # Delegation lives in the daemon, not the toolhost: it is minutes-long and owns a
    # child process. The egress preview rides the same ApprovalStore as every other
    # T2 action, and verification goes back through the gate as `dev.run_tests`.
    tokens = TokenStore()
    mcp = McpCallHandler(tokens, executor, eventlog)
    delegations = DelegationService(
        eventlog,
        approvals,
        engine,
        ClaudeCodeAdapter(),
        run_tests=_worktree_verifier(executor),
        # The delegate calls back through the same executor this line already built —
        # one gate, one audit log (INTEGRATIONS.md §4).
        tokens=tokens,
        mcp_url=f"http://127.0.0.1:{settings.port}",
    )
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
        agent=TurnPipeline(
            eventlog,
            provider,
            classifier,
            projects=projects,
            stats=stats,
            executor=executor,
            # Selection needs the model. Without one the pipeline still routes and
            # still refuses clearly — it just cannot choose a tool.
            selector=ToolSelector(registry, provider, stats=stats) if provider else None,
            approvals=approvals,
            projects_root=settings.projects_root,
        ),
        provider=provider,
        policy=engine,
        audit=audit,
        registry=registry,
        executor=executor,
        host=host,
        undo=undo,
        approvals=approvals,
        terminals=terminals,
        delegations=delegations,
        tokens=tokens,
        mcp=mcp,
        schema_version=version,
        projects=projects,
    )
    state.agent.degraded = degraded
    return state


async def _prewarm(st: AppState) -> None:
    """Start the toolhost ahead of first use. Failure is logged, never fatal."""
    try:
        await st.host.start()
    except Exception as exc:
        log.warning("toolhost.prewarm_failed", error=str(exc))


def _start_indexing(st: AppState) -> None:
    """Own the knowledge watcher for the daemon's lifetime (RAG.md §6).

    Spawned through `AppState.spawn`, which is the point: HALT cancels every tracked task,
    so the emergency stop already reaches the indexer without a second mechanism to keep in
    step with the first. `resume` calls this again.

    Never fatal. A missing or malformed `collections.yaml` means "index nothing", and the
    rest of the daemon is unaffected — chat, tools and the terminal do not depend on it.
    """
    if not st.settings.watch_knowledge:
        return
    try:
        from oracle.rag.collections import load_registry
        from oracle.rag.service import IndexService

        registry = load_registry(st.settings.collections_path)
    except Exception as exc:
        log.warning("rag.watch_unconfigured", error=str(exc))
        return

    async def publish(event_type: str, payload: dict[str, Any]) -> None:
        await st.eventlog.append(Event(type=event_type, trace_id=bind_trace(), payload=payload))

    st.indexer = IndexService(registry, st.settings.data_dir, publish=publish)
    st.spawn(st.indexer.run())


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure(settings.log_dir, settings.log_level)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        st = await _build_state(settings)
        app.state.oracle = st
        if settings.prewarm_toolhost:
            st.spawn(_prewarm(st))
        _start_indexing(st)
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
            await st.terminals.stop()
            await st.host.stop()
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
            # The UI needs a real path to open a terminal in, and it must be one the
            # runtime chose — not one assembled in the browser.
            "projects_root": str(st.settings.projects_root),
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
            "toolhost": {"running": st.host.running, **st.host.stats.snapshot()},
            "undo": {"available": len(st.undo.latest(50))},
            "approvals": {"open": st.approvals.open_requests()},
            "terminals": st.terminals.snapshot(),
        }

    @app.get("/api/v1/knowledge")
    async def knowledge() -> dict[str, Any]:
        """The index health view — what is indexed, when, how big, what failed (RAG.md §9).

        Opened per request rather than held on app state. `knowledge.db` is disposable:
        a reindex or a `SchemaMismatch` can replace the file underneath a long-lived
        handle, and a health view that reports a stale handle's contents is worse than
        one that costs a few milliseconds to open.
        """
        from oracle.rag.embedding import E5_BASE
        from oracle.rag.store import KnowledgeStore, SchemaMismatch

        st = state_of(app)
        path = st.settings.data_dir / "knowledge.db"
        if not path.exists():
            return {"built": False, "path": str(path), "model": E5_BASE.name}
        try:
            store = KnowledgeStore(path, E5_BASE.out_dim)
            try:
                store.bind(E5_BASE.name, E5_BASE.out_dim)
                return {"built": True, "model": E5_BASE.name, **store.stats()}
            finally:
                store.close()
        except SchemaMismatch as exc:
            # A real state the user has to see and can fix in one click: the index was
            # built by a different model, so it is not stale, it is wrong.
            return {"built": False, "path": str(path), "stale": True, "error": str(exc)}

    # ---------------------------------------------------------------- MCP inbound
    #
    # A delegated agent calling back into ORACLE (INTEGRATIONS.md §4). Loopback only,
    # like the rest of the API, and authorised by a delegation capability rather than
    # by being on the box: the token names its own tools, its own worktree and its own
    # expiry, and the daemon re-derives all three on every call.
    #
    # Deliberately NOT behind the WS protocol: the bridge is a short-lived child of the
    # delegate's CLI, not a UI client, and giving it the event-stream socket would hand
    # a delegated agent the whole command surface.

    @app.post("/api/v1/mcp/tools")
    async def mcp_tools(body: dict[str, Any]) -> dict[str, Any]:
        from oracle.mcp.catalogue import describe
        from oracle.mcp.tokens import TokenError

        st = state_of(app)
        try:
            cap = st.tokens.verify(str(body.get("token", "")))
        except TokenError as exc:
            log.warning("mcp.tools_rejected", reason=str(exc))
            # An empty list, not a 401: the client renders a server error either way,
            # and a bridge that cannot list tools must not look like a working one.
            return {"tools": []}
        return {"tools": describe(st.registry, cap)}

    @app.post("/api/v1/mcp/call")
    async def mcp_call(body: dict[str, Any]) -> dict[str, Any]:
        from oracle.mcp.catalogue import resolve
        from oracle.mcp.tokens import TokenError

        st = state_of(app)
        token = str(body.get("token", ""))
        try:
            cap = st.tokens.verify(token)
        except TokenError as exc:
            log.warning("mcp.call_rejected", reason=str(exc))
            return {"ok": False, "payload": {"error": "not permitted"}}

        tool = resolve(str(body.get("tool", "")), cap)
        if tool is None:
            return {"ok": False, "payload": {"error": "no such tool in this delegation"}}
        result = await st.mcp.call(token, tool, dict(body.get("arguments") or {}))
        return {"ok": result.ok, "payload": result.payload}

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


def session_of(cmd: ClientCommand) -> str | None:
    """The session a command belongs to, when it names one."""
    value = cmd.payload.get("session_id")
    return str(value) if value else None


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

    elif cmd.type == "approval.respond":
        # The ONLY way an approval is granted. Note what is not here: the client sends
        # an id and a decision, never a digest or a tier — those come from what the user
        # was actually shown (docs/SECURITY.md#5).
        approval_id = str(cmd.payload.get("approval_id") or "")
        approved = str(cmd.payload.get("decision", "")).lower() in ("approve", "approved", "yes")
        if approval_id:
            resolution = await st.approvals.resolve(approval_id, approved, by="user")
            st.audit.append(
                actor="user",
                tool="oracle.approval",
                decision=resolution,
                approval_id=approval_id,
            )

    elif cmd.type == "undo":
        undo_id = str(cmd.payload.get("undo_id") or "")
        record = undo_id or next((r.id for r in reversed(st.undo.latest(1))), "")
        if not record:
            return
        try:
            result = await st.undo.undo(record)
            payload: dict[str, Any] = {"state": "idle", "undone": result}
        except Exception as exc:
            payload = {"state": "idle", "undo_failed": str(exc)}
        st.audit.append(actor="user", tool="oracle.undo", decision="undo", undo_id=record)
        await st.eventlog.append(Event(type="agent.state", trace_id=bind_trace(), payload=payload))

    elif cmd.type == "term.open":
        # The human opening a shell. Everything about it still goes through the gate —
        # the path is canonicalised and scope-checked like any other.
        await st.terminals.open(str(cmd.payload.get("path") or ""), session_id=session_of(cmd))

    elif cmd.type == "term.input":
        # The person typing, NOT the agent. `term.write` is the agent's route and is
        # confirmed every time; asking someone to approve their own keystrokes would be
        # theatre (docs/API.md, docs/SECURITY.md#4b).
        await st.terminals.input(
            str(cmd.payload.get("pty_id") or ""), str(cmd.payload.get("data") or "")
        )

    elif cmd.type == "term.resize":
        await st.terminals.resize(
            str(cmd.payload.get("pty_id") or ""),
            int(cmd.payload.get("cols") or 100),
            int(cmd.payload.get("rows") or 30),
        )

    elif cmd.type == "term.close":
        await st.terminals.close(str(cmd.payload.get("pty_id") or ""), session_id=session_of(cmd))

    elif cmd.type == "delegate":
        # The human starting a delegation. The service asks its own question — the
        # egress preview — before anything leaves the machine, so this command only
        # has to name the work. Model-initiated delegation (the router's
        # complexity signal) is the phase capstone, not this seam.
        task_text = str(cmd.payload.get("task") or "").strip()
        project = str(cmd.payload.get("project") or "").strip()
        repo = (st.settings.projects_root / project).resolve() if project else None
        if (
            not task_text
            or repo is None
            or not repo.is_dir()
            or not repo.is_relative_to(st.settings.projects_root.resolve())
        ):
            # `..` in a project name must not walk out of the projects root.
            log.warning("delegate.bad_request", project=project, has_task=bool(task_text))
            return
        raw_tools = cmd.payload.get("allowed_tools")
        allowed = (
            tuple(str(t) for t in raw_tools)
            if isinstance(raw_tools, list) and raw_tools
            else ("Read", "Edit", "Write")
        )
        pkt = HandoffPacket(task_id=new_id("dlg"), task=task_text, allowed_tools=allowed)
        inputs = await asyncio.to_thread(_curate, st, repo, project, task_text)
        st.spawn(
            st.delegations.run(pkt, repo, inputs, session_id=session_of(cmd), trace_id=bind_trace())
        )

    elif cmd.type == "delegate.discard":
        task_id = str(cmd.payload.get("task_id") or "")
        if task_id:
            await st.delegations.discard(task_id)

    elif cmd.type == "halt":
        # Real now, not a stub. Order matters: flip policy to deny-all FIRST, so a
        # task that is mid-flight cannot slip one more tool call through while we are
        # still cancelling (docs/SECURITY.md#emergency-stop-halt).
        reason = str(cmd.payload.get("reason", "user requested halt"))
        st.policy.halt(reason)
        st.audit.append(actor="user", tool="oracle.halt", decision="halt", reason=reason)
        # Kill the whole tool process tree. This is the part that makes HALT real:
        # cancelling tasks does not kill `npm install`'s grandchildren, the job does.
        await st.host.kill_tree()
        # The shells die with the job; stop polling for output that will never
        # come rather than logging a failure per session per 120 ms.
        await st.terminals.stop()
        # An approval left live after a stop is a click that executes something nobody
        # is watching for any more.
        refused = await st.approvals.refuse_all(reason)
        for task in list(st.tasks):
            task.cancel()
        st.agent.halted = True
        await st.eventlog.append(
            Event(
                type="agent.state",
                trace_id=bind_trace(),
                payload={"state": "halted", "reason": reason, "approvals_refused": refused},
            )
        )

    elif cmd.type == "resume":
        # Never automatic. A human decides when a halt is over.
        st.policy.resume()
        st.agent.halted = False
        # HALT cancelled the knowledge watcher along with everything else. Resuming is a
        # human decision, and this is the point at which the machine is allowed to work
        # on the user's behalf again.
        _start_indexing(st)
        st.audit.append(actor="user", tool="oracle.resume", decision="resume")
        await st.eventlog.append(
            Event(type="agent.state", trace_id=bind_trace(), payload={"state": "idle"})
        )

    else:
        log.info("ws.unknown_command", type=cmd.type)
