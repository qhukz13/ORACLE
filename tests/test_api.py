"""End-to-end over the real ASGI app: REST, WS streaming, and reconnect-with-resume.

`test_reconnect_after_disconnect_has_no_gaps_or_duplicates` is the P0 acceptance
criterion for "kill the backend mid-session and catch up".
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from oracle.api.app import create_app
from oracle.config import Settings


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as c:
        yield c


def test_health_needs_no_state(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "ok"}


def test_status_reports_reality_not_fiction(client: TestClient) -> None:
    body = client.get("/api/v1/status").json()
    assert body["protocol"] == 1
    assert body["schema_version"] >= 1
    # With the LLM disabled the status says so rather than implying a live model.
    assert body["agent"]["kind"] == "router"
    assert body["agent"]["degraded"]
    assert body["agent"]["structured_output"]["attempts"] == 0


def test_slash_command_works_without_a_model(client: TestClient) -> None:
    """The degraded-mode guarantee (ADR-0011): with no LLM, deterministic paths still
    answer. This client fixture has llm_enabled=False."""
    with client.websocket_connect("/api/v1/stream?since_seq=0") as ws:
        ws.send_json({"type": "session.message", "payload": {"text": "/help"}})
        reply = ""
        for _ in range(40):
            ev = ws.receive_json()
            if ev["type"] == "message.completed":
                reply = str(ev["payload"]["text"])
            if ev["type"] == "turn.finished":
                assert ev["payload"]["route"] == "pre-router"
                break
    assert "/status" in reply and "/halt" in reply


def test_chat_without_a_model_degrades_clearly(client: TestClient) -> None:
    with client.websocket_connect("/api/v1/stream?since_seq=0") as ws:
        ws.send_json({"type": "session.message", "payload": {"text": "why is auth broken"}})
        outcome, reply = None, ""
        for _ in range(40):
            ev = ws.receive_json()
            if ev["type"] == "message.completed":
                reply = str(ev["payload"]["text"])
            if ev["type"] == "turn.finished":
                outcome = ev["payload"]["outcome"]
                break
    assert outcome == "degraded"
    assert "offline" in reply.lower()


def test_message_produces_the_full_event_sequence(client: TestClient) -> None:
    with client.websocket_connect("/api/v1/stream?since_seq=0") as ws:
        ws.send_json({"type": "session.message", "payload": {"text": "/status"}})
        types: list[str] = []
        for _ in range(40):
            ev = ws.receive_json()
            types.append(ev["type"])
            if ev["type"] == "turn.finished":
                break

    # `since_seq=0` replays the whole log, and since 2026-08-26 the first thing in a
    # fresh one is `system.boot` — the daemon announcing itself so the next start can
    # tell a clean stop from a crash (PROJECT_STATE.md §6). What this test is actually
    # about is the ordering *within* a turn, so it asserts that rather than an index.
    assert types[0] == "system.boot"
    assert "session.created" in types
    assert types.index("session.created") < types.index("turn.started")
    assert "message.completed" in types
    assert types[-1] == "turn.finished"


def test_every_event_carries_trace_id_and_seq(client: TestClient) -> None:
    with client.websocket_connect("/api/v1/stream?since_seq=0") as ws:
        ws.send_json({"type": "session.message", "payload": {"text": "trace me"}})
        seen = []
        for _ in range(40):
            ev = ws.receive_json()
            seen.append(ev)
            if ev["type"] == "turn.finished":
                break

    assert all(e["trace_id"] and e["trace_id"] != "-" for e in seen)
    seqs = [e["seq"] for e in seen]
    assert seqs == sorted(seqs)
    assert len(seqs) == len(set(seqs))


def test_reconnect_after_disconnect_has_no_gaps_or_duplicates(client: TestClient) -> None:
    with client.websocket_connect("/api/v1/stream?since_seq=0") as ws:
        ws.send_json({"type": "session.message", "payload": {"text": "first"}})
        first: list[dict] = []
        for _ in range(40):
            ev = ws.receive_json()
            first.append(ev)
            if ev["type"] == "turn.finished":
                break
    last_seq = first[-1]["seq"]  # client "crashed" here

    # More happens while the client is away.
    with client.websocket_connect("/api/v1/stream?since_seq=0") as other:
        other.send_json({"type": "session.message", "payload": {"text": "while away"}})
        for _ in range(40):
            if other.receive_json()["type"] == "turn.finished":
                break

    with client.websocket_connect(f"/api/v1/stream?since_seq={last_seq}") as ws2:
        caught: list[dict] = []
        for _ in range(40):
            ev = ws2.receive_json()
            caught.append(ev)
            if ev["type"] == "turn.finished":
                break

    seqs = [e["seq"] for e in caught]
    assert seqs[0] == last_seq + 1, "gap: first replayed event is not last_seq+1"
    assert seqs == list(range(seqs[0], seqs[0] + len(seqs))), "gap inside replay"
    assert len(seqs) == len(set(seqs)), "duplicate delivered on resume"


def test_two_clients_see_identical_streams(client: TestClient) -> None:
    with client.websocket_connect("/api/v1/stream?since_seq=0") as a:
        with client.websocket_connect("/api/v1/stream?since_seq=0") as b:
            a.send_json({"type": "session.message", "payload": {"text": "broadcast"}})
            sa, sb = [], []
            for sink, sock in ((sa, a), (sb, b)):
                for _ in range(40):
                    ev = sock.receive_json()
                    sink.append((ev["seq"], ev["type"]))
                    if ev["type"] == "turn.finished":
                        break
    assert sa == sb


def test_history_survives_reload(client: TestClient) -> None:
    with client.websocket_connect("/api/v1/stream?since_seq=0") as ws:
        ws.send_json({"type": "session.message", "payload": {"text": "persist me"}})
        session_id = None
        for _ in range(40):
            ev = ws.receive_json()
            session_id = session_id or ev["session_id"]
            if ev["type"] == "turn.finished":
                break

    events = client.get(f"/api/v1/sessions/{session_id}/events?since_seq=0").json()["events"]
    assert any(e["type"] == "message.completed" for e in events)
    assert all(e["session_id"] == session_id for e in events)


def test_secret_in_a_message_never_reaches_the_wire(client: TestClient) -> None:
    leak = "sk-ant-api03-AAAABBBBCCCCDDDDEEEEFFFFGGGG"
    with client.websocket_connect("/api/v1/stream?since_seq=0") as ws:
        ws.send_json({"type": "session.message", "payload": {"text": f"my key is {leak}"}})
        blob = ""
        for _ in range(60):
            ev = ws.receive_json()
            blob += str(ev)
            if ev["type"] == "turn.finished":
                break
    assert leak not in blob
    assert "REDACTED" in blob


def test_malformed_command_is_ignored_not_fatal(client: TestClient) -> None:
    with client.websocket_connect("/api/v1/stream?since_seq=0") as ws:
        ws.send_json({"garbage": True})
        ws.send_json({"type": "nonexistent.command", "payload": {}})
        ws.send_json({"type": "session.message", "payload": {"text": "still alive"}})
        for _ in range(40):
            if ws.receive_json()["type"] == "turn.finished":
                break  # reaching here at all is the assertion


def test_since_seq_ahead_of_server_resyncs_instead_of_hanging(client: TestClient) -> None:
    """Found by a live smoke test: a client whose since_seq is ahead of the server —
    e.g. after the database was reset — used to have every subsequent event filtered
    out as a duplicate, leaving the socket open, live, and permanently silent."""
    with client.websocket_connect("/api/v1/stream?since_seq=999999") as ws:
        first = ws.receive_json()
        assert first["type"] == "session.resync"
        assert first["payload"]["reason"] == "since_seq ahead of server"

        ws.send_json({"type": "session.message", "payload": {"text": "/status"}})
        for _ in range(40):
            ev = ws.receive_json()
            if ev["type"] == "turn.finished":
                break
        else:  # pragma: no cover
            raise AssertionError("stream went silent after a forward since_seq")


class TestKnowledgeHealth:
    """RAG.md §9: what is indexed, when, how big, what failed."""

    def test_an_unbuilt_index_says_so_rather_than_erroring(self, client: TestClient) -> None:
        """First run is the common case, and "not built yet" is a state, not a failure.

        The settings fixture points at a temporary data dir, so this is genuinely the
        no-index path rather than a mock of it.
        """
        body = client.get("/api/v1/knowledge").json()
        assert body["built"] is False
        assert body["model"]
        assert "path" in body

    def test_a_built_index_reports_its_contents(
        self, client: TestClient, settings: Settings
    ) -> None:
        import numpy as np

        from oracle.rag.chunking import Chunk
        from oracle.rag.collections import ContentKind, Document
        from oracle.rag.embedding import DEFAULT
        from oracle.rag.store import KnowledgeStore

        # `DEFAULT`, not a named spec: this asserts that an index built by the model
        # this build ships reads back as healthy, and it has to keep doing that across
        # a model switch.
        store = KnowledgeStore(settings.data_dir / "knowledge.db", DEFAULT.out_dim)
        store.bind(DEFAULT.name, DEFAULT.out_dim)
        doc = Document(
            collection="projects",
            project="Asterim",
            path="a.ts",
            abs_path=settings.data_dir / "a.ts",
            kind=ContentKind.CODE,
            size=10,
            mtime_ns=1,
        )
        chunk = Chunk(doc=doc, ordinal=0, anchor="TokenService", text="body text here")
        store.put(
            doc,
            [chunk],
            np.zeros((1, DEFAULT.out_dim), dtype=np.float32),
            content_hash="h",
            provenance="local_owned",
            indexed_at="2026-08-22T00:00:00Z",
            idents=["Token Service"],
            token_counts=[3],
        )
        store.close()

        body = client.get("/api/v1/knowledge").json()
        assert body["built"] is True
        assert body["chunks"] == 1
        assert body["vectors"] == 1
        assert body["collections"][0]["collection_id"] == "projects"
        assert body["file_bytes"] > 0

    def test_an_index_built_by_another_model_is_reported_stale(
        self, client: TestClient, settings: Settings
    ) -> None:
        """Not stale — wrong. Querying it returns confident nonsense, so the health view
        has to say so rather than reporting a healthy row count."""
        from oracle.rag.embedding import DEFAULT
        from oracle.rag.store import KnowledgeStore

        store = KnowledgeStore(settings.data_dir / "knowledge.db", DEFAULT.out_dim)
        store.bind("some-other-model", DEFAULT.out_dim)
        store.close()

        body = client.get("/api/v1/knowledge").json()
        assert body["built"] is False
        assert body["stale"] is True
        assert "reindex" in body["error"]


class TestKnowledgeReindex:
    """The health view's one action. The endpoint owns no indexing code: everything it
    does is ask the executor for `know.reindex`, so the call crosses the policy gate
    like every other invocation (ROADMAP sequencing rule 2)."""

    def test_a_trigger_goes_through_the_executor_and_reports_what_the_tool_did(
        self, client: TestClient
    ) -> None:
        """A successful run answers with the tool's own summary — not a bare 202. The
        request holds until the index is up to date, so the result IS the report."""
        from oracle.api.app import state_of
        from oracle.policy.model import Decision, PolicyVerdict, Tier
        from oracle.tools.executor import ToolOutcome
        from oracle.tools.knowledge import KnowReindexResult

        st = state_of(client.app)
        asked: list[tuple[str, dict]] = []

        class Recorder:
            async def execute(self, tool_id: str, raw_args: dict, **_: object) -> ToolOutcome:
                asked.append((tool_id, raw_args))
                return ToolOutcome(
                    tool=tool_id,
                    ok=True,
                    result=KnowReindexResult(
                        documents=3,
                        unchanged=2,
                        chunks=5,
                        embedded=4,
                        cached=1,
                        pruned=0,
                        failed=0,
                        seconds=1.2,
                        degraded=False,
                    ),
                    verdict=PolicyVerdict(
                        decision=Decision.ALLOW,
                        tier=Tier.T1,
                        base_tier=Tier.T1,
                        rule="tools.know.reindex",
                    ),
                    duration_ms=7,
                )

        st.executor = Recorder()  # type: ignore[assignment]

        body = client.post("/api/v1/knowledge/reindex", params={"full": True}).json()

        assert asked == [("know.reindex", {"full": True})]
        assert body["ok"] is True
        assert body["documents"] == 3 and body["chunks"] == 5 and body["embedded"] == 4
        assert body["cached"] == 1 and body["seconds"] == 1.2 and body["degraded"] is False

    def test_a_bare_trigger_is_incremental_and_a_collection_rides_through(
        self, client: TestClient
    ) -> None:
        """`full` re-embeds everything — roughly an hour on this CPU per the tool
        contract — so it must never be implicit: an unqualified click gets the
        incremental path. Scoping to one collection passes through unchanged."""
        from oracle.api.app import state_of
        from oracle.policy.model import Decision, PolicyVerdict, Tier
        from oracle.tools.executor import ToolOutcome
        from oracle.tools.knowledge import KnowReindexResult

        st = state_of(client.app)
        asked: list[tuple[str, dict]] = []

        class Recorder:
            async def execute(self, tool_id: str, raw_args: dict, **_: object) -> ToolOutcome:
                asked.append((tool_id, raw_args))
                return ToolOutcome(
                    tool=tool_id,
                    ok=True,
                    result=KnowReindexResult(
                        documents=0,
                        unchanged=0,
                        chunks=0,
                        embedded=0,
                        cached=0,
                        pruned=0,
                        failed=0,
                        seconds=0.1,
                        degraded=False,
                    ),
                    verdict=PolicyVerdict(
                        decision=Decision.ALLOW,
                        tier=Tier.T1,
                        base_tier=Tier.T1,
                        rule="tools.know.reindex",
                    ),
                    duration_ms=2,
                )

        st.executor = Recorder()  # type: ignore[assignment]

        client.post("/api/v1/knowledge/reindex")
        client.post("/api/v1/knowledge/reindex", params={"collection": "notes"})

        assert asked == [
            ("know.reindex", {"full": False}),
            ("know.reindex", {"full": False, "collection": "notes"}),
        ]

    def test_a_policy_refusal_is_reported_not_worked_around(self, client: TestClient) -> None:
        """A HALTed or locked-down daemon refuses `know.reindex` at the gate. The
        endpoint reflects the executor's answer — `ok: false`, naming the reason —
        rather than reaching into `rag/` itself, which would be exactly the second
        execution path sequencing rule 2 forbids. An HTTP error would be wrong too:
        a policy refusal is something to render, not a broken server."""
        from oracle.api.app import state_of
        from oracle.policy.model import Decision, PolicyVerdict, Tier
        from oracle.tools.executor import ToolError, ToolErrorKind, ToolOutcome

        st = state_of(client.app)

        class Refuser:
            async def execute(self, tool_id: str, raw_args: dict, **_: object) -> ToolOutcome:
                return ToolOutcome(
                    tool=tool_id,
                    ok=False,
                    result=None,
                    verdict=PolicyVerdict(
                        decision=Decision.DENY,
                        tier=Tier.T1,
                        base_tier=Tier.T1,
                        rule="halt",
                        reason="halted: user requested halt",
                    ),
                    duration_ms=1,
                    error=ToolError(ToolErrorKind.DENIED, "halted: user requested halt"),
                )

        st.executor = Refuser()  # type: ignore[assignment]

        resp = client.post("/api/v1/knowledge/reindex")

        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is False
        assert body["error"]["kind"] == "denied"
        assert "halted" in body["error"]["message"]

    def test_malformed_input_is_refused_before_the_executor_is_consulted(
        self, client: TestClient
    ) -> None:
        """`full=banana` is the client's error: a 422 from validation, not a 500 —
        and nothing reaches the executor, because "it returned an error" and "it
        never ran" are different properties and only the second is the claim."""
        from oracle.api.app import state_of

        st = state_of(client.app)
        asked: list[str] = []

        class Recorder:
            async def execute(self, tool_id: str, raw_args: dict, **_: object) -> object:
                asked.append(tool_id)
                raise AssertionError("validation should have refused this request")

        st.executor = Recorder()  # type: ignore[assignment]

        resp = client.post("/api/v1/knowledge/reindex", params={"full": "banana"})

        assert resp.status_code == 422
        assert asked == []


class TestGlobalSearch:
    """One query, grouped answers (UI.md §11). The retrieval half goes through the
    executor — and therefore the gate — exactly like a chat turn; the stored half is
    the briefing's precedent, SQL over the API's own rows. Each group fails alone."""

    @staticmethod
    def _outcome(tool_id: str, result: object) -> object:
        from oracle.policy.model import Decision, PolicyVerdict, Tier
        from oracle.tools.executor import ToolOutcome

        return ToolOutcome(
            tool=tool_id,
            ok=True,
            result=result,  # type: ignore[arg-type]
            verdict=PolicyVerdict(
                decision=Decision.ALLOW, tier=Tier.T0, base_tier=Tier.T0, rule=f"tools.{tool_id}"
            ),
            duration_ms=3,
        )

    @staticmethod
    def _hit(collection: str, path: str, provenance: str = "local_owned") -> dict:
        return {
            "chunk_id": f"ch_{path}",
            "collection": collection,
            "project": "Asterim",
            "path": path,
            "abs_path": f"C:/x/{path}",
            "anchor": "(file)",
            "score": 0.7,
            "provenance": provenance,
            "indexed_at": "2026-08-28T20:00:00Z",
            "text": "…snippet…",
        }

    def test_knowledge_hits_split_by_collection_and_taint_rides_through(
        self, client: TestClient
    ) -> None:
        """A vault note and a source file are different kinds of answer, so one
        `know.search` call becomes two groups — and `tainted` must survive the trip,
        because a search result is a chunk somebody may not have written."""
        from oracle.api.app import state_of
        from oracle.tools.knowledge import KnowSearchResult

        st = state_of(client.app)
        asked: list[tuple[str, dict]] = []
        outer = self

        class Recorder:
            async def execute(self, tool_id: str, raw_args: dict, **_: object) -> object:
                asked.append((tool_id, raw_args))
                return outer._outcome(
                    tool_id,
                    KnowSearchResult(
                        query=str(raw_args["query"]),
                        results=[
                            outer._hit("projects", "src/auth.ts"),
                            outer._hit("notes", "vault/auth.md", provenance="local_foreign"),
                            outer._hit("projects", "src/token.ts"),
                        ],
                        tainted=True,
                        strategy="hybrid",
                        degraded=False,
                    ),
                )

        st.executor = Recorder()  # type: ignore[assignment]

        body = client.get("/api/v1/search", params={"q": "auth"}).json()

        assert asked == [("know.search", {"query": "auth", "limit": 16})]
        assert [f["path"] for f in body["files"]] == ["src/auth.ts", "src/token.ts"]
        assert [n["path"] for n in body["notes"]] == ["vault/auth.md"]
        assert body["tainted"] is True
        assert body["git_searched"] is False  # no project named, no repository swept
        assert body["elapsed_ms"] >= 0

    def test_git_searches_one_repository_or_none(self, settings: Settings) -> None:
        """`git.log` has no grep and the toolhost serialises invocations, so an
        all-projects sweep would be OQ-24's fan-out under a new name. Naming a
        registered project filters its last 50 subjects; naming none turns the
        group off, stated rather than empty.

        Builds its own client: candidates are discovered at boot, so the directory
        must exist before the app does."""
        from oracle.api.app import state_of
        from oracle.tools.git import Commit, GitLogResult
        from oracle.tools.knowledge import KnowSearchResult

        (settings.projects_root / "Asterim").mkdir(parents=True)
        asked: list[tuple[str, dict]] = []
        outer = self

        class Recorder:
            async def execute(self, tool_id: str, raw_args: dict, **_: object) -> object:
                asked.append((tool_id, raw_args))
                if tool_id == "know.search":
                    return outer._outcome(
                        tool_id,
                        KnowSearchResult(
                            query="auth",
                            results=[],
                            tainted=False,
                            strategy="hybrid",
                            degraded=False,
                        ),
                    )
                return outer._outcome(
                    tool_id,
                    GitLogResult(
                        repo="Asterim",
                        commits=[
                            Commit(
                                sha="a" * 40,
                                short="aaaaaaa",
                                author="q",
                                date="2026-08-28",
                                subject="fix auth token refresh",
                            ),
                            Commit(
                                sha="b" * 40,
                                short="bbbbbbb",
                                author="q",
                                date="2026-08-28",
                                subject="ui: unrelated work",
                            ),
                        ],
                    ),
                )

        with TestClient(create_app(settings)) as client:
            registered = client.post("/api/v1/projects", params={"name": "Asterim"})
            assert registered.status_code == 200, registered.text

            state_of(client.app).executor = Recorder()  # type: ignore[assignment]
            body = client.get("/api/v1/search", params={"q": "auth", "project": "Asterim"}).json()

        git_calls = [a for a in asked if a[0] == "git.log"]
        assert len(git_calls) == 1 and git_calls[0][1]["limit"] == 50
        assert body["git_searched"] is True
        assert [c["subject"] for c in body["git"]] == ["fix auth token refresh"]

    def test_stored_rows_are_searched_without_tools_and_groups_fail_alone(
        self, client: TestClient, settings: Settings
    ) -> None:
        """Tasks and events are the API's own rows — the briefing's precedent, no tool
        and no model. And a refused `know.search` becomes that group's error field
        while the stored groups still answer: error is a field, never an exception."""
        import sqlite3

        from oracle.api.app import state_of
        from oracle.policy.model import Decision, PolicyVerdict, Tier
        from oracle.tools.executor import ToolError, ToolErrorKind, ToolOutcome

        seed = sqlite3.connect(settings.db_path)
        seed.execute(
            """INSERT INTO tasks (id, root_id, kind, status, spec, depends_on, created_at)
               VALUES ('tk_s1', 'tk_root', 'delegation', 'failed',
                       '{"objective": "repair the auth retry ladder"}', '[]',
                       '2026-08-28T20:00:00Z')"""
        )
        seed.execute(
            """INSERT INTO events (ts, type, session_id, turn_id, task_id, trace_id, actor,
                                   payload, critical)
               VALUES ('2026-08-28T20:00:01Z', 'continue.derived', NULL, NULL, NULL,
                       'tr_seed', 'system', '{"project": "Asterim", "notes": ["auth"]}', 0)"""
        )
        seed.commit()
        seed.close()

        st = state_of(client.app)

        class Refuser:
            async def execute(self, tool_id: str, raw_args: dict, **_: object) -> ToolOutcome:
                return ToolOutcome(
                    tool=tool_id,
                    ok=False,
                    result=None,
                    verdict=PolicyVerdict(
                        decision=Decision.DENY,
                        tier=Tier.T0,
                        base_tier=Tier.T0,
                        rule="halt",
                        reason="halted",
                    ),
                    duration_ms=1,
                    error=ToolError(ToolErrorKind.DENIED, "halted: user requested halt"),
                )

        st.executor = Refuser()  # type: ignore[assignment]

        body = client.get("/api/v1/search", params={"q": "auth"}).json()

        assert [t["id"] for t in body["tasks"]] == ["tk_s1"]
        assert body["tasks"][0]["objective"] == "repair the auth retry ladder"
        assert any(e["type"] == "continue.derived" for e in body["events"])
        assert body["files"] == [] and body["notes"] == []
        assert "halted" in body["knowledge_error"]

    def test_wildcards_are_searched_as_characters_not_as_wildcards(
        self, client: TestClient
    ) -> None:
        """A query of `100%` must look for the string `100%`, not become LIKE's
        match-everything — the difference between a search box and an injection."""
        from oracle.api.app import state_of
        from oracle.tools.knowledge import KnowSearchResult

        st = state_of(client.app)
        outer = self

        class Quiet:
            async def execute(self, tool_id: str, raw_args: dict, **_: object) -> object:
                return outer._outcome(
                    tool_id,
                    KnowSearchResult(
                        query=str(raw_args["query"]),
                        results=[],
                        tainted=False,
                        strategy="hybrid",
                        degraded=False,
                    ),
                )

        st.executor = Quiet()  # type: ignore[assignment]

        resp = client.get("/api/v1/search", params={"q": "100%_\\"})
        assert resp.status_code == 200
        body = resp.json()
        # A wildcard-as-wildcard would have matched every event the boot wrote.
        assert body["events"] == [] and body["tasks"] == []


def test_the_task_graph_endpoint_is_a_projection_of_the_table(client: TestClient) -> None:
    """`GET /api/v1/tasks` reads the rows and shapes them; it is not a second source of
    truth (ORCHESTRATION.md §6). A graph nobody ran is an empty tree, not a 404 — the
    client asking is a client that already saw a `task.*` event and wants the whole
    picture, and an error would tell it to retry something that will never appear."""
    import asyncio

    from oracle.api.app import state_of
    from oracle.orchestration.models import Task, TaskKind, TaskResult, TaskSpec, TaskStatus

    st = state_of(client.app)
    task = Task(
        id="tk_a",
        root_id="tk_root",
        kind=TaskKind.TOOL,
        spec=TaskSpec(objective="look at it", role="coder"),
    ).with_status(
        TaskStatus.SUCCEEDED,
        result=TaskResult(ok=True, summary="done", evidence={"rule": "fs.read"}, claim="I looked"),
    )
    # `TestClient` is synchronous and owns the app's loop, so the row goes in on a loop
    # of this test's own. Safe because `aiosqlite` binds each call's future to whichever
    # loop is running when it is made, and the connection's worker thread resolves it
    # there — but it is the only reason this looks odd.
    asyncio.run(_save(st, task))

    body = client.get("/api/v1/tasks", params={"root_id": "tk_root"}).json()
    assert body["root_id"] == "tk_root"
    assert body["live"] is False
    assert body["status"] == "succeeded"
    [only] = body["tasks"]
    assert only["id"] == "tk_a" and only["kind"] == "tool"
    # Evidence and claim arrive separate, all the way to the client.
    assert only["evidence"] == {"rule": "fs.read"} and only["claim"] == "I looked"

    empty = client.get("/api/v1/tasks", params={"root_id": "tk_nothing"}).json()
    assert empty["tasks"] == [] and empty["live"] is False


async def _save(st: object, task: object) -> None:
    await st.task_store.save(task)  # type: ignore[attr-defined]


# -- rung 4: a plan a person wrote  (P8-T3) ------------------------------------


def _typed_plan(**overrides: object) -> dict:
    body: dict = {
        "objective": "tidy the docs",
        "summary": "one pass",
        "tasks": [
            {
                "id": "A",
                "role": "coder",
                "objective": "tidy the docs",
                "acceptance": ["the suite still passes"],
                "expected_outcome": "diff",
            }
        ],
        "risks": [],
    }
    body.update(overrides)
    return body


def test_a_submitted_plan_that_names_a_tool_is_rejected_like_any_other(
    client: TestClient,
) -> None:
    """Rung 4 of the ladder (docs/PLANNER.md §6) is a path, not a privilege. "The author
    is trusted" is exactly the control ADR-0021 says never to build, so a plan a person
    typed meets the same parser a vendor's does."""
    hostile = _typed_plan()
    hostile["tasks"][0]["tool"] = "fs.write"
    with client.websocket_connect("/api/v1/stream?since_seq=0") as ws:
        ws.send_json({"type": "graph.submit_plan", "payload": {"plan": hostile}})
        problems: list[str] = []
        for _ in range(40):
            ev = ws.receive_json()
            if ev["type"] == "plan.rejected":
                problems = list(ev["payload"]["problems"])
                assert ev["payload"]["authored_by"] == "human"
                break
    assert problems and any("tool" in p for p in problems), problems


def test_a_submitted_plan_still_has_to_be_approved(client: TestClient) -> None:
    """It reaches the same card, priced the same way. Denying it is a full stop."""
    with client.websocket_connect("/api/v1/stream?since_seq=0") as ws:
        ws.send_json({"type": "graph.submit_plan", "payload": {"plan": _typed_plan()}})
        asked = None
        for _ in range(60):
            ev = ws.receive_json()
            if ev["type"] == "approval.requested":
                asked = ev["payload"]
                break
        assert asked is not None, "a plan a person typed ran without being approved"
        assert asked["tool"] == "ai.graph"
        assert asked["preview"]["authored_by"] == "human"
        assert asked["preview"]["rung"] == 4
        ws.send_json(
            {
                "type": "approval.respond",
                "payload": {"approval_id": asked["approval_id"], "decision": "deny"},
            }
        )
        for _ in range(40):
            ev = ws.receive_json()
            if ev["type"] == "approval.resolved":
                assert ev["payload"]["resolution"] == "refused"
                break
        else:  # pragma: no cover - the loop above always finds it
            raise AssertionError("the refusal was never recorded")
