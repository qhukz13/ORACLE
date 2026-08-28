"""Splitting documents into retrievable chunks.

**Chunk boundaries decide retrieval quality more than the embedding model does**
(docs/RAG.md#3), and the OQ-02 benchmark is the evidence: the gap between the best and
worst embedding candidate was smaller than the gap this module opened by packing tiny
blocks together instead of emitting them one per symbol.

Two invariants hold everywhere here:

* **Every chunk carries its ancestry, in the text as well as the metadata.** A section
  body reading "it converges fast, and needs far fewer examples" is unretrievable on its
  own; prefixed with `Fine-Tuning > Intuition` it is not. The same prefix is what makes a
  citation legible, so it is one mechanism serving two purposes.
* **Boundaries are computed in characters, not tokens.** That keeps chunking independent
  of the tokenizer, so a test, the indexer and the eval harness cannot produce different
  chunks from the same file. The cost is that the cap has to be *calibrated* against the
  model rather than derived from it — and until 2026-08-26 it was calibrated against an
  English-prose average that this corpus does not have, so 27% of embedded chunks
  overflowed the 512-token window and were silently truncated. Recalibrated and enforced;
  the rate is now 0.7%. See `MAX_CHARS`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from oracle.rag.collections import ContentKind, Document
from oracle.rag.pdf import PAGE_BREAK
from oracle.rag.treesitter import blocks_for

#: The ceiling on a chunk's **rendered** length — header, anchors and body together.
#:
#: MEASURED 2026-08-26 (`scripts/measure_truncation.py` and the density pass in
#: `logs/development/2026-08-26-oq18-chunking.md`). The old value was 1800, justified as
#: "~500 tokens of English prose". Two things were wrong with it and both are fixed here:
#:
#:   * **3.6 chars/token is not this corpus.** `bge-m3` tokenizes it at a median of 3.05
#:     (code) and 3.33 (markdown) chars per token, and at the 1st percentile 2.34 and
#:     2.42 — so the densest chunks reached 512 tokens at ~1200 characters, not 1800.
#:     27.1% of embedded chunks overflowed the model window and were silently truncated.
#:   * **the cap was applied to the body, not to the chunk.** `_pack` counted block
#:     bodies while emitting `header + anchor + body` per block, and `_window` counted
#:     lines while emitting `prefix + lines`. The longest "1800-character" chunk in the
#:     corpus was 4,055 characters.
#:
#: 1200 puts ~99% of code and markdown chunks inside the window at the 1st percentile of
#: density. It stays a **character** cap rather than a token cap on purpose: chunking
#: then needs no tokenizer, so a test, the indexer and the eval harness cannot produce
#: different chunks from the same file — which is the drift that made the previous
#: measurements describe the harness instead of the index.
MAX_CHARS = 1200

#: Below this, a block is not a chunk. Forty tokens of `export const X = 1` carries no
#: context to match a question against; it inflates the index and dilutes the neighbours
#: it should have been part of.
MIN_CHARS = 80

#: Consecutive small blocks are packed together until a chunk reaches this size.
PACK_TARGET = 700

#: Overlap between windows of an oversized block, as a fraction of its lines.
OVERLAP = 0.15

#: Bumped whenever a change here moves chunk boundaries.
#:
#: An index is disposable (ADR-0006) and reindexing must reproduce equivalent results —
#: but *incremental* indexing does not rebuild what is already there, so a boundary change
#: leaves a database whose old rows and new rows were cut by different rules. Nothing
#: fails; retrieval just gets quietly worse in a way no health check notices.
#:
#: `KnowledgeStore.bind` already refuses an index built by a different embedding model,
#: for exactly the same reason ("it returns confident nonsense"). This is that guard
#: extended to the other half of what makes a vector: what was in it.
#:
#:   1  the original character-budget chunker
#:   2  2026-08-26: MAX_CHARS 1800 -> 1200, and enforced against the *rendered* chunk
CHUNKER_VERSION = 2


@dataclass(frozen=True)
class Chunk:
    """One retrievable unit, with everything a citation needs already attached."""

    doc: Document
    ordinal: int
    #: Heading path for prose, symbol name for code. What a citation points at.
    anchor: str
    #: What gets embedded and indexed: the ancestry prefix followed by the body.
    text: str
    #: Obsidian `[[wikilinks]]` found in this chunk, for one-hop expansion (RAG.md §3).
    links: tuple[str, ...] = ()
    #: Front-matter tags and `#tags`, used as retrieval filters.
    tags: tuple[str, ...] = ()
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def semantic(self) -> bool:
        return self.doc.semantic


_WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
_HASHTAG = re.compile(r"(?:^|\s)#([A-Za-z][\w/-]*)")
_HEADING = re.compile(r"^(#{1,6})\s+(.*)")

#: A declaration that starts a new logical block, for languages `tree_sitter` has no
#: grammar for (PowerShell is the only one in this corpus). It is a rough approximation
#: and its limits are measured rather than assumed — see `_NOT_A_METHOD` and
#: `_OPENS_BLOCK` below, both of which exist because of things it got wrong on real files.
#: Anything the language pack covers goes through `treesitter.blocks_for` instead.
_SYMBOL = re.compile(
    r"^(?:export\s+)?(?:default\s+)?(?:async\s+)?"
    r"(?:(?:abstract\s+)?class|interface|type|enum|function|const|let|var|def|fn|impl"
    r"|struct|trait|pub\s+fn|pub\s+struct)"
    r"\s+([A-Za-z_$][\w$]*)"
)
#: Control flow that looks exactly like a method declaration to a line-based matcher.
#: Without this, `if (ok) {` at two spaces of indentation opens a new chunk anchored on
#: the symbol "if" — which is both a useless citation and a boundary in the middle of
#: the function body it should have stayed inside.
_NOT_A_METHOD = frozenset(
    {
        "if", "for", "while", "switch", "catch", "return", "do", "else", "elif",
        "with", "match", "case", "try", "except", "finally", "await", "yield",
        "throw", "new", "typeof", "delete", "in", "of", "and", "or", "not",
    }
)  # fmt: skip
_METHOD = re.compile(
    r"^\s{2,6}(?:public|private|protected|static|async|readonly|\s)*([A-Za-z_$][\w$]*)\s*\("
)


#: A declaration opens a block; a call statement closes itself. `foo() {`, `def foo():`
#: and a signature continued onto the next line all end in one of these, and
#: `assert.equal(a, b);` ends in none of them. Without this check the matcher treated
#: every indented call as a declaration — `equal` became the most common symbol in the
#: corpus at 863 occurrences, shredding every test file into call-sized fragments.
_OPENS_BLOCK = ("{", ":", "(", "=>", "->", ",")


def _symbol_at(line: str) -> str | None:
    """The symbol this line declares, if it declares one."""
    match = _SYMBOL.match(line)
    if match:
        return match.group(1)
    match = _METHOD.match(line)
    if match is None or match.group(1) in _NOT_A_METHOD:
        return None
    code = line.split("//")[0].split("#")[0].rstrip()
    return match.group(1) if code.endswith(_OPENS_BLOCK) else None


def _split_long_lines(text: str, room: int = MAX_CHARS) -> list[str]:
    """Lines, with anything longer than the whole budget cut mid-line.

    Generated and minified files are meant to be excluded before they reach here, but a
    176 KB single-line JSON blob got through the first benchmark run and produced one
    176 KB "chunk". A line-oriented splitter with no fallback silently emits whatever it
    cannot split, which is the failure mode worth guarding against.
    """
    out: list[str] = []
    for raw in text.split("\n"):
        rest = raw
        while len(rest) > room:
            out.append(rest[:room])
            rest = rest[room:]
        out.append(rest)
    return out


def _window(body: str, prefix: str, doc: Document, start: int, anchor: str) -> list[Chunk]:
    """Split one oversized block on line boundaries, with `OVERLAP` carried forward.

    Every chunk this emits carries `prefix`, so `prefix` spends part of every chunk's
    budget. Counting only the body — which is what this did until 2026-08-26 — is how a
    1800-character cap produced 4,055-character chunks."""
    out: list[Chunk] = []
    buf: list[str] = []
    room = max(MIN_CHARS, MAX_CHARS - len(prefix))
    size = 0
    for line in _split_long_lines(body, room):
        if size + len(line) > room and buf:
            out.append(Chunk(doc, start + len(out), anchor, prefix + "\n".join(buf)))
            # Overlap is capped in characters as well as in lines. A buffer of one very
            # long line has `int(1 * 0.15) == 0` lines to keep, and `max(1, ...)` would
            # retain the whole thing — producing chunks of two full budgets each, which
            # is how a 200 KB single-line blob still yielded oversized chunks after the
            # line splitter was added.
            keep = max(1, int(len(buf) * OVERLAP))
            buf = buf[-keep:]
            # `+ 1` per line, the same accounting the append below uses. Summing the
            # lengths alone under-counts the newlines the join will add, which is how a
            # config chunk came out nine characters over a 1200-character cap.
            size = sum(len(x) + 1 for x in buf)
            while buf and size > room * OVERLAP:
                size -= len(buf.pop(0)) + 1
        buf.append(line)
        size += len(line) + 1
    tail = "\n".join(buf)
    if tail.strip() and len(tail) >= MIN_CHARS:
        out.append(Chunk(doc, start + len(out), anchor, prefix + tail))
    return out


def _render(anchor: str, body: str) -> str:
    """One block, with its own anchor above it.

    Every block keeps its anchor even when several are packed into one chunk. Using only
    the group's first anchor loses the rest: a chunk holding `Fine-Tuning > What is it?`
    and `Fine-Tuning > Intuition` would name only the first, and a question about the
    intuition would have nothing to match against.
    """
    return f"{anchor}\n{body}" if anchor and anchor != "(file)" else body


def _pack(blocks: list[tuple[str, str]], doc: Document, header: str) -> list[Chunk]:
    """Combine consecutive `(anchor, body)` blocks into chunks of PACK_TARGET..MAX_CHARS.

    Merging happens only between neighbours within one document, so a chunk never spans
    two files, and the chunk's `anchor` is the first block's — where it starts.
    """
    out: list[Chunk] = []
    group: list[tuple[str, str]] = []
    # The header goes on every chunk and each block carries its own anchor line, so the
    # budget is what is *emitted*, not the sum of the bodies. `size` below tracks the
    # rendered length exactly.
    room = max(MIN_CHARS, MAX_CHARS - len(header))
    size = 0

    def flush() -> None:
        nonlocal group, size
        if not group:
            return
        body = "\n\n".join(_render(a, b) for a, b in group)
        # The first *named* block, not simply the first. A file's leading block is its
        # imports, anchored `(file)`; a chunk that also contains `TokenService` should
        # cite that, because "(file)" tells a reader nothing about what they are looking
        # at and a citation is only worth rendering if it names something.
        anchor = next((a for a, _ in group if a and a != "(file)"), group[0][0])
        out.append(Chunk(doc, len(out), anchor, header + body))
        group, size = [], 0

    for anchor, raw in blocks:
        body = raw.strip()
        if not body:
            continue
        rendered = _render(anchor, body)
        if len(rendered) > room:
            flush()
            out.extend(_window(body, f"{header}{anchor}\n", doc, len(out), anchor))
            continue
        if size + len(rendered) > room:
            flush()
        group.append((anchor, body))
        # `+ 2` for the "\n\n" that `flush()` joins blocks with. Over-counting by two at
        # the end of a group is the safe direction to be wrong in.
        size += len(rendered) + 2
        if size >= PACK_TARGET:
            flush()
    flush()
    return [c for c in out if len(c.text) >= MIN_CHARS]


def _front_matter(text: str) -> tuple[dict[str, Any], str]:
    """Split YAML front-matter from the body. Malformed front-matter is not fatal."""
    if not text.startswith("---\n"):
        return {}, text
    end = text.find("\n---", 4)
    if end == -1:
        return {}, text
    try:
        loaded = yaml.safe_load(text[4:end])
    except yaml.YAMLError:
        return {}, text[end + 4 :]
    return (loaded if isinstance(loaded, dict) else {}), text[end + 4 :]


def _tags_of(front: dict[str, Any], body: str) -> tuple[str, ...]:
    tags: list[str] = []
    raw = front.get("tags")
    if isinstance(raw, str):
        tags.extend(raw.replace(",", " ").split())
    elif isinstance(raw, list):
        tags.extend(str(t) for t in raw)
    for key in ("type", "domain", "difficulty", "status", "importance"):
        if isinstance(front.get(key), str):
            tags.append(f"{key}:{front[key]}")
    tags.extend(_HASHTAG.findall(body))
    return tuple(dict.fromkeys(tags))


def chunk_markdown(doc: Document, text: str, *, obsidian: bool = False) -> list[Chunk]:
    """Heading-aware, with the full heading path kept in every chunk.

    Obsidian specifics (RAG.md §3): front-matter becomes tags, `[[wikilinks]]` are
    extracted so retrieval can expand one hop to directly linked notes, and `#tags`
    become filters.
    """
    front, body = _front_matter(text)
    title = doc.path.rsplit("/", 1)[-1].removesuffix(".md").removesuffix(".mdx")
    tags = _tags_of(front, body) if obsidian or front else ()

    stack: list[str] = []
    sections: list[tuple[str, list[str]]] = []
    current: list[str] = []
    heading = title

    for line in body.split("\n"):
        match = _HEADING.match(line)
        if not match:
            current.append(line)
            continue
        if current:
            sections.append((heading, current))
        level = len(match.group(1))
        stack = stack[: level - 1]
        stack.append(match.group(2).strip())
        heading = " > ".join(stack)
        current = []
    if current:
        sections.append((heading, current))

    tag_line = " · ".join(tags)
    header = f"{doc.project} / {doc.path}\n" + (f"{tag_line}\n" if tag_line else "") + "\n"
    chunks = _pack([(h, "\n".join(b)) for h, b in sections], doc, header)
    if not chunks:  # front-matter and a single short paragraph
        chunks = _window(body.strip(), header, doc, 0, title)

    return [
        Chunk(
            doc=c.doc,
            ordinal=c.ordinal,
            anchor=c.anchor,
            text=c.text,
            links=tuple(dict.fromkeys(link.strip() for link in _WIKILINK.findall(c.text))),
            tags=tags,
            meta={"front_matter": front} if front else {},
        )
        for c in chunks
    ]


#: Use the syntax tree rather than the line matcher. **Off, on measured evidence.**
#:
#: tree-sitter unambiguously names symbols better — no control-flow keyword and no call
#: expression appears as an anchor anywhere in the corpus, against `equal` (548) and
#: `useEffect` (219) for the line matcher. But on the same corpus, with the same fixtures
#: and the same measurement code, it retrieves *worse*: recall@5 71-76% against 81%, across
#: four builds. Two fixtures separate them, and in both the line matcher wins by accident —
#: it packs neighbouring text together, so the file-header prose that answers a conceptual
#: question lands in the same chunk as the code.
#:
#: Better anchors are worth having and this is not the evidence to switch on: a 21-case set
#: where one case is 4.8 points cannot adjudicate a two-case difference in either
#: direction. The decision waits on the expanded fixture set (P5-T2 requirement 6), which is
#: why this is a flag and not a deletion.
#: [Log](../../../logs/development/2026-08-22-treesitter-chunking.md).
SYNTAX_AWARE = False


def chunk_code(doc: Document, text: str, *, syntax_aware: bool = SYNTAX_AWARE) -> list[Chunk]:
    """Symbol-boundary chunks, each prefixed with `project / path / symbol`.

    A real syntax tree where there is a grammar for the language and `syntax_aware` is on,
    and a line matcher otherwise. The difference is not cosmetic: the regex path cannot tell
    a call that takes a callback from a declaration, so `equal(...)` and `useEffect(...)`
    became the two most common "symbols" in the corpus. It is nonetheless the default —
    see `SYNTAX_AWARE`.
    """
    parsed = blocks_for(text, doc.abs_path) if syntax_aware else None
    if parsed is not None:
        header = f"{doc.project} / {doc.path}\n\n"
        return _pack([(b.anchor, b.text) for b in parsed], doc, header)

    blocks: list[tuple[str, list[str]]] = []
    name = "(file)"
    current: list[str] = []

    for line in text.split("\n"):
        symbol = _symbol_at(line)
        # Every declaration starts a block, however short the preceding one was.
        # Suppressing the split for a short block loses the *name*: a file whose imports
        # are 29 characters long would swallow `class TokenService` into a block anchored
        # `(file)`, and the symbol would never appear as an anchor at all. Short blocks
        # are not a problem to solve here — `_pack` recombines them, keeping each name.
        if symbol:
            if current:
                blocks.append((name, current))
            name, current = symbol, [line]
        else:
            current.append(line)
    if current:
        blocks.append((name, current))

    return _pack([(n, "\n".join(b)) for n, b in blocks], doc, f"{doc.project} / {doc.path}\n\n")


def chunk_pdf(doc: Document, text: str) -> list[Chunk]:
    """Page-aware chunks, anchored on the page number.

    `text` arrives from `rag.pdf.extract` with pages separated by `PAGE_BREAK`. Pages are
    packed like any other blocks, so a chunk usually spans two or three of them — a page
    is a printing artefact, not a unit of meaning, and 510 one-page chunks would be 510
    embeddings of running text cut mid-sentence.

    The anchor is `p. 12` because it is the only citation a PDF can offer that a reader can
    act on: there is no heading path to recover and no symbol to name.
    """
    header = f"{doc.project} / {doc.path}\n\n"
    pages = [(f"p. {n}", body) for n, body in enumerate(text.split(PAGE_BREAK), start=1)]
    return _pack([(a, b) for a, b in pages if b.strip()], doc, header)


def chunk_document(
    doc: Document, text: str, *, obsidian: bool = False, syntax_aware: bool = SYNTAX_AWARE
) -> list[Chunk]:
    """Dispatch on content kind. The only entry point callers should need."""
    if doc.kind is ContentKind.MARKDOWN:
        return chunk_markdown(doc, text, obsidian=obsidian)
    if doc.kind is ContentKind.CODE:
        return chunk_code(doc, text, syntax_aware=syntax_aware)
    if doc.kind is ContentKind.PDF:
        return chunk_pdf(doc, text)
    # Text and config both fall through to a recursive window. Config is chunked at all
    # only so it is searchable by exact string; `Document.semantic` is False for it, so
    # none of these chunks is ever embedded.
    return _window(text, f"{doc.project} / {doc.path}\n\n", doc, 0, "(file)")
