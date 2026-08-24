"""Embeddings: ONNX Runtime, on the CPU, in a process that is not the event loop's.

The GPU holds the router model permanently (ADR-0014, TECH_STACK §4). Sharing 4 GB
between an LLM and an embedding model causes load/unload thrash that costs far more than
the embedding compute saves, and there are 24 idle threads sitting next to it. So
indexing is a CPU batch job, and its latency does not matter — the router's does.

The model is named in configuration rather than hard-coded, but the **dimension is fixed
at index build time**: changing the model means rebuilding `knowledge.db`. That is
tolerable precisely because the index is disposable by design (ADR-0006), and it is why
[OQ-02](../../../docs/OPEN_QUESTIONS.md#oq-02) had to be answered before any of this was
written.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import numpy as np

from oracle.logsink import get_logger

if TYPE_CHECKING:  # pragma: no cover - import cost is real and only needed at runtime
    import onnxruntime as ort
    from tokenizers import Tokenizer

log = get_logger(__name__)

#: The E5 family is trained with asymmetric prefixes and silently loses roughly half its
#: quality without them — no error, just worse answers. The indexer and the retriever
#: must agree, which is why the prefixes live on the model spec rather than at the two
#: call sites, and why `tests/test_rag_embedding.py` asserts they differ.
QUERY = "query"
PASSAGE = "passage"


@dataclass(frozen=True)
class ModelSpec:
    """Everything that has to match between indexing and querying."""

    name: str
    #: Directory holding `model.onnx` and `tokenizer.json`.
    path: Path
    dim: int
    pooling: Literal["mean", "cls"]
    query_prefix: str = ""
    passage_prefix: str = ""
    max_tokens: int = 512
    #: Matryoshka-style truncation. `None` keeps the model's native dimension.
    truncate_to: int | None = None

    @property
    def out_dim(self) -> int:
        return self.truncate_to or self.dim

    def prefix(self, role: str) -> str:
        return self.query_prefix if role == QUERY else self.passage_prefix


#: Shipped from 2026-08-22 to 2026-08-24 and kept as a real option, not a rejected one:
#: it is a third of the indexing cost and half the resident memory, and on English
#: questions it scores what `bge-m3` scores. It loses on the cross-language column this
#: project is built around — 36% against 44% — which is what decided OQ-02.
E5_BASE = ModelSpec(
    name="multilingual-e5-base",
    path=Path("D:/ORACLE/models/embeddings/e5-base/onnx"),
    dim=768,
    pooling="mean",
    query_prefix="query: ",
    passage_prefix="passage: ",
)

E5_SMALL = ModelSpec(
    name="multilingual-e5-small",
    path=Path("D:/ORACLE/models/embeddings/e5-small/onnx"),
    dim=384,
    pooling="mean",
    query_prefix="query: ",
    passage_prefix="passage: ",
)

#: Chosen by measurement in OQ-02, not by reputation, and only on the second attempt:
#: `logs/development/2026-08-24-oq02-bge-m3.md` has the numbers. Over the full corpus and
#: 38 fixtures it scores **61% recall@5 and 44% on Russian questions**, against e5-base's
#: 55% and 36%. Measured through the *old* fusion gate it lost, 53% — the gate admitted
#: BM25 on every query, and fusion can only displace a correct dense hit that exists, so
#: the better dense half was the one being damaged. The gate fix shipped with this switch
#: and neither is worth much without the other.
#:
#: It costs ~2.5 h for a cold build (1.37 chunks/s) and ~3 GB resident against ~1.5 GB.
#: The cold build is paid once per model — see OQ-17 — and p95 is 332 ms, inside the
#: 400 ms budget.
#:
#: BGE-M3 uses CLS pooling and no prefixes; both differ from E5 and both matter.
#: Switching means rebuilding: `KnowledgeStore.bind` refuses an index built by the other.
BGE_M3 = ModelSpec(
    name="bge-m3",
    path=Path("D:/ORACLE/models/embeddings/bge-m3/onnx"),
    dim=1024,
    pooling="cls",
)

#: What the indexer and the `know.*` tools use. One name to change.
DEFAULT = BGE_M3


def normalise(spec: ModelSpec, vecs: np.ndarray) -> np.ndarray:
    """Truncate, then normalise. The order matters, and getting it wrong is silent.

    Normalising before truncation would leave the kept dimensions with a norm below one
    that varies per row, so cosine similarity would stop being a dot product and every
    score would be quietly, uniformly wrong.

    A module-level function rather than a method because it is pure, and because being
    able to test it without loading 1.1 GB of weights is the difference between this
    property being asserted and being assumed.
    """
    if spec.truncate_to:
        vecs = vecs[:, : spec.truncate_to]
    return np.asarray(
        vecs / np.clip(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9, None),
        dtype=np.float32,
    )


class Embedder:
    """One loaded ONNX session. Not thread-safe for concurrent `encode` calls."""

    def __init__(self, spec: ModelSpec, *, threads: int = 24) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self.spec = spec
        model = spec.path / "model.onnx"
        if not model.exists():
            raise FileNotFoundError(f"{model} is missing. Run scripts/fetch_embedding_models.py.")

        self._tok: Tokenizer = Tokenizer.from_file(str(spec.path / "tokenizer.json"))
        self._tok.enable_truncation(spec.max_tokens)
        self._tok.enable_padding()
        # A second, deliberately unpadded tokenizer, used only to measure lengths for
        # batch sorting. Measuring with the padded one returns the padded length for
        # every text, which makes the sort a silent no-op — it was, for one whole
        # benchmark run, and cost 1.8x of throughput without failing anything.
        self._len_tok: Tokenizer = Tokenizer.from_file(str(spec.path / "tokenizer.json"))
        self._len_tok.enable_truncation(spec.max_tokens)

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.inter_op_num_threads = 1
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._sess: ort.InferenceSession = ort.InferenceSession(
            str(model), opts, providers=["CPUExecutionProvider"]
        )
        self._inputs = {i.name for i in self._sess.get_inputs()}
        self._lock = threading.Lock()
        log.info("rag.embedder_loaded", model=spec.name, dim=spec.out_dim, threads=threads)

    def encode(self, texts: list[str], role: str, *, batch: int = 16) -> np.ndarray:
        """Embed `texts` as queries or as passages, returning vectors in input order.

        `role` is not optional and has no default. An asymmetric model with the wrong
        prefix is the classic silent failure in this subsystem: nothing raises, recall
        just drops. Making the caller name the role every time is the cheapest possible
        guard against writing one side of the pair and forgetting the other.
        """
        if role not in (QUERY, PASSAGE):
            raise ValueError(f"role must be {QUERY!r} or {PASSAGE!r}, got {role!r}")
        if not texts:
            return np.zeros((0, self.spec.out_dim), dtype=np.float32)

        prefix = self.spec.prefix(role)
        prepared = [prefix + t for t in texts]

        # Batches are formed from length-sorted texts: padding is to the longest member
        # of a batch, so mixing a 40-token chunk with a 500-token one pays 500 tokens for
        # both. Measured at 1.8x on this corpus (4.4 -> 8.0 chunks/s, e5-small).
        order = list(range(len(prepared)))
        if len(prepared) > batch:
            lengths = [len(e.ids) for e in self._len_tok.encode_batch(prepared)]
            order.sort(key=lambda i: lengths[i])

        out: list[np.ndarray] = []
        with self._lock:
            for start in range(0, len(order), batch):
                out.append(self._forward([prepared[i] for i in order[start : start + batch]]))

        stacked = np.vstack(out)
        restored = np.empty_like(stacked)
        restored[np.asarray(order)] = stacked
        return normalise(self.spec, restored)

    def _forward(self, batch: list[str]) -> np.ndarray:
        encoded = self._tok.encode_batch(batch)
        ids = np.array([e.ids for e in encoded], dtype=np.int64)
        mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
        feed: dict[str, np.ndarray] = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self._sess.run(None, {k: v for k, v in feed.items() if k in self._inputs})[0]

        if self.spec.pooling == "cls":
            return np.asarray(hidden[:, 0], dtype=np.float32)
        weights = mask[..., None].astype(np.float32)
        pooled = (hidden * weights).sum(1) / np.clip(weights.sum(1), 1e-9, None)
        return np.asarray(pooled, dtype=np.float32)
