"""`know.*` — search the project knowledge index.

These run inside the toolhost child (ADR-0003), which is why they open `knowledge.db`
themselves rather than being handed a store: nothing routes back into the runtime from
here, by design.

**Four tools, not the five in TOOLS.md.** `know.summarize` is deliberately absent.
As specified it "uses the local model", and a handler in the tool host cannot call the
LLM layer — L7 must never re-enter L3-L6 (ARCHITECTURE.md §4), and the whole point of
the process boundary is that the side of it holding an API key is not the side executing
model-chosen arguments. Summarising retrieved context is a *runtime* concern: it belongs
above the gate, built on `know.read_context`, and it needs an ADR before it is built as
anything else. Recorded rather than quietly implemented.

Everything returned from here is **untrusted**. Each result carries its provenance, and a
result set containing `local_foreign` content sets `tainted`, which escalates the tier of
any plan built from it (SECURITY.md §6). The tools are T0 because reading an index the
user opted into is not a side effect — but what they return is data, never instruction.
"""

from __future__ import annotations

import asyncio
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Any

from pydantic import Field

from oracle.config import Settings
from oracle.logsink import get_logger
from oracle.policy.model import Capability, Tier
from oracle.rag.cache import EmbeddingCache, cache_path
from oracle.rag.collections import load_registry
from oracle.rag.embedding import DEFAULT, Embedder, ModelSpec
from oracle.rag.indexer import index as run_index
from oracle.rag.retrieval import TOP_K, retrieve, to_citation
from oracle.rag.store import KnowledgeStore
from oracle.tools.contract import ToolArgs, ToolContext, ToolResult, tool

log = get_logger(__name__)

MAX_RESULTS = 20
#: How much chunk text a single result carries back. A search that returns forty full
#: chunks has spent the context budget the retrieval was supposed to protect.
SNIPPET_CHARS = 600

CollectionName = Annotated[str | None, Field(default=None, description="Restrict to a collection")]
ProjectName = Annotated[str | None, Field(default=None, description="Restrict to one project")]


#: The model these tools query with — `embedding.DEFAULT`, aliased ONCE and pinned by a
#: test. This line used to say `E5_BASE`, hardcoded, and when the indexer moved to
#: `bge-m3` on 2026-08-24 nobody moved it: every `know.*` call through the toolhost then
#: failed `bind()` against the rebuilt index — a SchemaMismatch the fixture-world tests
#: could never see, because a test's empty tmp index binds whatever the tool asks for,
#: self-consistently. Found 2026-08-28 by a latency measurement that expected numbers and
#: got refusals. "One name to change" (embedding.py) is only true when nobody keeps a
#: private copy of the name.
_MODEL: ModelSpec = DEFAULT


@lru_cache(maxsize=1)
def _store() -> KnowledgeStore:
    settings = Settings()
    store = KnowledgeStore(settings.data_dir / "knowledge.db", _MODEL.out_dim)
    store.bind(_MODEL.name, _MODEL.out_dim)
    return store


@lru_cache(maxsize=1)
def _cache() -> EmbeddingCache:
    """Shared with `scripts/index_knowledge.py` — one cache file per model, not per caller."""
    settings = Settings()
    return EmbeddingCache(
        cache_path(settings.data_dir, _MODEL.name, _MODEL.out_dim),
        _MODEL.name,
        _MODEL.out_dim,
    )


@lru_cache(maxsize=1)
def _embedder() -> Embedder | None:
    """The model, or None if it is not on disk.

    Returning None rather than raising is the degraded mode from ARCHITECTURE.md §8:
    without the model there is no dense half, and BM25 alone is a materially worse search
    — but it is a working one, and a knowledge tool that refuses to run at all is worse
    than one that says it is degraded.
    """
    try:
        return Embedder(_MODEL)
    except (FileNotFoundError, OSError) as exc:
        log.warning("know.embedder_unavailable", error=str(exc))
        return None


def _snippet(text: str) -> str:
    return text if len(text) <= SNIPPET_CHARS else text[:SNIPPET_CHARS] + "…"


# --------------------------------------------------------------------- know.search


class KnowSearchArgs(ToolArgs):
    query: Annotated[str, Field(min_length=2, description="Natural-language question")]
    collection: CollectionName = None
    project: ProjectName = None
    limit: Annotated[int, Field(default=TOP_K, ge=1, le=MAX_RESULTS)] = TOP_K


class KnowSearchResult(ToolResult):
    query: str
    results: list[dict[str, Any]]
    #: True when any result is `local_foreign`. The runtime reads this, not the text.
    tainted: bool
    strategy: str
    degraded: bool


@tool(
    id="know.search",
    summary="Search indexed projects and notes. Returns cited passages.",
    args=KnowSearchArgs,
    result=KnowSearchResult,
    capabilities={Capability.FS_READ},
    scopes=frozenset(),
    risk=Tier.T0,
    reversible=True,
    intents={"question", "investigate", "search"},
    side_effects="None. Reads the local index only.",
)
async def know_search(*, ctx: ToolContext, args: KnowSearchArgs) -> KnowSearchResult:
    return await _search(args)


async def _search(args: KnowSearchArgs) -> KnowSearchResult:
    """The shared body.

    A plain function rather than a handler calling a handler: `@tool` returns the
    *contract*, so the module-level name is data, not a callable. Two tools that share
    logic have to share a function.
    """
    embedder = _embedder()
    store = _store()

    def work() -> KnowSearchResult:
        if embedder is None:
            from oracle.rag.retrieval import fts_query

            hits = store.search_lexical(fts_query(args.query), args.limit)
            return KnowSearchResult(
                query=args.query,
                results=[{**to_citation(h), "text": _snippet(h.text)} for h in hits],
                tainted=any(h.provenance != "local_owned" for h in hits),
                strategy="lexical",
                degraded=True,
            )
        found = retrieve(
            args.query,
            store,
            embedder,
            collection=args.collection,
            project=args.project,
            limit=args.limit,
        )
        return KnowSearchResult(
            query=args.query,
            results=[{**to_citation(h), "text": _snippet(h.text)} for h in found.hits],
            tainted=found.tainted,
            strategy=found.strategy,
            degraded=False,
        )

    # The store is synchronous by design (see rag/store.py); a thread keeps a 30 ms scan
    # off the toolhost's loop rather than pretending SQLite is async.
    return await asyncio.to_thread(work)


# ---------------------------------------------------------------- know.search_code


class KnowSearchCodeArgs(ToolArgs):
    query: Annotated[str, Field(min_length=2, description="Symbol name or code question")]
    project: ProjectName = None
    limit: Annotated[int, Field(default=TOP_K, ge=1, le=MAX_RESULTS)] = TOP_K


@tool(
    id="know.search_code",
    summary="Search code only, favouring exact symbol matches.",
    args=KnowSearchCodeArgs,
    result=KnowSearchResult,
    capabilities={Capability.FS_READ},
    scopes=frozenset(),
    risk=Tier.T0,
    reversible=True,
    intents={"question", "investigate", "search", "modify"},
    side_effects="None. Reads the local index only.",
)
async def know_search_code(*, ctx: ToolContext, args: KnowSearchCodeArgs) -> KnowSearchResult:
    """Scoped to the `projects` collection.

    Separate from `know.search` because the questions differ in kind: "where is
    `TokenService.refresh`" wants the symbol, and mixing 157 conceptual notes into that
    result set is noise. The scoping is a metadata pre-filter, so it costs nothing.
    """
    return await _search(
        KnowSearchArgs(
            query=args.query, collection="projects", project=args.project, limit=args.limit
        )
    )


# ----------------------------------------------------------------- know.read_context


class KnowReadContextArgs(ToolArgs):
    topic: Annotated[str, Field(min_length=2)]
    project: ProjectName = None
    max_chars: Annotated[int, Field(default=6000, ge=500, le=20000)] = 6000


class KnowReadContextResult(ToolResult):
    topic: str
    context: str
    citations: list[dict[str, Any]]
    tainted: bool
    truncated: bool


@tool(
    id="know.read_context",
    summary="Assemble a citable context block about a topic.",
    args=KnowReadContextArgs,
    result=KnowReadContextResult,
    capabilities={Capability.FS_READ},
    scopes=frozenset(),
    risk=Tier.T0,
    reversible=True,
    intents={"question", "investigate", "explain"},
    side_effects="None. Reads the local index only.",
)
async def know_read_context(
    *, ctx: ToolContext, args: KnowReadContextArgs
) -> KnowReadContextResult:
    """Full chunk text, not snippets, up to a character budget.

    Every block is prefixed with `[n]` and the citation list is returned alongside, so a
    claim built from this can be checked in one click. An uncited claim from a 2B model
    is worthless; a cited one is checkable (RAG.md §7).
    """
    search = await _search(
        KnowSearchArgs(query=args.topic, project=args.project, limit=MAX_RESULTS)
    )

    blocks: list[str] = []
    citations: list[dict[str, Any]] = []
    used = 0
    truncated = False
    for i, result in enumerate(search.results, start=1):
        text = str(result.get("text", ""))
        header = f"[{i}] {result['project']} / {result['path']}"
        if result.get("anchor"):
            header += f" / {result['anchor']}"
        block = f"{header}\n{text}"
        if used + len(block) > args.max_chars:
            truncated = True
            break
        blocks.append(block)
        citations.append({k: v for k, v in result.items() if k != "text"})
        used += len(block)

    return KnowReadContextResult(
        topic=args.topic,
        context="\n\n".join(blocks),
        citations=citations,
        tainted=any(c.get("provenance") != "local_owned" for c in citations),
        truncated=truncated,
    )


# -------------------------------------------------------------------- know.reindex


class KnowReindexArgs(ToolArgs):
    collection: CollectionName = None
    #: A full rebuild re-embeds everything. Measured at roughly an hour on this CPU, so
    #: it is never implicit — the incremental path is the default and the fast one.
    full: bool = False


class KnowReindexResult(ToolResult):
    documents: int
    unchanged: int
    chunks: int
    embedded: int
    #: Vectors reused from the embedding cache rather than recomputed.
    cached: int
    pruned: int
    failed: int
    seconds: float
    degraded: bool


@tool(
    id="know.reindex",
    summary="Update the knowledge index from disk. Scoped unless full is set.",
    args=KnowReindexArgs,
    result=KnowReindexResult,
    capabilities={Capability.FS_READ},
    scopes=frozenset(),
    risk=Tier.T1,
    reversible=True,
    timeout_s=3600,
    intents={"maintain"},
    side_effects="Rewrites knowledge.db, which is disposable by design (ADR-0006).",
)
async def know_reindex(*, ctx: ToolContext, args: KnowReindexArgs) -> KnowReindexResult:
    """T1, not T0: it writes. But what it writes is a rebuildable cache, and it can only
    read paths a human already opted into in `config/collections.yaml` — so it is a
    reversible local write, not a change to anything anyone owns."""
    settings = Settings()
    registry = load_registry(Path(settings.policy_path).parent / "collections.yaml")
    embedder = _embedder()
    store = _store()

    stats = await asyncio.to_thread(
        run_index,
        registry,
        store,
        embedder,
        only=args.collection,
        full=args.full,
        cache=_cache() if embedder is not None else None,
    )
    return KnowReindexResult(
        documents=stats.documents,
        unchanged=stats.unchanged,
        chunks=stats.chunks,
        embedded=stats.embedded,
        cached=stats.cached,
        pruned=stats.pruned,
        failed=stats.failed,
        seconds=round(stats.seconds, 1),
        degraded=embedder is None,
    )


KNOW_TOOLS = (know_search, know_search_code, know_read_context, know_reindex)
