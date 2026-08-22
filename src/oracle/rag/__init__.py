"""Project knowledge: what may be indexed, how it is split, how it is retrieved.

This is the retrieval half of **L5 Context Assembly** (docs/ARCHITECTURE.md#4-layers).
It reads the filesystem directly rather than through the tool host, because indexing is
a background subsystem rather than a model-directed action — nothing the model emits
chooses what gets read here. What may be read is decided once, by a human, in
`config/collections.yaml`, and the walker refuses everything else.

Everything this package produces is **untrusted input**. A chunk retrieved from a note
or a source file is `local_owned` or `local_foreign` depending on who wrote it, it taints
the turn, and it can never influence the policy gate
(docs/SECURITY.md#6-prompt-injection-and-taint-tracking).
"""

from oracle.rag.chunking import Chunk, chunk_document
from oracle.rag.collections import (
    Collection,
    CollectionRegistry,
    ContentKind,
    Document,
    WalkStats,
    load_registry,
    walk,
)
from oracle.rag.embedding import E5_BASE, PASSAGE, QUERY, Embedder, ModelSpec
from oracle.rag.indexer import IndexStats, index, provenance_of
from oracle.rag.retrieval import Retrieved, retrieve, to_citation
from oracle.rag.store import Hit, KnowledgeStore, SchemaMismatch

__all__ = [
    "E5_BASE",
    "PASSAGE",
    "QUERY",
    "Chunk",
    "Collection",
    "CollectionRegistry",
    "ContentKind",
    "Document",
    "Embedder",
    "Hit",
    "IndexStats",
    "KnowledgeStore",
    "ModelSpec",
    "Retrieved",
    "SchemaMismatch",
    "WalkStats",
    "chunk_document",
    "index",
    "load_registry",
    "provenance_of",
    "retrieve",
    "to_citation",
    "walk",
]
