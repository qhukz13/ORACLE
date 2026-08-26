"""Selection helpers for packet inputs (INTEGRATIONS.md §6, steps 1-6).

The rule for everything here: **curated, not dumped**, and every excerpt carries its
source. Step 4 (the project's own agent docs) is called out in §6 as higher-value per
token than more source code — those files exist precisely to orient a coding agent.
Step 3 (hybrid retrieval scoped to the project) returns text that is untrusted by
definition: provenance rides along, and the caller feeds the tainted sources to the
gate so the egress approval escalates (SECURITY.md §6).

Symbol-level neighbours (step 2) wait for the reference-scenario task — they need the
tree-sitter index, and pretending with a regex would be a mock dressed up as curation.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from oracle.handoff.packet import ContextExcerpt, GitState
from oracle.integrations.workspace import WorkspaceError, _git

if TYPE_CHECKING:
    from oracle.rag.embedding import Embedder
    from oracle.rag.store import KnowledgeStore

#: (filename, eviction priority). Ordered by how much each orients an agent; all sit
#: above retrieval hits so the budget evicts code excerpts before orientation docs.
PROJECT_DOCS = (("AGENTS.md", 9), ("CLAUDE.md", 8), ("decisions.md", 7), ("README.md", 6))
#: Per-doc cap. Orientation, not the whole handbook — the budget is shared.
DOC_CAP_CHARS = 4_000
#: Retrieval hits sit below every orientation doc.
RETRIEVAL_PRIORITY = 3


def gather_git_state(repo: Path, *, commits: int = 5, failing_tests: str = "") -> GitState:
    """Step 5: branch, uncommitted status, the last few commits. Never raises — a
    project without git history still deserves a packet, just a thinner STATE.md."""
    try:
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD").strip()
        status = _git(repo, "status", "--porcelain").rstrip()
        log = _git(repo, "log", f"-{commits}", "--format=%h %s").strip()
    except WorkspaceError:
        return GitState(failing_tests=failing_tests)
    return GitState(
        branch=branch,
        status=status,
        recent_commits=tuple(line for line in log.splitlines() if line),
        failing_tests=failing_tests,
    )


def gather_project_docs(repo: Path) -> tuple[ContextExcerpt, ...]:
    """Step 4: the files that exist to orient a coding agent, forwarded as-is (capped).
    Far higher value per token than more source code (INTEGRATIONS.md §6)."""
    out: list[ContextExcerpt] = []
    for name, priority in PROJECT_DOCS:
        file = repo / name
        if not file.is_file():
            continue
        text = file.read_text(encoding="utf-8", errors="replace")
        out.append(
            ContextExcerpt(
                source=name,
                text=text[:DOC_CAP_CHARS],
                reason="project orientation doc",
                priority=priority,
            )
        )
    return tuple(out)


def gather_retrieval(
    question: str,
    store: KnowledgeStore,
    embedder: Embedder,
    *,
    project: str | None = None,
    limit: int = 6,
    translator: Callable[[str], str | None] | None = None,
) -> tuple[tuple[ContextExcerpt, ...], tuple[str, ...]]:
    """Step 3: top hybrid hits for the goal, scoped to the project.

    Returns the excerpts and the sources whose provenance is untrusted — the caller
    hands those to the gate, which escalates the egress approval. Splitting the two
    here would let a caller take the text and forget the taint; returning them
    together makes forgetting a type error.

    **This is the path query translation ships on** (OQ-18, RAG.md §5). A goal typed in
    Russian against an English repository otherwise reaches the packet through one
    multilingual embedding; a second English probe is worth measured recall here and is
    measured *not* to fit the interactive answer path, where the whole latency headroom
    is smaller than one query embedding. A packet precedes a delegation that runs for
    minutes, so seconds are free exactly here and nowhere else.
    """
    from oracle.rag.retrieval import retrieve

    retrieved = retrieve(
        question, store, embedder, project=project, limit=limit, translator=translator
    )
    excerpts = tuple(
        ContextExcerpt(
            source=f"{hit.rel_path} · {hit.anchor}" if hit.anchor else hit.rel_path,
            text=hit.text,
            reason=f"retrieval hit ({hit.provenance})",
            priority=RETRIEVAL_PRIORITY,
        )
        for hit in retrieved.hits
    )
    tainted = tuple(
        dict.fromkeys(h.rel_path for h in retrieved.hits if h.provenance != "local_owned")
    )
    return excerpts, tainted
