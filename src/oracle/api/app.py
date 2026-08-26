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
from fastapi import APIRouter, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect

from oracle import __version__
from oracle.config import Settings, get_settings
from oracle.core import briefing as briefing_mod
from oracle.core.approvals import ApprovalStore
from oracle.core.eventlog import EventLog
from oracle.core.events import PROTOCOL_VERSION, ClientCommand, Event, new_id
from oracle.core.project_state import ProjectStore, effective_status, observe
from oracle.core.projects import discover_projects
from oracle.core.sessions import SessionStore
from oracle.core.terminal import TerminalBridge
from oracle.core.unfinished import derive, objective_of, question_for
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
from oracle.memory import FactKind, FactScope, FactSource, MemoryStore, WriteContext, rows_of
from oracle.memory.attempts import from_task
from oracle.orchestration.models import TaskKind
from oracle.orchestration.plan import ExecutionPlan, compile_plan, parse
from oracle.orchestration.plan import validate as plan_validate
from oracle.orchestration.recovery import recover
from oracle.orchestration.registry import Registry, load_registry
from oracle.orchestration.service import GraphService
from oracle.orchestration.store import TaskStore
from oracle.orchestration.templates import Templates, load_templates
from oracle.pipelines.loader import Loaded
from oracle.pipelines.loader import discover as discover_pipelines
from oracle.policy.audit import AuditLog
from oracle.policy.engine import PolicyEngine, load_policy
from oracle.router.intent import IntentClassifier
from oracle.router.pipeline import TurnPipeline
from oracle.router.selection import ToolSelector
from oracle.runners import build_runners
from oracle.runners.planning import (
    Planner,
    PlanSource,
    approve_graph,
    audit_overrides,
    make_replanner,
    plan_with_ladder,
)
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
    #: The durable task graph (ORCHESTRATION.md §2). Present from P7-T2 so recovery can
    #: read it at startup; the scheduler that writes to it is created per graph run, and
    #: nothing creates graphs until P8 routes an intent to one.
    task_store: TaskStore
    #: Projects as durable entities (PROJECT_STATE.md, ADR-0024). Holds only what git
    #: cannot answer — what ORACLE attempted here, what it cost, where the briefing
    #: resumes. Branch and dirty count are read fresh through `observe()` and are
    #: deliberately absent from the row.
    project_store: ProjectStore
    #: Live task graphs, addressable by root id, so a person can stop one
    #: (ORCHESTRATION.md §3).
    graphs: GraphService
    #: The capability registry: which agent may hold which role (PLANNER.md §5). Named
    #: `agents` because `registry` is the tool registry — two different things, and one
    #: of them decides who may execute.
    agents: Registry
    #: Rung 2 of the planner ladder: the shapes ORACLE uses when no model will produce a
    #: plan (PLANNER.md §6). Data, loaded beside the registry.
    templates: Templates
    #: Named workflows, by name (PIPELINES.md §2). Data as well: a pipeline is a file a
    #: human wrote, and running one compiles it to an ordinary task graph rather than
    #: reaching a second executor.
    pipelines: dict[str, Loaded]
    #: What ORACLE learned, as opposed to what it can look up (MEMORY.md). Facts,
    #: preferences and prior attempts — the last of which is what stops a delegate
    #: repeating last week's dead end.
    memory: MemoryStore
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


def _packet_translator(st: AppState) -> Callable[[str], str | None] | None:
    """The synchronous seam `retrieve()` takes, bridged onto the async provider.

    `_curate` runs in a worker thread (`to_thread`), so reaching an async provider means
    scheduling onto the loop that owns it and waiting from the thread. That is the whole
    of the machinery here, and it is deliberately confined to this function: nothing in
    `rag/` learns that an LLM exists — it receives a string or a `None`.

    Returns `None` — meaning "do not translate at all" — when the provider is absent or
    the switch is off, so the caller has one degradation path rather than two.
    """
    provider = st.provider
    if provider is None or not st.settings.translate_queries:
        return None

    from oracle.rag.translate import DEFAULT_TIMEOUT_S, looks_translatable, translate_to_english

    loop = asyncio.get_running_loop()

    def translate(question: str) -> str | None:
        # The cheap test first: an all-Latin goal has nothing to gain from a second
        # all-Latin probe, and this is the common case on this corpus.
        if not looks_translatable(question):
            return None
        future = asyncio.run_coroutine_threadsafe(translate_to_english(question, provider), loop)
        try:
            # `translate_to_english` owns the real deadline; this one only exists so a
            # loop that has stopped cannot park a worker thread forever.
            return future.result(timeout=DEFAULT_TIMEOUT_S + 5)
        except Exception as exc:
            future.cancel()
            log.info("delegate.translation_unavailable", reason=str(exc)[:200])
            return None

    return translate


def _curate(
    st: AppState,
    repo: Path,
    project: str,
    task_text: str,
    translator: Callable[[str], str | None] | None = None,
) -> PacketInputs:
    """§6 curation for a delegation: orientation docs, retrieval scoped to the project,
    git state. Runs off the event loop (`to_thread`) — the embedder alone is seconds.

    Degradable on purpose: no knowledge index or no embedding model means a thinner
    packet (docs + git state), never a failed delegation. The taint from retrieval
    provenance rides into `PacketInputs`, so the egress approval escalates when the
    packet carries `local_foreign` text (SECURITY.md §6).

    `translator` is built by the caller (`_packet_translator`) because it needs the
    running loop, and defaults to `None` so every existing caller — and every test —
    keeps the untranslated behaviour."""
    from oracle.handoff.gather import gather_project_docs, gather_retrieval

    excerpts = list(gather_project_docs(repo))
    tainted: tuple[str, ...] = ()
    try:
        from oracle.rag.embedding import DEFAULT, Embedder
        from oracle.rag.store import KnowledgeStore

        store = KnowledgeStore(st.settings.data_dir / "knowledge.db", DEFAULT.out_dim)
        try:
            store.bind(DEFAULT.name, DEFAULT.out_dim)
            hits, tainted = gather_retrieval(
                task_text, store, Embedder(DEFAULT), project=project, translator=translator
            )
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

    task_store = TaskStore(conn)
    project_store = ProjectStore(conn)
    memory = MemoryStore(conn, eventlog)
    agent_registry = load_registry(settings.registry_path)
    plan_templates = load_templates(settings.plan_templates_path)
    # Discovered at boot like the registry and the templates, and just as fail-open: a
    # broken pipeline file is a `Problem` in the log, not a daemon that will not start,
    # and the pipelines that *do* parse stay available (PIPELINES.md §2).
    pipeline_index, pipeline_problems = discover_pipelines(
        config_dir=settings.pipelines_dir,
        projects_root=settings.projects_root,
        projects=tuple(projects),
    )
    log.info(
        "pipelines.loaded",
        count=len(pipeline_index),
        names=sorted(pipeline_index),
        problems=len(pipeline_problems),
    )
    for problem in pipeline_problems:
        log.warning("pipelines.problem", detail=str(problem))
    if not agent_registry.usable:
        # Same instinct as policy's read-only lockdown: a registry that failed open would
        # let a plan pick its own executor. Planning simply becomes unavailable.
        log.warning("registry.unusable", problem=agent_registry.problem)
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
            # Names the pre-router will recognise deterministically, with no model in the
            # loop (PIPELINES.md §5).
            pipelines=frozenset(pipeline_index),
            stats=stats,
            executor=executor,
            # Selection needs the model. Without one the pipeline still routes and
            # still refuses clearly — it just cannot choose a tool.
            selector=ToolSelector(registry, provider, stats=stats) if provider else None,
            approvals=approvals,
            projects_root=settings.projects_root,
            # The `delegate` intent and the escalation signal both need this; without
            # it the pipeline still routes and simply says delegation is not wired.
            delegations=delegations,
            # Band 5. Without it every answer is assembled exactly as it was before
            # Phase 9 — the rollback MEMORY.md promises, and a test asserts it.
            memory=memory,
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
        task_store=task_store,
        project_store=project_store,
        graphs=GraphService(eventlog, task_store),
        agents=agent_registry,
        templates=plan_templates,
        pipelines=pipeline_index,
        memory=memory,
        tokens=tokens,
        mcp=mcp,
        schema_version=version,
        projects=projects,
    )
    state.agent.degraded = degraded
    # A delegation started by a turn outlives it, and must still be cancellable: routing
    # it through `AppState.spawn` means HALT reaches it like every other tracked task.
    # Assigned after construction because the pipeline is built inside the state.
    state.agent.spawn = state.spawn

    def _start_pipeline(name: str, session_id: str | None, trace: str) -> None:
        """The pre-router's hook, assigned here because it needs the finished state.

        Runs with the pipeline's own declared defaults: a name typed into the chat has no
        parameters attached, and every parameter has a default precisely so a run needs
        no arguments (PIPELINES.md §2)."""
        loaded = state.pipelines.get(name)
        if loaded is not None:
            state.spawn(_run_pipeline(state, loaded, {}, session_id, trace))

    def _start_continue(project: str, session_id: str | None, trace: str) -> None:
        """The router's `continue` hook, assigned here for the same reason as the
        pipeline one: the derivation needs the task table, the gate and a planner, and
        this is the one place allowed to see all three."""
        state.spawn(_continue_project(state, project, session_id, trace))

    state.agent.run_pipeline = _start_pipeline
    state.agent.continue_work = _start_continue
    return state


async def _continue_project(st: AppState, project: str, session_id: str | None, trace: str) -> None:
    """ "Continue Asterim." — read the state, or ask.

    Three things happen here that cannot happen in the router
    ([PROJECT_STATE.md §5](../../docs/PROJECT_STATE.md)):

    1. **The project is registered on first use.** Naming a project in a `continue` is
       the human act that registration requires — not auto-discovery, which is why
       `discover_projects()` alone never creates a row. Registration happens *before* the
       derivation, so it also happens when the answer turns out to be a question: the row
       records that this is a project someone cares about, and `last_touched` stays null
       because no work was done. Registration grants nothing (ADR-0024).
    2. **An empty derivation asks.** No open tasks and no task document means the honest
       answer is a question. Handing a planner a project name and nothing else produces
       plausible work, and plausible work costs a worktree and a delegation to falsify.
    3. **Repo task documents are named on the approval card.** They are `local_foreign`,
       and the honest consequence is **attribution, not escalation**: the graph approval
       already evaluates as `Provenance.EXTERNAL` at T2 (ADR-0021), so there is no
       further tier to rise to and claiming one would be theatre. What the person gains
       is knowing *which file* helped write the objective they are being asked to approve.
    """
    # The router only ever hands over a name from `st.projects`, but the invariant is
    # worth holding locally: a name from anywhere else would become a filesystem path
    # and then a registry row.
    if project not in st.projects:
        log.warning("continue.unknown_project", project=project)
        return
    root = (st.settings.projects_root / project).resolve()
    if not root.is_relative_to(st.settings.projects_root.resolve()):
        log.warning("continue.escapes_root", project=project)
        return

    tracked = await st.project_store.by_name(project) or await st.project_store.register(
        project, root
    )
    unfinished = await derive(st.conn, st.executor, tracked.name, tracked.root)
    objective = objective_of(unfinished)

    if objective is None:
        await st.eventlog.append(
            Event(
                type="message.completed",
                session_id=session_id,
                trace_id=trace,
                actor="assistant",
                payload={"text": question_for(tracked.name)},
            )
        )
        log.info("continue.nothing_to_do", project=tracked.name)
        return

    await st.project_store.touch(tracked.id)
    await st.eventlog.append(
        Event(
            type="continue.derived",
            session_id=session_id,
            trace_id=trace,
            actor="system",
            payload={
                "project": tracked.name,
                "open_tasks": len(unfinished.tasks),
                "dropped": unfinished.dropped,
                # Named, so the approval card can say WHERE the untrusted half came from
                # rather than only that there was one.
                "notes": [n.path for n in unfinished.notes],
                "tainted": unfinished.tainted,
            },
        )
    )
    await _plan_and_run(
        st,
        objective,
        session_id,
        trace,
        intent="continue",
        project=tracked.name,
        untrusted_sources=[n.path for n in unfinished.notes],
    )


async def _announce_boot(st: AppState) -> None:
    """Record that the daemon started, and whether the previous run ended cleanly.

    A crash leaves no trace of itself — the log simply stops — and a silent gap is
    indistinguishable from an idle night. So the fact is established here, once, by
    looking at what the last event *was*: a `system.shutdown` means somebody stopped it,
    anything else means it died. That is what lets the briefing say "ORACLE stopped
    unexpectedly at 04:12" instead of saying nothing at all (ADR-0025).
    """
    async with st.conn.execute("SELECT type, ts FROM events ORDER BY seq DESC LIMIT 1") as cur:
        row = await cur.fetchone()
    last_event = str(row["type"]) if row is not None else None
    last_seen = str(row["ts"]) if row is not None else None
    # A first-ever boot has no previous run and is therefore not unclean — the absence of
    # a shutdown only means something when there was a start to go with it.
    unclean = last_event is not None and last_event != "system.shutdown"
    await st.eventlog.append(
        Event(
            type="system.boot",
            trace_id=bind_trace(),
            actor="system",
            payload={
                "unclean": unclean,
                "last_event": last_event,
                "last_seen": last_seen,
                "schema_version": st.schema_version,
            },
        )
    )
    if unclean:
        log.warning("oracled.unclean_previous_run", last_event=last_event, last_seen=last_seen)


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
        # Before anything else runs: a graph left mid-flight by a crash is reported and
        # nothing is resumed (ORCHESTRATION.md §3). Awaited rather than spawned — starting
        # new work while unexplained work sits in the table is the failure mode the rule
        # exists to prevent.
        found = await recover(st.task_store, st.eventlog)
        if found.gated:
            log.warning(
                "oracled.recovered",
                interrupted=[t.id for t in found.interrupted],
                action="nothing restarted; a human decides",
            )
        await _announce_boot(st)
        # Projects, reconciled against the disk and against the task table. Both are
        # cheap — one `is_dir()` per row, one indexed scan per project — and both repair
        # rather than trust: a root deleted while the daemon was down becomes MISSING
        # instead of a crash the first time something renders it, and counters are a
        # projection whose stored value is never authoritative (PROJECT_STATE.md §3).
        gone = await st.project_store.refresh_presence()
        await st.project_store.recount_all()
        if gone:
            log.warning("projects.presence_changed", projects=[p.name for p in gone])
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
            # Before anything is torn down, and best-effort: a shutdown that cannot be
            # recorded is exactly the case the next boot must be able to notice, so a
            # failure here is logged and swallowed rather than masking the stop.
            with contextlib.suppress(Exception):
                await st.eventlog.append(
                    Event(type="system.shutdown", trace_id=bind_trace(), actor="system", payload={})
                )
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
            # The named workflows this daemon found (PIPELINES.md §5). Sent so the palette
            # can offer them by name and so a person can see what ORACLE actually
            # discovered — the commonest pipeline bug is a file in the wrong directory,
            # and "it is not in this list" answers that in one glance.
            "pipelines": [
                {
                    "name": name,
                    "description": loaded.pipeline.description,
                    "project": loaded.project,
                    "source": loaded.source,
                    "steps": len(loaded.pipeline.steps),
                }
                for name, loaded in sorted(st.pipelines.items())
            ],
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
        # `DEFAULT`, not a named spec: this endpoint exists to say whether the index on
        # disk matches the model this build would use, and hardcoding one turns a model
        # switch into a health view that lies.
        from oracle.rag.embedding import DEFAULT
        from oracle.rag.store import KnowledgeStore, SchemaMismatch

        st = state_of(app)
        path = st.settings.data_dir / "knowledge.db"
        if not path.exists():
            return {"built": False, "path": str(path), "model": DEFAULT.name}
        try:
            store = KnowledgeStore(path, DEFAULT.out_dim)
            try:
                store.bind(DEFAULT.name, DEFAULT.out_dim)
                return {"built": True, "model": DEFAULT.name, **store.stats()}
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

    @app.get("/api/v1/tasks")
    async def task_graph(root_id: str = Query(..., min_length=1)) -> dict[str, Any]:
        """One graph as a tree (ORCHESTRATION.md §6). A projection over `tasks`, not a
        second source of truth: the WS `task.*` stream keeps a client live, and this is
        what it reconciles against on connect."""
        st = state_of(app)
        return await st.graphs.tree(root_id)

    @app.get("/api/v1/briefing")
    async def briefing() -> dict[str, Any]:
        """What changed since I last acknowledged (PROJECT_STATE.md §6).

        **Rendering does not advance the watermark.** Glancing at the screen and walking
        away must leave the briefing intact — one that clears itself on sight is a
        notification, and notifications are how people miss things.

        `through_seq` is pinned to the log head at this moment and echoed back on
        acknowledgement, so work arriving while the reader is looking cannot be marked
        seen by an acknowledgement of what they actually saw.

        No model is called. Every number is arithmetic over task rows.
        """
        st = state_of(app)
        built = await briefing_mod.build(
            st.conn, st.project_store, through_seq=st.eventlog.last_seq
        )
        return briefing_mod.wire(built)

    @app.post("/api/v1/briefing/ack")
    async def briefing_ack(
        through_seq: int = Query(..., ge=0),
        project_id: str = Query("", description="one project, or empty for all"),
    ) -> dict[str, Any]:
        """The only thing that advances the watermark.

        Monotonic: a stale client acknowledging an old sequence cannot rewind a pointer a
        later acknowledgement already advanced, which would re-show work already seen.
        """
        st = state_of(app)
        if project_id and await st.project_store.get(project_id) is None:
            raise HTTPException(status_code=404, detail=f"no such project: {project_id!r}")
        await briefing_mod.acknowledge(
            st.conn,
            st.project_store,
            through_seq=through_seq,
            project_id=project_id or None,
        )
        return {"acknowledged_through": through_seq, "project_id": project_id or None}

    @app.get("/api/v1/projects")
    async def list_projects(
        include_archived: bool = Query(False),
    ) -> dict[str, Any]:
        """Registered projects, plus the directories that are only candidates.

        The split is the point (PROJECT_STATE.md §3): `discover_projects()` lists what is
        on disk, and this machine's projects root holds `New folder` and `docs.zip`
        alongside the real ones. Registration is an explicit human act, so the UI needs to
        show both — what ORACLE tracks, and what it could be asked to track.

        No `git` runs here. A list of twenty projects would be twenty subprocesses on a
        page-load, and the fields that would need them (branch, dirty count) belong to the
        per-project read below, which the sidebar calls lazily per row.
        """
        st = state_of(app)
        tracked = await st.project_store.all(include_archived=include_archived)
        known = {p.name for p in tracked}
        return {
            "projects": [
                {
                    "id": p.id,
                    "name": p.name,
                    "root": str(p.root),
                    # Corrected by a fresh existence check: a directory deleted since boot
                    # must not be reported as `idle` (PROJECT_STATE.md §2).
                    "status": str(effective_status(p)),
                    "description": p.description,
                    "description_source": str(p.description_source),
                    "first_seen": p.first_seen,
                    "last_touched": p.last_touched,
                    "briefed_through_seq": p.briefed_through_seq,
                    "open_tasks": p.open_tasks,
                    "failed_tasks": p.failed_tasks,
                    "tokens_spent": p.tokens_spent,
                    "usd_spent": p.usd_spent,
                }
                for p in tracked
            ],
            "candidates": [name for name in st.projects if name not in known],
            "projects_root": str(st.settings.projects_root),
        }

    @app.post("/api/v1/projects")
    async def register_project(
        name: str = Query(..., min_length=1), description: str = Query("")
    ) -> dict[str, Any]:
        """Register a discovered directory as a project.

        `name` must be one `discover_projects()` actually found. That is the same rule the
        intent classifier follows and it is a safety rule, not a convenience: a name that
        is not in the candidate list would become a filesystem path assembled from a
        request body. Registration grants nothing — scopes live in `config/policy.yaml`,
        where a human edits them and git records the edit.
        """
        st = state_of(app)
        if name not in st.projects:
            raise HTTPException(
                status_code=404, detail=f"no such directory under the projects root: {name!r}"
            )
        root = (st.settings.projects_root / name).resolve()
        if not root.is_relative_to(st.settings.projects_root.resolve()):
            # Belt and braces: `name` is already constrained to the discovered list, so
            # this is unreachable unless that list is ever widened.
            raise HTTPException(status_code=400, detail="project name escapes the projects root")
        project = await st.project_store.register(name, root, description=description)
        return {"id": project.id, "name": project.name, "status": str(project.status)}

    @app.get("/api/v1/projects/{project_id}")
    async def project_detail(project_id: str) -> dict[str, Any]:
        """One project: the stored half, and the observed half read fresh.

        `observation` is never cached and never persisted. If it were, the branch name in
        the sidebar would be wrong the moment someone switched branches in their editor,
        with no event that could correct it (PROJECT_STATE.md §2).
        """
        st = state_of(app)
        project = await st.project_store.get(project_id)
        if project is None:
            raise HTTPException(status_code=404, detail=f"no such project: {project_id!r}")
        obs = await observe(st.executor, project)
        return {
            "id": project.id,
            "name": project.name,
            "root": str(project.root),
            "status": str(effective_status(project)),
            "description": project.description,
            "description_source": str(project.description_source),
            "first_seen": project.first_seen,
            "last_touched": project.last_touched,
            "briefed_through_seq": project.briefed_through_seq,
            "open_tasks": project.open_tasks,
            "failed_tasks": project.failed_tasks,
            "tokens_spent": project.tokens_spent,
            "usd_spent": project.usd_spent,
            "observation": {
                "branch": obs.branch,
                "upstream": obs.upstream,
                "ahead": obs.ahead,
                "behind": obs.behind,
                "dirty": obs.dirty,
                "clean": obs.clean,
                "last_commit": list(obs.last_commit) if obs.last_commit else None,
                "kinds": [str(k) for k in obs.detected.kinds] if obs.detected else [],
                "test": [t.display() for t in obs.detected.test] if obs.detected else [],
                "agent_docs": list(obs.agent_docs),
                "error": obs.error,
            },
        }

    @app.get("/api/v1/memory")
    async def memory_view(
        project: str = Query("", description="scope_ref for project facts"),
        include_superseded: bool = Query(True),
    ) -> dict[str, Any]:
        """The Memory view's query (MEMORY.md §6).

        Superseded rows are included **by default**: a person auditing what ORACLE
        believes needs to see what it stopped believing, and "why does it think that?"
        is only answerable if the chain is visible."""
        st = state_of(app)
        facts = await st.memory.all_facts()
        if not include_superseded:
            facts = [f for f in facts if f.live]
        if project:
            facts = [f for f in facts if f.scope_ref in (project, None)]
        return {"facts": rows_of(facts)}

    @app.get("/api/v1/memory/attempts")
    async def memory_attempts(
        goal: str = Query("", min_length=0), project: str = Query("")
    ) -> dict[str, Any]:
        """What has been tried, for a goal or for a project. The same lookup the packet
        renderer uses, exposed so a person can see what a worker will be told."""
        from oracle.memory.attempts import DEFAULT_LIMIT, match, signature

        st = state_of(app)
        if goal:
            found = await st.memory.attempts_for(signature(goal, project), project=project)
            if not found:
                found = match(goal, await st.memory.attempts_in(project), limit=DEFAULT_LIMIT)
        else:
            found = await st.memory.attempts_in(project, limit=50)
        return {"attempts": [a.model_dump() for a in found]}

    @app.websocket("/api/v1/stream")
    async def stream(ws: WebSocket, since_seq: int = Query(0, ge=0)) -> None:
        await _ws_handler(app, ws, since_seq)


async def _plan_and_run(
    st: AppState,
    objective: str,
    session_id: str | None,
    trace: str,
    *,
    intent: str | None = None,
    project: str | None = None,
    untrusted_sources: list[str] | None = None,
) -> None:
    """Plan, ask, compile, ask again, run — and, if something fails, ask once more.

    Each step's refusal is a full stop, not a fallback to doing it anyway. The replanner
    is built here, beside the runners, for the same reason they are: this is the one place
    allowed to see both the supervisor and the things that spend money."""
    planner = Planner(
        ClaudeCodeAdapter(),
        st.approvals,
        st.policy,
        st.agents,
        projects=set(st.projects),
        session_id=session_id,
    )
    ladder = await plan_with_ladder(
        planner,
        st.templates,
        st.agents,
        set(st.projects),
        objective,
        trace_id=trace,
        intent=intent,
        project=project,
        eventlog=st.eventlog,
        session_id=session_id,
    )
    if ladder.plan is None:
        log.info(
            "graph.not_planned",
            refused=ladder.refused,
            attempts=ladder.attempts,
            descents=len(ladder.descents),
            problems=ladder.problems[:3],
        )
        return
    await _run_plan(
        st,
        ladder.plan,
        objective,
        session_id,
        trace,
        source=ladder.source or PlanSource.PLANNER,
        descents=ladder.descents,
        planner=planner,
        untrusted_sources=untrusted_sources,
    )


async def _run_plan(
    st: AppState,
    plan: ExecutionPlan,
    objective: str,
    session_id: str | None,
    trace: str,
    *,
    source: PlanSource,
    descents: list[dict[str, Any]] | None = None,
    planner: Planner | None = None,
    untrusted_sources: list[str] | None = None,
) -> None:
    """Compile, ask, run — identical for every rung of the ladder and for a plan a person
    wrote. That sameness is the point: a degraded mode with its own path is a degraded
    mode nobody has tested.

    A replan needs a planner whatever authored the *first* plan, so one is built here when
    the caller has none. If none is reachable, replanning simply produces nothing — the
    same answer the ladder already gave, one level down."""
    planner = planner or Planner(
        ClaudeCodeAdapter(),
        st.approvals,
        st.policy,
        st.agents,
        projects=set(st.projects),
        session_id=session_id,
    )
    plan_id = new_id("pl")
    # Every hint the registry refused, on the audit chain before anybody is asked to
    # approve the graph those hints were trying to steer.
    audit_overrides(st.audit, plan, st.agents, trace_id=trace)
    graph = compile_plan(plan, st.agents, plan_id=plan_id)
    if not await approve_graph(
        st.approvals,
        st.policy,
        graph,
        plan,
        trace_id=trace,
        session_id=session_id,
        source=source,
        descents=descents,
        untrusted_sources=untrusted_sources,
    ):
        log.info("graph.not_approved", root_id=graph.root_id, plan_id=plan_id, source=str(source))
        return

    async def exhausted(report: dict[str, Any]) -> None:
        """The budget ran out. ORCHESTRATION.md §4: the root fails with a report of
        everything tried — including the branches the partial work was harvested onto, so
        the keep/discard decision has something to point at rather than a shrug."""
        await st.eventlog.append(
            Event(
                type="graph.replan_exhausted",
                session_id=session_id,
                task_id=graph.root_id,
                trace_id=trace,
                payload={"root_id": graph.root_id, "source": "graph", **report},
            )
        )

    replan = make_replanner(
        planner,
        st.approvals,
        st.policy,
        st.agents,
        # From the table, not from the scheduler's memory: the budget is counted off the
        # durable record, which is the one both a restarted daemon and a reconnecting
        # client can also read.
        lambda: st.task_store.load_graph(graph.root_id),
        objective=objective,
        trace_id=trace,
        session_id=session_id,
        on_exhausted=exhausted,
    )
    await st.graphs.run(
        graph, build_runners(st), replan=replan, session_id=session_id, trace_id=trace
    )
    await _record_attempts(st, graph.root_id, trace)


async def _run_pipeline(
    st: AppState,
    loaded: Loaded,
    params: dict[str, Any],
    session_id: str | None,
    trace: str,
) -> None:
    """One pipeline run, with its boundary announced and its record kept.

    The two `pipeline.*` events are the only new event types Phase 10 adds, and neither
    is a `task.*`: the steps emit ordinary task events because they *are* ordinary tasks.
    A consumer needs no new vocabulary to render a run — `TaskTree` already does it.
    """
    from oracle.runners.pipeline import PipelineService

    service = PipelineService(
        st.graphs,
        st.executor,
        st.approvals,
        st.policy,
        projects_root=st.settings.projects_root,
    )
    await st.eventlog.append(
        Event(
            type="pipeline.started",
            session_id=session_id,
            trace_id=trace,
            actor="user",
            payload={"pipeline": loaded.pipeline.name, "source": loaded.source, "params": params},
        )
    )
    record = await service.run(
        loaded,
        params,
        runners_for=lambda granted: build_runners(st, pre_granted=granted),
        session_id=session_id,
        trace_id=trace,
    )
    await st.eventlog.append(
        Event(
            type="pipeline.finished",
            session_id=session_id,
            trace_id=trace,
            actor="system",
            payload=record,
        )
    )
    if record.get("root_id"):
        await _record_attempts(st, str(record["root_id"]), trace)


async def _record_attempts(st: AppState, root_id: str, trace: str) -> None:
    """Every task that ran becomes a durable attempt (MEMORY.md §4).

    **After the graph, not during it.** An attempt is a record rather than a belief, so
    the write policy does not gate it — but recording mid-run would mean a task could
    read a record of itself, and "what has been tried" is a question about finished work.
    The rows are read back from the store rather than from the scheduler's memory,
    because the row is the record (ORCHESTRATION.md §2).

    A failure here loses a memory, not a result: the graph has already finished and its
    evidence is in the task table either way."""
    try:
        for task in await st.task_store.load_graph(root_id):
            if task.terminal and task.kind is not TaskKind.PLANNING:
                await st.memory.record_attempt(from_task(task), trace_id=trace)
    except Exception:
        log.warning("memory.attempts_not_recorded", root_id=root_id, exc_info=True)


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


def _optional(value: Any) -> str | None:
    """A payload field that may be absent, null, or empty - all three meaning "not
    stated" rather than "stated as empty"."""
    text = str(value or "").strip()
    return text or None


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
        inputs = await asyncio.to_thread(
            _curate, st, repo, project, task_text, _packet_translator(st)
        )
        st.spawn(
            st.delegations.run(pkt, repo, inputs, session_id=session_of(cmd), trace_id=bind_trace())
        )

    elif cmd.type == "delegate.discard":
        task_id = str(cmd.payload.get("task_id") or "")
        if task_id:
            await st.delegations.discard(task_id)

    elif cmd.type == "pipe.run":
        # A named workflow a human wrote, run as a task graph (PIPELINES.md §3).
        #
        # Not a registered tool, and deliberately: a tool that starts a whole graph would
        # be unbounded work behind one tier, and `PlannedTask(extra="forbid")` plus
        # ADR-0021 already stop a *plan* from naming one. A pipeline is started by a
        # person, or by the deterministic pre-router match — never by a model.
        name = str(cmd.payload.get("name") or "").strip()
        loaded = st.pipelines.get(name)
        if loaded is None:
            await st.eventlog.append(
                Event(
                    type="error",
                    session_id=session_of(cmd),
                    trace_id=bind_trace(),
                    actor="system",
                    payload={"error": f"no pipeline named {name!r}", "known": sorted(st.pipelines)},
                )
            )
        else:
            raw_params = cmd.payload.get("params")
            st.spawn(
                _run_pipeline(
                    st,
                    loaded,
                    dict(raw_params) if isinstance(raw_params, dict) else {},
                    session_of(cmd),
                    bind_trace(),
                )
            )

    elif cmd.type == "graph.plan":
        # An objective in, a graph out — with two questions in between, both the owner's:
        # the planning egress, and the shape of what came back (PLANNER.md, ORCHESTRATION
        # §5). Spawned so HALT reaches it like everything else.
        #
        # No registry check here any more: the ladder is the check. An unusable registry
        # means rung 1 is skipped and rungs 2 and 3 fail validation, which produces a
        # reason in the log instead of a silent refusal - and, importantly, no egress.
        objective = str(cmd.payload.get("objective") or "").strip()
        if objective:
            st.spawn(
                _plan_and_run(
                    st,
                    objective,
                    session_of(cmd),
                    bind_trace(),
                    intent=_optional(cmd.payload.get("intent")),
                    project=_optional(cmd.payload.get("project")),
                )
            )
        else:
            log.warning("graph.plan_refused", reason="no objective")

    elif cmd.type == "graph.submit_plan":
        # Rung 4: the person writes the plan (PLANNER.md §6). It is parsed and validated
        # by exactly the same functions a vendor's plan is - there is no privileged path
        # for a plan a human typed, because "the author is trusted" is precisely the
        # control ADR-0021 says never to build.
        objective = str(cmd.payload.get("objective") or "").strip()
        plan, problems = parse(cmd.payload.get("plan"))
        if plan is not None:
            problems = plan_validate(plan, st.agents, set(st.projects))
        if plan is None or problems:
            log.warning("graph.submitted_plan_invalid", problems=problems[:5])
            await st.eventlog.append(
                Event(
                    type="plan.rejected",
                    session_id=session_of(cmd),
                    trace_id=bind_trace(),
                    actor="user",
                    payload={"authored_by": str(PlanSource.HUMAN), "problems": problems[:10]},
                )
            )
            return
        st.spawn(
            _run_plan(
                st,
                plan,
                objective or plan.objective,
                session_of(cmd),
                bind_trace(),
                source=PlanSource.HUMAN,
            )
        )

    elif cmd.type == "memory.remember":
        # The owner stating a fact, which is rule 1 of MEMORY.md §3's write policy and
        # the only source that needs no corroboration. `plan_active` is read from the
        # daemon rather than from the payload: a client cannot talk its way past the
        # mid-plan rule by omitting a flag.
        key = str(cmd.payload.get("key") or "").strip()
        value = str(cmd.payload.get("value") or "").strip()
        if not key or not value:
            log.warning("memory.remember_refused", reason="key and value are both required")
            return
        correcting = bool(cmd.payload.get("correcting"))
        # Not `project`: that name is already bound in this handler by `delegate.start`,
        # and a rebind here would be a scope bug wearing a readable name.
        scope_ref = _optional(cmd.payload.get("project"))
        outcome = await st.memory.remember(
            key,
            value,
            context=WriteContext(
                source=FactSource.USER_CORRECTED if correcting else FactSource.USER_STATED,
                plan_active=bool(st.graphs.running),
            ),
            kind=FactKind.PREFERENCE if cmd.payload.get("preference") else FactKind.FACT,
            scope=FactScope.PROJECT if scope_ref else FactScope.GLOBAL,
            scope_ref=scope_ref,
            origin=session_of(cmd) or "",
            trace_id=bind_trace(),
        )
        log.info("memory.remember", key=key, outcome=type(outcome).__name__)

    elif cmd.type == "memory.forget":
        # The undo button (MEMORY.md §6). The only deletion in the subsystem, and always
        # a person's: nothing inside ORACLE calls it.
        fact_id = str(cmd.payload.get("fact_id") or "")
        if fact_id:
            await st.memory.forget(
                fact_id, reason=str(cmd.payload.get("reason") or ""), trace_id=bind_trace()
            )

    elif cmd.type == "graph.cancel":
        # One task, or the whole graph. Not HALT: HALT is above this and stops
        # everything, including graphs this daemon never started.
        root_id = str(cmd.payload.get("root_id") or "")
        task_id = str(cmd.payload.get("task_id") or "")
        if root_id and task_id:
            await st.graphs.cancel_task(root_id, task_id)
        elif root_id:
            await st.graphs.cancel_root(root_id)

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
