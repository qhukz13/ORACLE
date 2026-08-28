#!/usr/bin/env python
"""Embedding-candidate benchmark for OQ-02 — which model for a mixed RU/EN corpus?

    python scripts/eval_embeddings.py --dry-run              # corpus + chunk stats only
    python scripts/eval_embeddings.py --models e5-base,bge-m3

Answers [OQ-02](docs/OPEN_QUESTIONS.md#oq-02) with measurement rather than assumption:
recall@5 over the fixture set in `tests/fixtures/retrieval/cases.yaml`, against the real
corpus declared in `config/collections.yaml`, for each candidate — and the CPU throughput
that decides whether a full reindex fits the 10-minute budget.

Three properties make the comparison fair, and each was a deliberate choice:

  * **Chunking is model-independent.** Boundaries are computed in characters, not
    tokens, so every candidate sees byte-identical chunks. If chunking varied with the
    tokenizer, this script would be measuring two things at once.
  * **The distractor set is the whole corpus**, not the fixture files. Recall against a
    hand-picked 20-document corpus is not a measurement of anything.
  * **Minus the answer key.**  `ADDED 2026-08-26` ORACLE indexes ORACLE, and
    `tests/fixtures/retrieval/cases.yaml` lists every fixture question beside its
    expected path — so it was the strongest lexical match for 37 of the 38 queries that
    measure this system, and took a top-5 slot from each of them. `ANSWER_KEY` drops
    those documents from a ranking before it is scored. ORACLE's *prose* docs stay in;
    see the constant for why the line is drawn there.
  * **Hybrid is reported alongside dense.** The shipped retriever is dense + BM25 + RRF
    (RAG.md §5), so a model that loses on dense alone but wins in fusion is the one to
    ship. Dense-only is reported too, because it isolates the embedding.

This is a *measurement script*, not a unit test: it needs the ONNX models on disk and
costs tens of minutes of CPU, so it stays out of `scripts/check.py`.

Requires `onnxruntime`, `tokenizers`, `numpy`. Models are expected under
`D:/ORACLE/models/embeddings/<name>/onnx/` (see `--models-dir`); fetch them with
`scripts/fetch_embedding_models.py`.
"""

from __future__ import annotations

import argparse
import fnmatch
import hashlib
import json
import math
import os
import random
import re
import subprocess
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import numpy as np
import yaml

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests/fixtures/retrieval/cases.yaml"
COLLECTIONS = ROOT / "config/collections.yaml"

G, R, Y, B, D, X = "\033[32m", "\033[31m", "\033[33m", "\033[34m", "\033[2m", "\033[0m"

# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Candidate:
    """One embedding configuration under test."""

    name: str
    model_dir: str
    onnx_file: str
    dim: int
    pooling: Literal["mean", "cls"]
    query_prefix: str = ""
    passage_prefix: str = ""
    truncate_to: int | None = None  # Matryoshka-style: keep the first N dims, renormalise

    @property
    def out_dim(self) -> int:
        return self.truncate_to or self.dim


# E5 requires the query:/passage: prefixes. Getting them wrong silently halves quality,
# which is exactly why RAG.md §4 says a test must assert it — `--no-prefix` below exists
# to measure how much it actually costs on this corpus rather than repeat the folklore.
CANDIDATES: dict[str, Candidate] = {
    "e5-small": Candidate(
        "e5-small", "e5-small", "model.onnx", 384, "mean", "query: ", "passage: "
    ),
    "e5-base": Candidate("e5-base", "e5-base", "model.onnx", 768, "mean", "query: ", "passage: "),
    "e5-base-384": Candidate(
        "e5-base-384", "e5-base", "model.onnx", 768, "mean", "query: ", "passage: ", truncate_to=384
    ),
    "e5-base-int8": Candidate(
        "e5-base-int8",
        "e5-base",
        "model_qint8_avx512_vnni.onnx",
        768,
        "mean",
        "query: ",
        "passage: ",
    ),
    "bge-m3": Candidate("bge-m3", "bge-m3", "model.onnx", 1024, "cls"),
    "bge-m3-512": Candidate("bge-m3-512", "bge-m3", "model.onnx", 1024, "cls", truncate_to=512),
}

# ---------------------------------------------------------------------------
# Corpus
# ---------------------------------------------------------------------------

CODE_EXT = {
    ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".rs", ".go", ".java",
    ".c", ".h", ".cpp", ".hpp", ".cs", ".sh", ".ps1", ".sql", ".css", ".scss",
}  # fmt: skip
MARKDOWN_EXT = {".md", ".mdx"}
TEXT_EXT = {".txt", ".rst"}
# Lexical index only — an embedding of a tsconfig.json matches everything and means
# nothing (RAG.md §2).
CONFIG_EXT = {".json", ".yaml", ".yml", ".toml", ".ini", ".cfg", ".xml", ".env.example"}
# Extensionless files worth reading. Dockerfile.relay is a fixture answer.
NAMED = {"Dockerfile", "Makefile", "Justfile"}

#: Documents that must not be allowed to occupy a top-5 slot when scoring, because they
#: contain the questions being asked.  `MEASURED 2026-08-26, P9-T3`
#:
#: ORACLE indexes ORACLE — deliberately, and that is right for the product: a question
#: about ORACLE should reach ORACLE's own documentation. It is fatal for the
#: *measurement*, because `tests/fixtures/retrieval/cases.yaml` holds all 38 fixture
#: questions verbatim next to their expected answer paths, and is therefore the single
#: strongest lexical match for **37 of the 38 queries that measure this system**.
#:
#: That is the answer key sitting in the exam hall. It costs the lexical half one of its
#: five slots on nearly every English fixture, and it is why `en-relay-dockerfile` — the
#: one fixture whose answer is a config file that only BM25 can reach — is recorded as
#: a structural miss. It is not structural. It is fourth, behind the fixture file and
#: two documents *about* the fixture file.
#:
#: Excluded here rather than in `config/collections.yaml`, and the distinction is the
#: whole point: the corpus is not wrong, the scoring was. ORACLE's prose documentation
#: stays in — `docs/RAG.md` quoting a fixture question is a real document a real query
#: could really want, and pretending otherwise would be scoring against a corpus nobody
#: has. What comes out is only the file whose purpose is to list the answers.
ANSWER_KEY = ("ORACLE/tests/fixtures/",)


@dataclass
class Doc:
    collection: str
    project: str
    path: str  # corpus-relative, forward slashes — this is what a fixture matches on
    abs_path: Path
    kind: Literal["code", "markdown", "text", "config"]
    text: str


@dataclass
class Chunk:
    doc: Doc
    ordinal: int
    anchor: str  # heading path or symbol path
    text: str
    semantic: bool


def _matches(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, p) for p in patterns)


_DIRPAT = re.compile(r"^\*\*/([^*/]+)/\*\*$")


def _prune_names(patterns: list[str]) -> set[str]:
    """Directory names from `**/name/**` patterns, so the walk never descends into them.

    This is not an optimisation. `Path.rglob` enumerates `node_modules` in full before
    anything gets a chance to exclude it — on Asterim that is a walk of six figures of
    files to discard every one of them, and it took longer than embedding the corpus.
    Exclusion has to happen *during* traversal, which is also what RAG.md §6 requires of
    the watcher: drop before hashing, not after.
    """
    return {m.group(1) for p in patterns if (m := _DIRPAT.match(p))}


def _git_tracked(root: Path) -> set[str] | None:
    """Files git knows about, or None when `root` is not a repository.

    `respect_gitignore` is implemented by asking git rather than by reimplementing
    gitignore semantics. Two of the seven projects are not repositories at all, and for
    those the exclude globs are the only defence — which is the reason `**/target/**`
    is in collections.yaml and not left to a .gitignore that would never be read.
    """
    if not (root / ".git").exists():
        return None
    out = subprocess.run(  # noqa: S603
        ["git", "-C", str(root), "ls-files"],  # noqa: S607
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if out.returncode != 0:
        return None
    return {line.strip() for line in out.stdout.splitlines() if line.strip()}


def classify(p: Path) -> str | None:
    ext = p.suffix.lower()
    if ext in MARKDOWN_EXT:
        return "markdown"
    if ext in CODE_EXT:
        return "code"
    if ext in TEXT_EXT:
        return "text"
    if ext in CONFIG_EXT:
        return "config"
    if p.name in NAMED or p.name.startswith("Dockerfile"):
        return "config"
    return None


def load_corpus(verbose: bool = True) -> list[Doc]:
    cfg = yaml.safe_load(COLLECTIONS.read_text(encoding="utf-8"))
    deny: list[str] = cfg.get("deny", [])
    docs: list[Doc] = []
    skipped: Counter[str] = Counter()

    for coll in cfg["collections"]:
        if not coll.get("enabled", True):
            continue
        exclude: list[str] = coll.get("exclude", [])
        max_bytes: int = coll.get("max_file_bytes", 1_000_000)

        for root_s in coll["roots"]:
            root = Path(root_s)
            if not root.exists():
                print(f"{Y}  root missing: {root}{X}")
                continue

            if coll["id"] == "projects":
                units = [
                    (root / name, name)
                    for name in coll.get("include_projects", [])
                    if (root / name).is_dir()
                ]
            else:
                units = [(root, root.name)]

            prune = _prune_names(deny) | _prune_names(exclude) | {".git"}

            for unit_root, project in units:
                tracked = _git_tracked(unit_root) if coll.get("respect_gitignore") else None
                tracked_dirs: set[str] | None = None
                if tracked is not None:
                    tracked_dirs = {""}
                    for rel in tracked:
                        parts = rel.split("/")[:-1]
                        for i in range(len(parts)):
                            tracked_dirs.add("/".join(parts[: i + 1]))

                for dirpath, dirnames, filenames in os.walk(unit_root):
                    reldir = Path(dirpath).relative_to(unit_root).as_posix()
                    reldir = "" if reldir == "." else reldir
                    dirnames[:] = [
                        d
                        for d in dirnames
                        if d not in prune
                        and (tracked_dirs is None or f"{reldir}/{d}".lstrip("/") in tracked_dirs)
                    ]
                    for fname in filenames:
                        p = Path(dirpath) / fname
                        rel = f"{reldir}/{fname}".lstrip("/")
                        full = p.as_posix()
                        if _matches(full, deny) or _matches(rel, deny):
                            skipped["deny"] += 1
                            continue
                        if _matches(full, exclude) or _matches(rel, exclude):
                            skipped["exclude"] += 1
                            continue
                        if tracked is not None and rel not in tracked:
                            skipped["untracked"] += 1
                            continue
                        kind = classify(p)
                        if kind is None:
                            skipped["type"] += 1
                            continue
                        try:
                            if p.stat().st_size > max_bytes:
                                skipped["size"] += 1
                                continue
                            text = p.read_text(encoding="utf-8")
                        except (OSError, UnicodeDecodeError):
                            skipped["unreadable"] += 1
                            continue
                        if not text.strip():
                            continue
                        key = f"{project}/{rel}" if coll["id"] == "projects" else rel
                        docs.append(Doc(coll["id"], project, key, p, kind, text))  # type: ignore[arg-type]

    if verbose:
        print(f"{D}  skipped: {dict(skipped)}{X}")
    return docs


# ---------------------------------------------------------------------------
# Chunking (RAG.md §3) — deliberately in characters, so every candidate sees the
# same chunks. ~3.6 chars/token holds for English and understates Russian slightly.
# ---------------------------------------------------------------------------

MAX_CHARS = 1800  # ~500 tokens, kept under the 512-token model limit — see _pack
MIN_CHARS = 80
# Blocks smaller than this are packed together with their neighbours. A file of
# one-line `export const` declarations otherwise yields thirty chunks of forty
# tokens each, and a forty-token chunk carries no context to match against — it
# inflates the index and dilutes every neighbour it should have been part of.
PACK_TARGET = 700
OVERLAP = 0.15

_SYMBOL_RE = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:(?:abstract\s+)?class|interface|type|enum|function|const|let|var|def|fn|impl|struct|trait|pub\s+fn|pub\s+struct)"
    r"\s+([A-Za-z_$][\w$]*)"
)
_METHOD_RE = re.compile(
    r"^\s{2,6}(?:public|private|protected|static|async|readonly|\s)*([A-Za-z_$][\w$]*)\s*\("
)


def _window(text: str, prefix: str, doc: Doc, start_ord: int, anchor: str, semantic: bool):
    """Split an oversized block on line boundaries with 15% overlap.

    Lines longer than the whole budget are cut mid-line. Generated and minified files
    are meant to be excluded before they reach here, but a 176 KB single-line JSON blob
    got through the first run of this and produced one 176 KB "chunk" — a line-oriented
    splitter with no fallback silently emits whatever it cannot split.
    """
    lines: list[str] = []
    for raw in text.split("\n"):
        while len(raw) > MAX_CHARS:
            lines.append(raw[:MAX_CHARS])
            raw = raw[MAX_CHARS:]
        lines.append(raw)
    out: list[Chunk] = []
    buf: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) > MAX_CHARS and buf:
            body = "\n".join(buf)
            out.append(Chunk(doc, start_ord + len(out), anchor, prefix + body, semantic))
            keep = max(1, int(len(buf) * OVERLAP))
            buf = buf[-keep:]
            size = sum(len(x) for x in buf)
        buf.append(line)
        size += len(line) + 1
    if buf and len("\n".join(buf).strip()) >= MIN_CHARS:
        out.append(Chunk(doc, start_ord + len(out), anchor, prefix + "\n".join(buf), semantic))
    return out


def _pack(blocks: list[tuple[str, str]], doc: Doc, header: str, semantic: bool) -> list[Chunk]:
    """Greedily combine consecutive blocks into chunks of roughly PACK_TARGET..MAX_CHARS.

    `blocks` is (anchor, body) in file order. Merging only ever happens between
    neighbours in one file, so a chunk never spans two documents and the anchor of the
    first block in the group still names where it starts.
    """
    out: list[Chunk] = []
    group: list[tuple[str, str]] = []
    size = 0

    def flush() -> None:
        nonlocal group, size
        if not group:
            return
        anchor = group[0][0]
        body = "\n\n".join(b for _, b in group)
        prefix = f"{header}{anchor}\n\n" if anchor else header
        out.append(Chunk(doc, len(out), anchor, prefix + body, semantic))
        group, size = [], 0

    for anchor, body in blocks:
        body = body.strip()
        if not body:
            continue
        if len(body) > MAX_CHARS:
            flush()
            out.extend(_window(body, f"{header}{anchor}\n\n", doc, len(out), anchor, semantic))
            continue
        if size + len(body) > MAX_CHARS:
            flush()
        group.append((anchor, body))
        size += len(body) + 2
        if size >= PACK_TARGET:
            flush()
    flush()
    return [c for c in out if len(c.text) >= MIN_CHARS]


def chunk_markdown(doc: Doc) -> list[Chunk]:
    """Heading-aware: every chunk keeps its full heading path, in the text and out of it.

    The path goes into the chunk *text* because it is often the only thing naming the
    subject — a section body that says "it converges fast" is unretrievable without the
    `# Fine-Tuning` above it.
    """
    text = doc.text
    front: dict[str, Any] = {}
    if text.startswith("---\n"):
        end = text.find("\n---", 4)
        if end != -1:
            try:
                front = yaml.safe_load(text[4:end]) or {}
            except yaml.YAMLError:
                front = {}
            text = text[end + 4 :]

    tags = ""
    if isinstance(front, dict) and front:
        bits = [f"{k}: {v}" for k, v in front.items() if isinstance(v, (str, int, float, list))]
        tags = " · ".join(str(b) for b in bits)

    title = doc.path.rsplit("/", 1)[-1].removesuffix(".md")
    stack: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    cur: list[str] = []
    cur_head = title

    for line in text.split("\n"):
        m = re.match(r"^(#{1,6})\s+(.*)", line)
        if m:
            if cur:
                sections.append((cur_head, cur))
            level, head = len(m.group(1)), m.group(2).strip()
            stack = stack[: level - 1]
            stack.append(head)
            cur_head = " > ".join(stack)
            cur = []
        else:
            cur.append(line)
    if cur:
        sections.append((cur_head, cur))

    header = (
        f"{doc.project} / {doc.path}\n{title}\n{tags}\n"
        if tags
        else (f"{doc.project} / {doc.path}\n{title}\n")
    )
    chunks = _pack([(h, "\n".join(b)) for h, b in sections], doc, header, True)
    if not chunks:  # a note that is all front-matter and one paragraph
        chunks = _window(text.strip(), header, doc, 0, title, True)
    return chunks


def chunk_code(doc: Doc) -> list[Chunk]:
    """Symbol-boundary chunks with the ancestry retained.

    NOTE: this is a regex approximation of the tree-sitter chunker RAG.md §3 specifies.
    It is good enough to compare embedding models — every candidate sees the same
    boundaries — but the absolute recall numbers must be re-measured once tree-sitter
    lands, because better boundaries lift every model.
    """
    lines = doc.text.split("\n")
    blocks: list[tuple[str, list[str]]] = []
    cur_name = "(file)"
    cur: list[str] = []
    for line in lines:
        m = _SYMBOL_RE.match(line) or _METHOD_RE.match(line)
        if m and cur and len("\n".join(cur).strip()) >= MIN_CHARS:
            blocks.append((cur_name, cur))
            cur_name, cur = m.group(1), [line]
        elif m and not cur:
            cur_name, cur = m.group(1), [line]
        else:
            cur.append(line)
    if cur:
        blocks.append((cur_name, cur))

    return _pack(
        [(name, "\n".join(body)) for name, body in blocks],
        doc,
        f"{doc.project} / {doc.path} / ",
        True,
    )


def chunk_doc(doc: Doc) -> list[Chunk]:
    """Chunk with **the shipped chunker** (`oracle.rag.chunking`).

    This used to call the local copy above, and that was right for OQ-02: every embedding
    candidate had to see byte-identical chunks, and depending on `src/` would have made
    the comparison depend on whatever the index happened to be doing that week.

    It is wrong now, and measurably so. The model is fixed (OQ-02) and the two chunkers
    have **drifted**: on the same corpus the copy produced 12,770 chunks and the shipped
    one 11,727 (measured 2026-08-25, `scripts/measure_truncation.py`). A recall number
    computed over chunks the index does not produce describes this script, not ORACLE —
    and OQ-18's 61%/44% baseline was computed that way.

    The local `chunk_markdown` / `chunk_code` / `_pack` / `_window` below are kept, unused
    by this path, because `--models` comparisons recorded before this change were measured
    with them and deleting them would make those runs unreproducible.
    """
    from oracle.rag.chunking import chunk_document
    from oracle.rag.collections import ContentKind, Document

    shipped = Document(
        collection=doc.collection,
        project=doc.project,
        path=doc.path,
        abs_path=doc.abs_path,
        kind=ContentKind(doc.kind),
        size=len(doc.text),
        mtime_ns=0,
    )
    return chunk_document(  # type: ignore[return-value]
        shipped, doc.text, obsidian=doc.collection == "notes"
    )


def chunk_doc_legacy(doc: Doc) -> list[Chunk]:
    """The pre-2026-08-25 chunker, kept so older `--models` runs stay reproducible."""
    if doc.kind == "markdown":
        return chunk_markdown(doc)
    if doc.kind == "code":
        return chunk_code(doc)
    prefix = f"{doc.project} / {doc.path}\n\n"
    # Config is lexical-only: it still needs to be searchable by exact string, it just
    # must not pollute the vector space.
    return _window(doc.text, prefix, doc, 0, "(file)", doc.kind != "config")


# ---------------------------------------------------------------------------
# BM25 (the lexical half of RAG.md §5)
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z0-9_]+|[\u0400-\u04FF]+")


def lex_tokens(s: str) -> list[str]:
    """Lowercased words, with camelCase and snake_case also emitted as parts.

    `entitlementGuard` has to match a query for `entitlement`, and `MAX_YAML_DEPTH`
    has to match `yaml depth`. Emitting both the whole identifier and its parts is
    what makes the lexical half earn its place on a code corpus.
    """
    out: list[str] = []
    for tok in _WORD_RE.findall(s):
        low = tok.lower()
        out.append(low)
        parts = re.findall(r"[A-Z]+(?![a-z])|[A-Z][a-z0-9]*|[a-z0-9]+", tok)
        if len(parts) > 1:
            out.extend(p.lower() for p in parts)
        if "_" in tok:
            out.extend(p.lower() for p in tok.split("_") if p)
    return out


class BM25:
    def __init__(self, docs: list[list[str]], k1: float = 1.2, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.n = len(docs)
        self.len = np.array([len(d) for d in docs], dtype=np.float32)
        self.avgdl = float(self.len.mean()) if self.n else 1.0
        self.postings: dict[str, list[tuple[int, int]]] = defaultdict(list)
        for i, d in enumerate(docs):
            for term, tf in Counter(d).items():
                self.postings[term].append((i, tf))
        self.idf = {
            t: math.log(1 + (self.n - len(p) + 0.5) / (len(p) + 0.5))
            for t, p in self.postings.items()
        }

    def answerable(self, query: str, max_df_ratio: float = 0.10) -> bool:
        """Whether this query has any lexical purchase on the corpus at all.

        True when at least one query term appears in fewer than `max_df_ratio` of the
        documents. A term in *every* document discriminates nothing, and a term in *no*
        document is not evidence either — so a query made entirely of those two kinds is
        one BM25 can only answer with noise.
        """
        return any(
            0 < len(self.postings.get(term, ())) < self.n * max_df_ratio
            for term in set(lex_tokens(query))
        )

    def search(self, query: str, k: int) -> list[int]:
        scores = np.zeros(self.n, dtype=np.float32)
        for term in set(lex_tokens(query)):
            posting = self.postings.get(term)
            if not posting:
                continue
            idf = self.idf[term]
            for i, tf in posting:
                denom = tf + self.k1 * (1 - self.b + self.b * self.len[i] / self.avgdl)
                scores[i] += idf * (tf * (self.k1 + 1)) / denom
        top = np.argpartition(-scores, min(k, self.n - 1))[:k]
        return [int(i) for i in top[np.argsort(-scores[top])] if scores[i] > 0]


# ---------------------------------------------------------------------------
# Embedding
# ---------------------------------------------------------------------------


class Embedder:
    def __init__(self, cand: Candidate, models_dir: Path, threads: int, max_len: int) -> None:
        import onnxruntime as ort
        from tokenizers import Tokenizer

        base = models_dir / cand.model_dir / "onnx"
        self.cand = cand
        self.max_len = max_len
        self.tok = Tokenizer.from_file(str(base / "tokenizer.json"))
        self.tok.enable_truncation(max_len)
        self.tok.enable_padding()
        # A second, unpadded tokenizer, used only to measure lengths for batch sorting.
        self.len_tok = Tokenizer.from_file(str(base / "tokenizer.json"))
        self.len_tok.enable_truncation(max_len)

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = threads
        opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        t0 = time.perf_counter()
        self.sess = ort.InferenceSession(
            str(base / cand.onnx_file), opts, providers=["CPUExecutionProvider"]
        )
        self.load_s = time.perf_counter() - t0
        self.inputs = {i.name for i in self.sess.get_inputs()}
        self.model_bytes = sum(
            f.stat().st_size for f in base.glob(cand.onnx_file + "*") if f.is_file()
        )

    def encode(self, texts: list[str], batch: int = 16, sort: bool = True) -> np.ndarray:
        """Embed `texts`, returning vectors in the caller's order.

        Batches are formed from length-sorted texts. Padding is to the longest member of
        a batch, so a batch holding a 40-token chunk and a 500-token chunk pays 500
        tokens for both; grouping similar lengths together measured **1.8x** on this
        corpus (4.4 → 8.0 chunks/s, e5-small). It is free and it is the single largest
        CPU lever found, which is why it lives in the embedder rather than in the caller.
        """
        order = list(range(len(texts)))
        if sort and len(texts) > batch:
            # Measure with padding OFF. `self.tok` pads to the longest member of the
            # batch, so `len(e.ids)` there is the padded length — identical for every
            # text, and the sort silently becomes a no-op that costs a tokenisation pass
            # and buys nothing. This was live for one benchmark run before it was caught.
            lengths = [len(e.ids) for e in self.len_tok.encode_batch(texts)]
            order.sort(key=lambda i: lengths[i])

        out: list[np.ndarray] = []
        for i in range(0, len(order), batch):
            encs = self.tok.encode_batch([texts[j] for j in order[i : i + batch]])
            ids = np.array([e.ids for e in encs], dtype=np.int64)
            mask = np.array([e.attention_mask for e in encs], dtype=np.int64)
            feed: dict[str, np.ndarray] = {"input_ids": ids, "attention_mask": mask}
            if "token_type_ids" in self.inputs:
                feed["token_type_ids"] = np.zeros_like(ids)
            hidden = self.sess.run(None, {k: v for k, v in feed.items() if k in self.inputs})[0]
            if self.cand.pooling == "cls":
                vec = hidden[:, 0]
            else:
                m = mask[..., None].astype(np.float32)
                vec = (hidden * m).sum(1) / np.clip(m.sum(1), 1e-9, None)
            out.append(vec.astype(np.float32))

        stacked = np.vstack(out)
        restored = np.empty_like(stacked)
        restored[np.asarray(order)] = stacked
        return restored

    @staticmethod
    def finish(vecs: np.ndarray, truncate_to: int | None) -> np.ndarray:
        """Matryoshka truncation and L2 normalisation, applied after pooling.

        Split out from `encode` so that 768d and its 384d truncation are one forward
        pass rather than two — they are the same model, and re-running it to throw half
        the dimensions away would have doubled this benchmark's cost for nothing.
        """
        if truncate_to:
            vecs = vecs[:, :truncate_to]
        return vecs / np.clip(np.linalg.norm(vecs, axis=1, keepdims=True), 1e-9, None)


# ---------------------------------------------------------------------------
# The forward pass, saved
# ---------------------------------------------------------------------------


def corpus_fingerprint(
    chunks: list[Chunk], sem_idx: list[int], key: tuple[str, str, bool], max_len: int
) -> str:
    """What a saved vector file has to agree with before it may be reused.

    Everything the pooled array is a function of: the exact texts embedded, in order, and
    the model that embedded them. A chunker change, a corpus edit, a `--sample`, a
    different ONNX file or a different truncation length all move this.

    It is a hash rather than a version stamp on purpose. A stamp records what somebody
    remembered to bump; this records what was actually embedded, and this question has
    already lost two days to a number computed over chunks the system does not produce.
    """
    h = hashlib.sha256()
    h.update(f"{key}|{max_len}|{len(sem_idx)}|".encode())
    for i in sem_idx:
        h.update(chunks[i].text.encode("utf-8"))
        h.update(b"\x00")
    return h.hexdigest()


#: How many chunks are embedded between checkpoints. At bge-m3's ~1.1 chunks/s this is a
#: checkpoint every ~4 minutes, and the write itself is a few seconds of a ~50 MB .npz —
#: ~1% overhead. Two corpus runs (2026-08-27, 2026-08-28) were killed mid-pass and lost
#: everything, because the save only happened after the last batch; a kill now costs at
#: most one slice. Length-sorted batching happens per slice rather than globally, which
#: gives back a little of the 1.8x sorted-batch win; the vectors themselves are
#: unaffected (padding is masked out of the pooling).
CHECKPOINT_CHUNKS = 256


def save_vectors(path: str, raw: np.ndarray, fingerprint: str, complete: bool = True) -> None:
    """Write the pass so far. Atomic — a kill mid-write must not corrupt the checkpoint."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(p) + ".tmp.npz"
    np.savez_compressed(
        tmp, vectors=raw, fingerprint=np.array(fingerprint), complete=np.array(complete)
    )
    os.replace(tmp, p)


def load_vectors(path: str, fingerprint: str) -> tuple[np.ndarray, bool] | None:
    """The saved pass and whether it is complete, or None — a loud None, never a quiet
    wrong answer. A partial file (killed run) is returned for resumption; files written
    before the `complete` flag existed were only ever written after the full pass, so
    its absence means complete."""
    f = Path(path)
    if not f.exists():
        print(f"{Y}  no saved vectors at {path}; embedding from scratch{X}")
        return None
    data = np.load(f, allow_pickle=False)
    stored = str(data["fingerprint"])
    if stored != fingerprint:
        print(f"{R}  {path} was built from a different corpus or model; ignoring it{X}")
        print(f"{D}    saved {stored[:16]}…  wanted {fingerprint[:16]}…{X}")
        return None
    complete = bool(data["complete"]) if "complete" in data.files else True
    return np.asarray(data["vectors"]), complete


def keep_system_awake() -> None:
    """Tell Windows the machine is busy while this measurement runs.

    The 2026-08-28 corpus run lost twelve hours to the machine sleeping mid-pass
    (Kernel-Power 42 at 01:44, wake at 13:37) — a scheduled task does not keep the
    system awake on its own. ES_SYSTEM_REQUIRED does, for the life of this process;
    the display may still sleep. No-op off Windows.
    """
    if sys.platform == "win32":
        import ctypes

        es_continuous, es_system_required = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(es_continuous | es_system_required)


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


@dataclass
class Result:
    name: str
    dim: int
    dense: dict[str, float] = field(default_factory=dict)
    hybrid: dict[str, float] = field(default_factory=dict)
    lexical: dict[str, float] = field(default_factory=dict)
    chunks_per_s: float = 0.0
    query_ms_p50: float = 0.0
    query_ms_p95: float = 0.0
    load_s: float = 0.0
    model_mb: float = 0.0
    index_mb: float = 0.0
    misses: list[str] = field(default_factory=list)
    #: recall@5 per fusion strategy, so the choice of fusion is reported alongside the
    #: choice of model rather than assumed.
    fusion: dict[str, float] = field(default_factory=dict)


def rrf(*rankings: list[int], k: int = 60, weights: tuple[float, ...] | None = None) -> list[int]:
    """Reciprocal Rank Fusion. `weights` defaults to 1.0 for every list.

    RAG.md §5 chose RRF precisely because it has no tuned weights. The weighted form
    exists here to *measure* whether that property is costing recall, not because the
    default is in doubt — see the fusion sweep in `main`.
    """
    ws = weights or (1.0,) * len(rankings)
    score: dict[int, float] = defaultdict(float)
    for weight, ranking in zip(ws, rankings, strict=True):
        if weight == 0.0:
            continue
        for rank, idx in enumerate(ranking):
            score[idx] += weight / (k + rank + 1)
    return sorted(score, key=lambda i: -score[i])


#: Every arm printed per model, in the order a reader should compare them: the two
#: retrievers alone, the fusion variants, then the two translated probes.
#:
#: **None of these is "the shipped path" on its own**, and that has cost this question two
#: corrections already. `retrieve()` picks per query: a Russian question loses its lexical
#: terms to the script rule and runs `dense` (or `dense_mt`, if translation is on), while
#: an English one runs `gated`. The composition is done in the dev log from the miss
#: lists, which is why every arm prints its misses rather than only its score.
STRATEGIES = ("dense", "rrf", "rrf_w2", "gated", "dense_xl", "rrf_xl", "dense_mt", "rrf_mt")


def fusions(dense: list[int], lexical: list[int], bm25: BM25, query: str) -> dict[str, list[int]]:
    """Every fusion strategy under comparison, for one query.

    The one worth explaining is `gated`. On a Russian question against an English
    codebase, BM25 has nothing to contribute — the query shares no meaningful term with
    any document — but it still returns *thirty ranked results*, and unweighted RRF
    treats that noise as a second opinion of equal standing. It does not merely fail to
    help; it displaces correct dense hits out of the top 5. So the lexical list is
    admitted only when the query is something BM25 could plausibly answer: it must
    contain at least one term that occurs in the corpus and is not ubiquitous.
    """
    return {
        "dense": dense,
        "lexical": lexical,
        "rrf": rrf(dense, lexical),
        "rrf_w2": rrf(dense, lexical, weights=(2.0, 1.0)),
        "gated": rrf(dense, lexical) if bm25.answerable(query) else dense,
    }


def second_probe(
    emb: Embedder,
    cand: Candidate,
    prefix: str,
    cases: list[dict[str, Any]],
    texts: dict[str, str],
    dense_rank: dict[str, list[int]],
    rank_of: Any,
) -> dict[str, list[int]]:
    """Native dense ranking fused with a second dense probe from `texts` (OQ-18 lever 1).

    Only the *query* is re-embedded — 25 short strings against a corpus forward pass that
    is already paid for — so an arm is nearly free to measure and would not be free to
    run. A case with no entry in `texts` keeps its native ranking, so the English-only
    fixtures are unaffected and the comparison stays like-for-like everywhere.

    Two arms use this and the difference between them is the whole of P9-T3: `q_en` is a
    **human** translation and measures the ceiling of the idea; `--translations` is what
    the resident router model actually produced and measures the mechanism.
    """
    out = dict(dense_rank)
    picked = [c for c in cases if texts.get(c["id"])]
    if not picked:
        return out
    raw = np.vstack([emb.encode([prefix + texts[c["id"]]], batch=1)[0] for c in picked])
    for c, qv in zip(picked, Embedder.finish(raw, cand.truncate_to), strict=True):
        out[c["id"]] = rrf(dense_rank[c["id"]], rank_of(qv))
    return out


def hit(chunks: list[Chunk], ranked: list[int], expect: list[str], n: int) -> bool:
    seen_files: list[str] = []
    for idx in ranked:
        path = chunks[idx].doc.path
        if path not in seen_files:
            seen_files.append(path)
        if len(seen_files) >= n:
            break
    return any(any(e in f or f.endswith(e) for e in expect) for f in seen_files[:n])


def without_answer_key(chunks: list[Chunk], ranked: list[int]) -> list[int]:
    """The ranking with `ANSWER_KEY` documents dropped.

    Applied to the *ranking* rather than to the corpus so a saved forward pass stays
    valid: removing a document can only shift the entries below it up, which is exactly
    what a smaller corpus would have produced for recall@k. BM25's idf shifts by three
    documents in seventeen thousand, which is below the resolution of a 38-case gate.
    """
    return [i for i in ranked if not chunks[i].doc.path.startswith(ANSWER_KEY)]


def score_set(cases, chunks, rank_fn, *, answer_key: bool = False) -> tuple[dict, list[str]]:
    at1 = at5 = at10 = 0
    misses: list[str] = []
    by_kind: Counter[str] = Counter()
    kind_n: Counter[str] = Counter()
    for c in cases:
        ranked = rank_fn(c)
        if not answer_key:
            ranked = without_answer_key(chunks, ranked)
        kind_n[c["kind"]] += 1
        if hit(chunks, ranked, c["expect_any"], 1):
            at1 += 1
        if hit(chunks, ranked, c["expect_any"], 5):
            at5 += 1
            by_kind[c["kind"]] += 1
        else:
            misses.append(c["id"])
        if hit(chunks, ranked, c["expect_any"], 10):
            at10 += 1
    n = len(cases)
    out = {
        "recall@1": at1 / n,
        "recall@5": at5 / n,
        "recall@10": at10 / n,
    }
    for kind, total in kind_n.items():
        out[f"r@5/{kind}"] = by_kind[kind] / total
    return out, misses


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="e5-small,e5-base,e5-base-384,bge-m3,bge-m3-512")
    ap.add_argument("--models-dir", default="D:/ORACLE/models/embeddings")
    ap.add_argument("--threads", type=int, default=24)
    ap.add_argument("--max-len", type=int, default=512)
    ap.add_argument("--batch", type=int, default=16)
    ap.add_argument("--dry-run", action="store_true", help="corpus and chunk stats, no embedding")
    ap.add_argument("--limit", type=int, default=0, help="cap chunks, for a fast smoke run")
    ap.add_argument(
        "--sample",
        type=int,
        default=0,
        help="keep every chunk of every fixture-answer document plus a deterministic "
        "random sample of the rest, to compare candidates without embedding the whole "
        "corpus five times. Inflates absolute recall for every candidate equally; the "
        "winner is then confirmed on the full corpus.",
    )
    ap.add_argument("--no-prefix", action="store_true", help="drop E5 query:/passage: prefixes")
    ap.add_argument(
        "--translations",
        default="",
        help="a JSON file from scripts/translate_fixtures.py. Adds the `dense_mt`/`rrf_mt` "
        "arms: the same second probe as `dense_xl`, but embedding what the resident router "
        "model produced instead of the human translation in the fixture file.",
    )
    ap.add_argument(
        "--save-vectors",
        default="",
        help="write the pooled corpus vectors to an .npz beside their fingerprint. The "
        "forward pass is ~2 hours of CPU and every question asked of it since has been "
        "about the QUERY half; saving it makes the next one cost minutes.",
    )
    ap.add_argument(
        "--load-vectors",
        default="",
        help="reuse an .npz from --save-vectors. Refuses on a fingerprint mismatch rather "
        "than scoring stale vectors — a silently wrong reuse here is exactly the class of "
        "error this question has already been bitten by four times.",
    )
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    # A Windows console defaults to cp1252 and this script prints Russian fixture ids
    # and arrows. Without this the run dies on the first print, after the embedding.
    # Line-buffered, because the corpus run redirects stdout to a file and Python
    # block-buffers redirected stdout — the 2026-08-28 run sat at 63 bytes of log for
    # fifteen hours and its death left no trace of where it got to.
    sys.stdout.reconfigure(  # type: ignore[union-attr]
        encoding="utf-8", errors="replace", line_buffering=True
    )

    print(f"{B}corpus{X}")
    t0 = time.perf_counter()
    docs = load_corpus()
    chunks: list[Chunk] = []
    for d in docs:
        chunks.extend(chunk_doc(d))
    walk_s = time.perf_counter() - t0

    by_kind = Counter(d.kind for d in docs)
    by_coll = Counter(d.collection for d in docs)
    sem = sum(1 for c in chunks if c.semantic)
    print(f"  {len(docs)} docs {dict(by_coll)} {dict(by_kind)}")
    print(
        f"  {len(chunks)} chunks ({sem} semantic, {len(chunks) - sem} lexical-only) in {walk_s:.1f}s"
    )
    sizes = np.array([len(c.text) for c in chunks])
    print(
        f"  chars/chunk: mean {sizes.mean():.0f} p50 {np.percentile(sizes, 50):.0f} "
        f"p95 {np.percentile(sizes, 95):.0f} max {sizes.max()}"
    )

    cases = yaml.safe_load(FIXTURES.read_text(encoding="utf-8"))["cases"]

    # The model-translated arm. Absent, `dense_mt` is identical to `dense` and prints as
    # such — a missing file must not silently become a second copy of the human arm.
    model_translations: dict[str, str] = {}
    if args.translations:
        payload = json.loads(Path(args.translations).read_text(encoding="utf-8"))
        model_translations = {
            r["id"]: r["q_en_model"] for r in payload["translations"] if r.get("q_en_model")
        }
        print(
            f"{B}translations{X}  {payload.get('model')}: {len(model_translations)} of "
            f"{payload.get('cases')} usable, {payload.get('failures')} failed"
        )

    # A fixture whose expected file never made it into the corpus measures the walker,
    # not the model. Fail loudly rather than silently scoring 0.
    corpus_files = {c.doc.path for c in chunks}
    unreachable = [
        c["id"]
        for c in cases
        if not any(any(e in f or f.endswith(e) for f in corpus_files) for e in c["expect_any"])
    ]
    if unreachable:
        print(f"{R}  fixtures whose expected source is not in the corpus: {unreachable}{X}")
    else:
        print(f"{G}  all {len(cases)} fixture sources present in the corpus{X}")

    if args.dry_run:
        return 1 if unreachable else 0

    if args.sample:
        keep = [
            i
            for i, c in enumerate(chunks)
            if any(
                any(e in c.doc.path or c.doc.path.endswith(e) for e in case["expect_any"])
                for case in cases
            )
        ]
        rest = [i for i in range(len(chunks)) if i not in set(keep)]
        rng = random.Random(0xC0FFEE)  # noqa: S311 — sampling a corpus, not minting a key
        rng.shuffle(rest)
        idx = sorted(keep + rest[: max(0, args.sample - len(keep))])
        chunks = [chunks[i] for i in idx]
        print(f"{Y}  sampled to {len(chunks)} chunks ({len(keep)} from fixture-answer docs){X}")

    if args.limit:
        chunks = chunks[: args.limit]

    sem_idx = [i for i, c in enumerate(chunks) if c.semantic]
    print(f"\n{B}lexical index{X}")
    t0 = time.perf_counter()
    bm25 = BM25([lex_tokens(c.text) for c in chunks])
    print(
        f"  BM25 over {len(chunks)} chunks, {len(bm25.postings)} terms, {time.perf_counter() - t0:.1f}s"
    )

    lex_rank = {c["id"]: bm25.search(c["q"], 50) for c in cases}
    lex_scores, lex_misses = score_set(cases, chunks, lambda c: lex_rank[c["id"]])
    print(f"  recall@5 {lex_scores['recall@5']:.0%}   misses: {lex_misses}")
    # The lexical half is where the answer key does its damage, so its size is reported
    # where it happens: how many of the 38 queries put a fixture file in their top 5.
    keyed = sum(
        1
        for c in cases
        if len(without_answer_key(chunks, lex_rank[c["id"]])[:12]) < len(lex_rank[c["id"]][:12])
    )
    print(f"{D}  answer-key documents in the lexical candidates of {keyed}/{len(cases)} queries{X}")

    results: list[Result] = []
    # (model_dir, onnx_file, prefixed) -> pooled-but-not-yet-truncated vectors. A
    # truncation variant is the same forward pass as its parent, so it must not pay for
    # one: `bge-m3` and `bge-m3-512` together cost what `bge-m3` costs alone.
    pooled: dict[tuple[str, str, bool], tuple[np.ndarray, float, float, float]] = {}
    query_pool: dict[tuple[str, str, bool], tuple[np.ndarray, list[float]]] = {}

    for name in args.models.split(","):
        name = name.strip()
        cand = CANDIDATES[name]
        print(f"\n{B}{name}{X} ({cand.out_dim}d, {cand.pooling} pooling)")
        try:
            emb = Embedder(cand, Path(args.models_dir), args.threads, args.max_len)
        except Exception as exc:
            # A missing or unloadable model must not kill a run that has already spent
            # an hour on the candidates before it.
            print(f"{R}  unavailable: {exc}{X}")
            continue

        pp = "" if args.no_prefix else cand.passage_prefix
        qp = "" if args.no_prefix else cand.query_prefix
        key = (cand.model_dir, cand.onnx_file, not args.no_prefix)

        fingerprint = corpus_fingerprint(chunks, sem_idx, key, args.max_len)
        cached = load_vectors(args.load_vectors, fingerprint) if args.load_vectors else None
        if key in pooled:
            raw, cps, enc_s, model_mb = pooled[key]
            print(f"{D}  reusing the {cand.model_dir} forward pass ({enc_s:.0f}s){X}")
        elif cached is not None and cached[1]:
            raw, cps, enc_s, model_mb = cached[0], 0.0, 0.0, emb.model_bytes / 1e6
            pooled[key] = (raw, cps, enc_s, model_mb)
            print(f"{G}  loaded {len(raw)} corpus vectors from {args.load_vectors}{X}")
        else:
            keep_system_awake()
            texts = [pp + chunks[i].text for i in sem_idx]
            parts: list[np.ndarray] = []
            start = 0
            if cached is not None:
                parts, start = [cached[0]], len(cached[0])
                print(
                    f"{Y}  resuming a killed pass: {start}/{len(texts)} chunks already "
                    f"in {args.load_vectors}{X}"
                )
            t0 = time.perf_counter()
            for s in range(start, len(texts), CHECKPOINT_CHUNKS):
                parts.append(emb.encode(texts[s : s + CHECKPOINT_CHUNKS], batch=args.batch))
                raw = np.vstack(parts) if len(parts) > 1 else parts[0]
                parts = [raw]
                if args.save_vectors:
                    save_vectors(
                        args.save_vectors, raw, fingerprint, complete=len(raw) == len(texts)
                    )
                done_this_run = len(raw) - start
                elapsed = time.perf_counter() - t0
                rate = done_this_run / max(elapsed, 1e-9)
                eta = (len(texts) - len(raw)) / max(rate, 1e-9)
                print(
                    f"{D}  {len(raw)}/{len(texts)} chunks  {rate:.2f} chunks/s  "
                    f"elapsed {elapsed:.0f}s  eta {eta:.0f}s{X}"
                )
            raw = parts[0]
            enc_s = time.perf_counter() - t0
            cps = (len(raw) - start) / max(enc_s, 1e-9)
            model_mb = emb.model_bytes / 1e6
            pooled[key] = (raw, cps, enc_s, model_mb)
            resumed = f" ({start} reused)" if start else ""
            print(
                f"  embedded {len(raw) - start} chunks{resumed} in {enc_s:.1f}s → "
                f"{G}{cps:.1f} chunks/s{X} (load {emb.load_s:.1f}s, model {model_mb:.0f} MB)"
            )
            if args.save_vectors:
                print(f"{D}  saved the forward pass to {args.save_vectors}{X}")
        vecs = Embedder.finish(raw, cand.truncate_to)

        if key in query_pool:
            qraw, qtimes = query_pool[key]
        else:
            qvs: list[np.ndarray] = []
            qtimes = []
            for c in cases:
                t0 = time.perf_counter()
                qvs.append(emb.encode([qp + c["q"]], batch=1)[0])
                qtimes.append((time.perf_counter() - t0) * 1000)
            qraw = np.vstack(qvs)
            query_pool[key] = (qraw, qtimes)

        def rank_of(qv: np.ndarray, v: np.ndarray = vecs, si: list[int] = sem_idx) -> list[int]:
            sims = v @ qv
            top = np.argpartition(-sims, 50)[:50]
            return [si[i] for i in top[np.argsort(-sims[top])]]

        dense_rank: dict[str, list[int]] = {}
        for c, qv in zip(cases, Embedder.finish(qraw, cand.truncate_to), strict=True):
            dense_rank[c["id"]] = rank_of(qv)

        # OQ-18 lever 1, in two arms that differ only in who did the translating.
        #   `_xl` — the `q_en` human translations in the fixture file: the CEILING.
        #   `_mt` — whatever `--translations` holds, which is the resident router model's
        #           output as recorded by `scripts/translate_fixtures.py`: the MECHANISM.
        # Reporting them side by side is the point. The ceiling alone is what P9-T2 had,
        # and shipping on it would have been shipping on a number no running system can
        # produce.
        xl_rank = second_probe(
            emb,
            cand,
            qp,
            cases,
            {c["id"]: c["q_en"] for c in cases if c.get("q_en")},
            dense_rank,
            rank_of,
        )
        mt_rank = second_probe(emb, cand, qp, cases, model_translations, dense_rank, rank_of)

        # Every fusion strategy, scored against the same rankings. Free — the embedding
        # is already paid for, and it is the only way to see whether RRF's "no tuned
        # weights" property is buying robustness or costing recall.
        ranked = {
            c["id"]: fusions(dense_rank[c["id"]], lex_rank[c["id"]], bm25, c["q"]) for c in cases
        }
        # The translated arm scored through the *same* fusion, so the only difference
        # between `rrf` and `rrf_xl` is the second probe.
        for c in cases:
            ranked[c["id"]]["dense_xl"] = xl_rank[c["id"]]
            ranked[c["id"]]["rrf_xl"] = rrf(xl_rank[c["id"]], lex_rank[c["id"]])
            ranked[c["id"]]["dense_mt"] = mt_rank[c["id"]]
            ranked[c["id"]]["rrf_mt"] = rrf(mt_rank[c["id"]], lex_rank[c["id"]])
        fused: dict[str, dict[str, float]] = {}
        fused_misses: dict[str, list[str]] = {}
        for strategy in STRATEGIES:
            scores, missed = score_set(cases, chunks, lambda c, r=ranked, s=strategy: r[c["id"]][s])
            fused[strategy] = scores
            fused_misses[strategy] = missed

        dense_scores = fused["dense"]
        hyb_scores, hyb_misses = fused["rrf"], fused_misses["rrf"]

        res = Result(
            name=name,
            dim=cand.out_dim,
            dense=dense_scores,
            hybrid=hyb_scores,
            lexical=lex_scores,
            chunks_per_s=cps,
            query_ms_p50=float(np.percentile(qtimes, 50)),
            query_ms_p95=float(np.percentile(qtimes, 95)),
            load_s=emb.load_s,
            model_mb=emb.model_bytes / 1e6,
            index_mb=vecs.nbytes / 1e6,
            misses=hyb_misses,
            fusion={k: v["recall@5"] for k, v in fused.items()},
        )
        results.append(res)
        for strategy in STRATEGIES:
            scores = fused[strategy]
            best = max(fused[s]["recall@5"] for s in fused)
            colour = G if scores["recall@5"] >= best else ""
            print(
                f"  {strategy:<8}r@1 {scores['recall@1']:.0%}  {colour}r@5 "
                f"{scores['recall@5']:.0%}{X if colour else ''}  r@10 {scores['recall@10']:.0%}"
                f"   misses: {fused_misses[strategy]}"
            )
        # OQ-18 is a question about the Russian subset specifically, so it gets its own
        # line: an overall number that moved because the English cases improved would be
        # the wrong evidence entirely.
        ru_cases = [c for c in cases if c.get("lang") == "ru"]
        for strategy in ("dense", "rrf", "dense_xl", "dense_mt"):
            ru_scores, ru_missed = score_set(
                ru_cases, chunks, lambda c, r=ranked, st=strategy: r[c["id"]][st]
            )
            print(
                f"  RU-only {strategy:<7}r@5 {ru_scores['recall@5']:.0%}  "
                f"r@10 {ru_scores['recall@10']:.0%}   misses: {ru_missed}"
            )
        # The same arms scored the way this project scored them until 2026-08-26: with
        # `tests/fixtures/retrieval/cases.yaml` — the file listing all 38 questions and
        # their answers — still eligible for a top-5 slot. Printed so every number
        # recorded before today stays comparable to the numbers recorded after it, and
        # so the size of the leak is a figure rather than an assertion. See ANSWER_KEY.
        leak = {
            s: score_set(cases, chunks, lambda c, r=ranked, st=s: r[c["id"]][st], answer_key=True)[
                0
            ]["recall@5"]
            for s in STRATEGIES
        }
        moved = [s for s in STRATEGIES if abs(leak[s] - fused[s]["recall@5"]) > 1e-9]
        print(
            f"{D}  with the answer key eligible (pre-2026-08-26 scoring): "
            + "  ".join(f"{s} {leak[s]:.0%}" for s in STRATEGIES)
            + (f"  → moved: {moved}" if moved else "  → moved nothing")
            + X
        )
        print(
            f"  query {res.query_ms_p50:.0f} ms p50 / {res.query_ms_p95:.0f} ms p95, "
            f"index {res.index_mb:.0f} MB"
        )

    print(f"\n{B}summary{X}  (dense-only → hybrid with BM25+RRF; gate is recall@5 ≥ 80%)")
    # The header named four columns while the row below printed five numbers, so `RU@5`
    # sat under `rrf_w2` and every reader of this table was off by one. Fixed 2026-08-26;
    # the widths here are the row's, field for field.
    hdr = (
        f"  {'model':<14}{'dim':>5}{'dense@5':>7}{'rrf@5':>7}{'rrf_w2':>8}"
        f"{'gated':>7}{'RU@5':>7}{'chunks/s':>10}{'idx MB':>8}"
    )
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))
    for r in results:
        best = max(r.fusion.values(), default=0.0)
        mark = G if best >= 0.8 else R
        print(
            f"  {r.name:<14}{r.dim:>5}{mark}{r.fusion.get('dense', 0):>7.0%}"
            f"{r.fusion.get('rrf', 0):>7.0%}{r.fusion.get('rrf_w2', 0):>8.0%}"
            f"{r.fusion.get('gated', 0):>7.0%}{X}"
            f"{r.hybrid.get('r@5/crosslang', 0):>7.0%}"
            f"{r.chunks_per_s:>10.1f}{r.index_mb:>8.0f}"
        )

    if args.out:
        Path(args.out).write_text(
            json.dumps(
                {
                    "corpus": {"docs": len(docs), "chunks": len(chunks), "semantic": sem},
                    "lexical_only": lex_scores,
                    "results": [r.__dict__ for r in results],
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"\n{D}  wrote {args.out}{X}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
