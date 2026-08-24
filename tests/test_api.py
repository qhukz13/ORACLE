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

    assert types[0] == "session.created"
    assert "turn.started" in types
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
