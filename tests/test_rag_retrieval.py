"""Hybrid retrieval: fusion, the fusion gate, boosts, diversity, and taint.

The gate on fusion is the part worth testing hardest, because it exists for a measured
reason rather than a theoretical one: on the OQ-02 fixture set, unweighted RRF added 9
points of recall@5 to `e5-base` and removed 5 from `e5-small`. Fusing a retriever that
has nothing to say is not neutral — it is actively harmful.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

from oracle.rag.chunking import Chunk
from oracle.rag.collections import ContentKind, Document
from oracle.rag.retrieval import (
    MAX_PER_FILE,
    Retrieved,
    _diversify,
    discriminating_terms,
    fts_query,
    has_lexical_purchase,
    retrieve,
    rrf,
    to_citation,
)
from oracle.rag.store import Hit, KnowledgeStore

DIM = 4
NOW = datetime(2026, 8, 22, tzinfo=UTC)


class FakeEmbedder:
    """A stand-in for 1.1 GB of weights: maps a phrase to a fixed unit vector.

    Retrieval logic is what these tests are about. Using the real model here would make
    them slow, non-hermetic, and dependent on the very quality question OQ-02 already
    answered elsewhere.
    """

    def __init__(self, table: dict[str, list[float]]) -> None:
        self.table = table

    def encode(self, texts: list[str], role: str, *, batch: int = 16) -> np.ndarray:
        vecs = [self.table.get(t, [0.0, 0.0, 0.0, 1.0]) for t in texts]
        return np.array(vecs, dtype=np.float32)


def _doc(rel: str, project: str = "Asterim", provenance: str = "local_owned") -> Document:
    return Document(
        collection="projects",
        project=project,
        path=rel,
        abs_path=Path("C:/Projects") / rel,
        kind=ContentKind.CODE,
        size=10,
        mtime_ns=1,
    )


def _put(
    store: KnowledgeStore,
    rel: str,
    texts: list[str],
    vecs: list[list[float]],
    *,
    project: str = "Asterim",
    provenance: str = "local_owned",
    indexed_at: str = "2026-08-22T00:00:00Z",
    anchors: list[str] | None = None,
) -> None:
    doc = _doc(rel, project)
    chunks = [
        Chunk(doc=doc, ordinal=i, anchor=(anchors[i] if anchors else f"sym{i}"), text=t)
        for i, t in enumerate(texts)
    ]
    store.put(
        doc,
        chunks,
        np.array(vecs, dtype=np.float32),
        content_hash=hashlib.sha256(rel.encode()).hexdigest(),
        provenance=provenance,
        indexed_at=indexed_at,
        idents=texts,
        token_counts=[len(t.split()) for t in texts],
    )


@pytest.fixture
def store(tmp_path: Path) -> KnowledgeStore:
    s = KnowledgeStore(tmp_path / "knowledge.db", DIM)
    s.bind("fake", DIM)
    return s


class TestFtsQuery:
    def test_a_question_is_not_fts_syntax_and_is_quoted(self) -> None:
        """A bare `AND`, quote or parenthesis raises OperationalError in FTS5, and a
        lexical failure must never fail a turn."""
        query = fts_query('how do we handle "quotes" AND (parens)?')
        assert '"quotes"' in query
        assert query.count("(") == 0

    def test_cyrillic_terms_are_prefix_expanded(self) -> None:
        """OQ-08: unicode61 does not stem, and `токен` must reach `токена`."""
        query = fts_query("как работает токен")
        assert '"токен"*' in query
        assert '"работает"*' in query

    def test_latin_terms_are_not_prefix_expanded(self) -> None:
        """Expanding `get` would match half the corpus."""
        query = fts_query("refresh the access token")
        assert "*" not in query
        assert '"refresh"' in query

    def test_single_characters_are_dropped(self) -> None:
        assert fts_query("a b refresh") == '"refresh"'


class TestFusionGate:
    def test_a_russian_question_against_an_english_corpus_stays_dense(
        self, store: KnowledgeStore
    ) -> None:
        """The measured case. BM25 has nothing to contribute and would only displace
        correct dense hits, so the lexical list is never admitted."""
        _put(store, "token.ts", ["TokenService refreshes the access token"], [[1, 0, 0, 0]])
        assert not has_lexical_purchase("как работает обновление токена", store)

    def test_a_distinctive_english_term_does_have_purchase(self, store: KnowledgeStore) -> None:
        _put(store, "token.ts", ["TokenService refreshes the access token"], [[1, 0, 0, 0]])
        assert has_lexical_purchase("what does TokenService do", store)

    def test_a_ubiquitous_term_alone_does_not(self, store: KnowledgeStore) -> None:
        """A term in every document discriminates nothing."""
        for i in range(60):
            _put(store, f"f{i}.ts", ["the common word appears everywhere"], [[1, 0, 0, 0]])
        assert not has_lexical_purchase("the common word", store)

    def test_retrieve_reports_which_strategy_it_used(self, store: KnowledgeStore) -> None:
        _put(store, "token.ts", ["TokenService refreshes the access token"], [[1, 0, 0, 0]])
        embedder = FakeEmbedder({"как работает токен": [1, 0, 0, 0]})
        result = retrieve("как работает токен", store, embedder, now=NOW)  # type: ignore[arg-type]
        assert result.strategy == "dense"
        assert result.lexical_count == 0
        assert [h.rel_path for h in result.hits] == ["token.ts"]


class TestFusion:
    def test_rrf_rewards_agreement_between_retrievers(self) -> None:
        """A chunk both lists rank highly beats one that only one list likes."""
        scores = rrf([["b", "a", "x"], ["b", "c", "y"]])
        assert scores["b"] > scores["a"]
        assert scores["b"] > scores["c"]

    def test_rrf_does_not_reward_being_middling_in_both(self) -> None:
        """Worth pinning, because it is the opposite of the intuition.

        With two exactly-reversed lists, `1/61 + 1/63 > 2/62` — the *extremes* win and
        the consistently-middling item loses. RRF rewards being ranked highly by someone,
        not being agreed upon by everyone, and a first draft of the test above asserted
        the reverse.
        """
        scores = rrf([["a", "b", "c"], ["c", "b", "a"]])
        assert scores["a"] > scores["b"]
        assert scores["c"] > scores["b"]

    def test_rrf_of_one_list_preserves_its_order(self) -> None:
        scores = rrf([["a", "b", "c"]])
        assert scores["a"] > scores["b"] > scores["c"]


class TestBoostsAndDiversity:
    def test_same_project_wins_a_tie(self, store: KnowledgeStore) -> None:
        _put(store, "a.ts", ["identical text"], [[1, 0, 0, 0]], project="GameRecs")
        _put(store, "b.ts", ["identical text"], [[1, 0, 0, 0]], project="Asterim")
        embedder = FakeEmbedder({"q": [1, 0, 0, 0]})
        result = retrieve("q", store, embedder, project="Asterim", now=NOW)  # type: ignore[arg-type]
        assert result.hits[0].rel_path == "b.ts"

    def test_one_file_cannot_eat_the_whole_budget(self) -> None:
        """A large document would otherwise fill the top-k and starve the answer of a
        second source."""
        hits = [
            Hit(
                chunk_id=str(i),
                collection="projects",
                project="Asterim",
                rel_path="big.ts" if i < 8 else "other.ts",
                abs_path="C:/big.ts",
                anchor="",
                text="t",
                score=1.0 / (i + 1),
                provenance="local_owned",
                indexed_at="2026-08-22T00:00:00Z",
            )
            for i in range(10)
        ]
        out = _diversify(hits, limit=8)
        assert sum(1 for h in out if h.rel_path == "big.ts") == MAX_PER_FILE

    def test_recency_boost_applies_only_within_the_window(self, store: KnowledgeStore) -> None:
        stale = (NOW - timedelta(days=90)).isoformat().replace("+00:00", "Z")
        fresh = (NOW - timedelta(days=1)).isoformat().replace("+00:00", "Z")
        _put(store, "old.ts", ["same"], [[1, 0, 0, 0]], indexed_at=stale)
        _put(store, "new.ts", ["same"], [[1, 0, 0, 0]], indexed_at=fresh)
        embedder = FakeEmbedder({"q": [1, 0, 0, 0]})
        result = retrieve("q", store, embedder, now=NOW)  # type: ignore[arg-type]
        assert result.hits[0].rel_path == "new.ts"


class TestAttribution:
    def test_every_hit_is_citable(self, store: KnowledgeStore) -> None:
        _put(store, "apps/server/token.ts", ["text"], [[1, 0, 0, 0]], anchors=["TokenService"])
        embedder = FakeEmbedder({"q": [1, 0, 0, 0]})
        result = retrieve("q", store, embedder, now=NOW)  # type: ignore[arg-type]
        citation = to_citation(result.hits[0])
        assert citation["path"] == "apps/server/token.ts"
        assert citation["anchor"] == "TokenService"
        assert citation["provenance"] == "local_owned"
        assert isinstance(citation["score"], float)

    def test_foreign_content_taints_the_result(self, store: KnowledgeStore) -> None:
        """Retrieved text is untrusted input. A `local_foreign` chunk escalates the tier
        of any plan built from it (SECURITY.md §6), so the flag is derived here rather
        than left for a caller to remember."""
        _put(store, "vendor.ts", ["text"], [[1, 0, 0, 0]], provenance="local_foreign")
        embedder = FakeEmbedder({"q": [1, 0, 0, 0]})
        result = retrieve("q", store, embedder, now=NOW)  # type: ignore[arg-type]
        assert result.tainted is True

    def test_owned_content_does_not_taint(self, store: KnowledgeStore) -> None:
        _put(store, "mine.ts", ["text"], [[1, 0, 0, 0]])
        embedder = FakeEmbedder({"q": [1, 0, 0, 0]})
        result = retrieve("q", store, embedder, now=NOW)  # type: ignore[arg-type]
        assert result.tainted is False

    def test_an_empty_index_returns_nothing_rather_than_raising(
        self, store: KnowledgeStore
    ) -> None:
        embedder = FakeEmbedder({"q": [1, 0, 0, 0]})
        result = retrieve("q", store, embedder, now=NOW)  # type: ignore[arg-type]
        assert isinstance(result, Retrieved)
        assert result.hits == ()
        assert result.tainted is False


class TestTheGateNeedsMostOfTheQuestion:
    """One surviving term is a thin basis for a thirty-result second opinion.

    BM25 ranking on a fragment of a question is still a full ranking, and RRF admits it
    as a peer. The measured case is a Latin word inside a Russian sentence — `refresh` in
    `ru-token-refresh` — which opened the gate on its own and cost that case its top-5
    slot under *both* embedding models. See `MIN_QUESTION_COVERAGE`.
    """

    def index(self, tmp_path: Path) -> KnowledgeStore:
        store = KnowledgeStore(tmp_path / "coverage.db", DIM)
        store.bind("fake", DIM)
        for i in range(190):
            extra = " PairingService" if i == 0 else ""
            _put(store, f"en{i}.md", [f"the service handles request {i}{extra}"], [[1.0, 0, 0, 0]])
        store.record_script_census()
        return store

    def test_one_term_out_of_many_does_not_open_the_gate(self, tmp_path: Path) -> None:
        store = self.index(tmp_path)
        # `the`, `service` and `request` are in every chunk; only `PairingService` is
        # discriminating, and one term in five is not enough of the question.
        assert discriminating_terms("the service that handles request PairingService", store) == []

    def test_a_bare_identifier_is_the_whole_question_and_does(self, tmp_path: Path) -> None:
        """The rule is a *share*, not a count — searching for one identifier is the case
        the lexical half exists for, and it must survive the fix that closed the gate on
        a stray word."""
        assert discriminating_terms("PairingService", self.index(tmp_path)) == ["PairingService"]

    def test_a_term_in_no_document_is_not_counted_against_coverage(self, tmp_path: Path) -> None:
        """A term BM25 cannot answer is not a term BM25 declined to answer. Counting
        absent words would let one typo close the gate on a question it handles well."""
        store = self.index(tmp_path)
        assert discriminating_terms("PairingService zzzznotinthecorpus", store) == [
            "PairingService"
        ]


class TestOnlyTheMajorityScriptReachesBM25:
    """BM25 sees a query term only if the corpus is written in that term's script.

    Document frequency measures rarity in the corpus, not uninformativeness, and those
    are the same thing only when the corpus and the query share a language. `the` is in
    most of an English corpus and is correctly dropped; `как` — the same kind of word —
    was in 0.8% of this corpus and read as *highly discriminating*, because the corpus is
    mostly English. Every Russian question then pulled in whichever Russian documents
    existed, matching on `как`, `внутри` and `она`, and RRF pushed the correct dense
    hits down (`logs/development/2026-08-22-fusion-denominator.md`).

    **The first remedy was a per-script denominator, and it was not enough.** Counting
    `как` against the Cyrillic sub-corpus does drop it — and recall did not move, because
    the words underneath it are no better: `если` is 4% of the Russian and survives, and
    the genuinely rare ones match the one unrelated Russian project in the corpus. On the
    full corpus, admitting minority-script terms at any frequency costs **12 points of
    cross-language recall@5** (`logs/development/2026-08-24-oq02-bge-m3.md`).

    The rule is *minority*, not *Cyrillic*: the last test here builds a Russian-majority
    corpus and asserts that Latin is what gets gated out.
    """

    def index(self, tmp_path: Path) -> KnowledgeStore:
        """A corpus that is mostly English with a Russian minority — the real shape."""
        store = KnowledgeStore(tmp_path / "denominator.db", DIM)
        store.bind("fake", DIM)
        for i in range(190):
            _put(
                store,
                f"en{i}.md",
                [f"the service validates the token for request {i}"],
                [[1.0, 0, 0, 0]],
            )
        # `как` is in every Russian chunk and no English one, so it is 5% of the corpus
        # and 100% of the Russian in it. `миграциями` is in exactly one.
        for i in range(10):
            extra = " миграциями" if i == 0 else ""
            _put(
                store, f"ru{i}.md", [f"как это работает внутри номер {i}{extra}"], [[0, 1.0, 0, 0]]
            )
        store.record_script_census()
        return store

    def test_a_russian_stopword_is_recognised_as_ubiquitous(self, tmp_path: Path) -> None:
        store = self.index(tmp_path)
        # `как` is in every Russian chunk and no English one: 10 of 100 by the corpus,
        # which looks discriminating, and 10 of 10 by the Russian sub-corpus, which is
        # what it actually is.
        assert "как" not in discriminating_terms("как работает токен", store)

    def test_an_english_stopword_is_still_recognised(self, tmp_path: Path) -> None:
        """The change must not alter the majority-language path at all."""
        assert "the" not in discriminating_terms("the token service", self.index(tmp_path))

    def test_even_a_rare_minority_script_word_is_dropped(self, tmp_path: Path) -> None:
        """This is the assertion that reversed, and it reversed on evidence.

        It used to read "a rare Russian word still counts", on the reasoning that a term
        in one document out of ten is exactly the evidence the lexical half exists to
        supply. That reasoning is sound and the measurement refutes its premise: in a
        corpus that is 6% Cyrillic the rare Russian words are rare *because the corpus is
        not in that language*, so what they match is the one Russian project in it rather
        than the subject of the question. BM25 answers from the wrong neighbourhood and
        RRF admits it as an equal opinion, which is worth minus 12 points on the
        cross-language column.
        """
        store = self.index(tmp_path)
        assert discriminating_terms("как работать с миграциями", store) == []

    def test_a_russian_corpus_gates_out_latin_instead(self, tmp_path: Path) -> None:
        """The rule is minority, not Cyrillic — so it has to reverse with the corpus."""
        store = KnowledgeStore(tmp_path / "russian.db", DIM)
        store.bind("fake", DIM)
        for i in range(190):
            # `миграций` is in exactly one chunk: rare, and in the corpus's own script.
            extra = " миграций" if i == 0 else ""
            _put(
                store,
                f"ru{i}.md",
                [f"сервис проверяет токен запроса номер {i}{extra}"],
                [[1.0, 0, 0, 0]],
            )
        for i in range(10):
            _put(
                store,
                f"en{i}.md",
                [f"the service validates the token number {i}"],
                [[0, 1.0, 0, 0]],
            )
        store.record_script_census()

        assert "миграций" in discriminating_terms("миграций запроса", store)
        assert discriminating_terms("what does TokenService do", store) == []

    def test_an_index_without_a_census_keeps_the_old_behaviour(self, tmp_path: Path) -> None:
        """An index built before the census is not a broken index."""
        store = self.index(tmp_path)
        store.db.execute("DELETE FROM meta WHERE key='cyrillic_chunks'")
        store.db.commit()
        assert store.script_census() is None
        assert "как" in discriminating_terms("как работает токен", store)
