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
  of the tokenizer, which is what let the OQ-02 benchmark compare five embedding
  candidates against byte-identical chunks. It has a known cost, recorded rather than
  hidden: character budgets under-control token length on dense code, and roughly 20% of
  chunks exceed the 512-token model limit and are truncated. See `MAX_CHARS`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import yaml

from oracle.rag.collections import ContentKind, Document
from oracle.rag.treesitter import blocks_for

#: ~500 tokens of English prose. Deliberately below the 512-token limit of the E5
#: family, though not reliably so: identifier-dense code tokenizes at closer to one
#: token per three characters, so a minority of code chunks still truncate. A
#: token-aware splitter would fix that and would also make chunking depend on the
#: tokenizer — which is a trade worth making only once the model is fixed, and it now is.
#: `TO VERIFY` — measure what truncation costs recall before spending that complexity.
MAX_CHARS = 1800

#: Below this, a block is not a chunk. Forty tokens of `export const X = 1` carries no
#: context to match a question against; it inflates the index and dilutes the neighbours
#: it should have been part of.
MIN_CHARS = 80

#: Consecutive small blocks are packed together until a chunk reaches this size.
PACK_TARGET = 700

#: Overlap between windows of an oversized block, as a fraction of its lines.
OVERLAP = 0.15


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


def _split_long_lines(text: str) -> list[str]:
    """Lines, with anything longer than the whole budget cut mid-line.

    Generated and minified files are meant to be excluded before they reach here, but a
    176 KB single-line JSON blob got through the first benchmark run and produced one
    176 KB "chunk". A line-oriented splitter with no fallback silently emits whatever it
    cannot split, which is the failure mode worth guarding against.
    """
    out: list[str] = []
    for raw in text.split("\n"):
        rest = raw
        while len(rest) > MAX_CHARS:
            out.append(rest[:MAX_CHARS])
            rest = rest[MAX_CHARS:]
        out.append(rest)
    return out


def _window(body: str, prefix: str, doc: Document, start: int, anchor: str) -> list[Chunk]:
    """Split one oversized block on line boundaries, with `OVERLAP` carried forward."""
    out: list[Chunk] = []
    buf: list[str] = []
    size = 0
    for line in _split_long_lines(body):
        if size + len(line) > MAX_CHARS and buf:
            out.append(Chunk(doc, start + len(out), anchor, prefix + "\n".join(buf)))
            # Overlap is capped in characters as well as in lines. A buffer of one very
            # long line has `int(1 * 0.15) == 0` lines to keep, and `max(1, ...)` would
            # retain the whole thing — producing chunks of two full budgets each, which
            # is how a 200 KB single-line blob still yielded oversized chunks after the
            # line splitter was added.
            keep = max(1, int(len(buf) * OVERLAP))
            buf = buf[-keep:]
            size = sum(len(x) for x in buf)
            while buf and size > MAX_CHARS * OVERLAP:
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
        if len(body) > MAX_CHARS:
            flush()
            out.extend(_window(body, f"{header}{anchor}\n", doc, len(out), anchor))
            continue
        if size + len(body) > MAX_CHARS:
            flush()
        group.append((anchor, body))
        size += len(body) + 2
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


def chunk_document(
    doc: Document, text: str, *, obsidian: bool = False, syntax_aware: bool = SYNTAX_AWARE
) -> list[Chunk]:
    """Dispatch on content kind. The only entry point callers should need."""
    if doc.kind is ContentKind.MARKDOWN:
        return chunk_markdown(doc, text, obsidian=obsidian)
    if doc.kind is ContentKind.CODE:
        return chunk_code(doc, text, syntax_aware=syntax_aware)
    # Text and config both fall through to a recursive window. Config is chunked at all
    # only so it is searchable by exact string; `Document.semantic` is False for it, so
    # none of these chunks is ever embedded.
    return _window(text, f"{doc.project} / {doc.path}\n\n", doc, 0, "(file)")
