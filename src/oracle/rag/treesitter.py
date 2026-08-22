"""Real syntax trees for code chunking, replacing a regex that had measured limits.

[RAG.md §3](../../../docs/RAG.md#3-chunking) always specified tree-sitter here. The regex
approximation that shipped first was honest about being one, and its failure mode was
measured rather than imagined: across the corpus, the most common "symbol" it found was
`equal` (548 occurrences), followed by `useEffect` (219). Both are *calls*, not
declarations — a call that takes a callback opens a block, and to a line matcher that is
indistinguishable from a definition. Every test file was being shredded into call-sized
fragments anchored on the name of an assertion.

Three properties this module has that the regex could not:

* **Names are names.** They come from the `name` field of a declaration node, so a
  control-flow keyword or a call expression cannot become one.
* **Ancestry is real.** A method's anchor is `TokenService.signAccessToken`, built from the
  nodes that actually enclose it rather than from indentation.
* **The whole file is still covered.** Imports and top-level statements between
  declarations are emitted as their own blocks, so nothing silently stops being indexed
  because the parser had no name for it.

**Failure is a fallback, never an exception.** tree-sitter is error-tolerant and returns a
tree for broken input; a language it does not know, or a file it cannot find declarations
in, returns `None` here and the caller uses the line-based splitter. A chunker that raised
on a half-written file would take the index down for a syntax error.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from oracle.logsink import get_logger

if TYPE_CHECKING:  # pragma: no cover - the import costs ~40 ms and is only needed to parse
    from tree_sitter import Node

log = get_logger(__name__)

#: Suffix -> grammar. Only languages verified to load on this machine
#: (`tree-sitter-language-pack` 1.14.3, one abi3 wheel). `.ps1` is deliberately absent —
#: the pack has no PowerShell grammar, so it keeps the line-based splitter.
LANGUAGES: dict[str, str] = {
    ".ts": "typescript", ".tsx": "tsx",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".py": "python", ".rs": "rust", ".go": "go", ".java": "java",
    ".c": "c", ".h": "c", ".cpp": "cpp", ".hpp": "cpp", ".cs": "csharp",
    ".lua": "lua", ".rb": "ruby", ".php": "php", ".kt": "kotlin", ".swift": "swift",
    ".sh": "bash", ".sql": "sql", ".css": "css", ".scss": "css",
}  # fmt: skip

#: Node types that are a named declaration worth anchoring a chunk on.
#: Deliberately a denylist-free allowlist: an unknown node type is *not* a definition, so a
#: grammar change adds no spurious anchors — it only stops finding some.
DEFINITIONS: frozenset[str] = frozenset(
    {
        # TypeScript / JavaScript
        "class_declaration", "abstract_class_declaration", "function_declaration",
        "generator_function_declaration", "method_definition", "interface_declaration",
        "type_alias_declaration", "enum_declaration", "lexical_declaration",
        "variable_declaration", "public_field_definition",
        # Python
        "function_definition", "class_definition", "decorated_definition",
        # Rust
        "function_item", "impl_item", "struct_item", "trait_item", "enum_item", "mod_item",
        # Go / Java / C-family
        "method_declaration", "type_declaration", "struct_specifier", "class_specifier",
        "function_definition_c", "constructor_declaration",
        # Ruby / PHP / Kotlin / Swift / Lua
        "method", "singleton_method", "module", "class", "function_declaration_lua",
        "object_declaration", "protocol_declaration",
    }
)  # fmt: skip

#: Node types worth walking *through* to find declarations inside them. A function body is
#: absent on purpose: a nested closure is part of the function that contains it, and
#: splitting there is how a body loses the signature that explains it.
CONTAINERS: frozenset[str] = frozenset(
    {
        "program", "module", "source_file", "translation_unit",
        "export_statement", "decorated_definition", "expression_statement",
        "class_body", "class_declaration", "abstract_class_declaration", "class_definition",
        "interface_declaration", "interface_body", "object_type",
        "declaration_list", "impl_item", "trait_item", "block", "body",
        "enum_declaration", "enum_body", "namespace_definition",
    }
)  # fmt: skip


@dataclass(frozen=True)
class Block:
    """One span of a file, with the symbol path that names it."""

    anchor: str
    text: str


def language_for(path: Path) -> str | None:
    return LANGUAGES.get(path.suffix.lower())


def _parser(language: str) -> Any | None:
    from tree_sitter_language_pack import get_parser

    try:
        return get_parser(language)
    except (LookupError, ValueError, OSError) as exc:
        log.warning("rag.grammar_unavailable", language=language, error=str(exc))
        return None


def _name_of(node: Node, src: bytes) -> str | None:
    """The declared name, from the grammar's `name` field where there is one."""
    field = node.child_by_field_name("name")
    if field is not None:
        return src[field.start_byte : field.end_byte].decode("utf-8", "replace")
    for child in node.children:
        if child.type in {
            "identifier",
            "type_identifier",
            "property_identifier",
            "field_identifier",
        }:
            return src[child.start_byte : child.end_byte].decode("utf-8", "replace")
    # `const handler = () => {}` hangs its name off a declarator rather than a field.
    for child in node.children:
        if child.type in {"variable_declarator", "init_declarator"}:
            return _name_of(child, src)
    return None


def _inner_definitions(node: Node, src: bytes) -> list[tuple[Node, str]]:
    """Named declarations directly inside `node`, seen through container nodes only."""
    found: list[tuple[Node, str]] = []
    for child in node.children:
        if child.type in DEFINITIONS:
            name = _name_of(child, src)
            if name:
                found.append((child, name))
                continue
            # An anonymous declaration is not an anchor, but what is inside it may be.
        if child.type in CONTAINERS or child.type in DEFINITIONS:
            found.extend(_inner_definitions(child, src))
    return found


#: Line starts that introduce a declaration rather than standing on their own: doc comments
#: in every syntax this pack covers, plus decorators and attributes.
_TRIVIA: tuple[bytes, ...] = (b"//", b"/*", b"*", b"#", b"--", b"@", b'"""', b"'''")


def _lead_start(src: bytes, gap_start: int, decl_start: int) -> int:
    """Where a declaration really begins, counting the trivia that introduces it.

    The grammar reports a node starting at `type SecretSettingKey`, not at the `export`
    in front of it, and not at the `/** ... */` above it. Slicing at the node's own start
    therefore severs a doc comment from the constant it documents and strands the bare word
    `export` as a block of its own — and neither fragment is valid source or reads as
    anything alone. Both were happening: see the block dump in
    `logs/development/2026-08-22-treesitter-chunking.md`.

    Works in bytes throughout, because the offsets are byte offsets and decoding with
    `replace` does not preserve them.
    """
    gap = src[gap_start:decl_start]
    if not gap.strip():
        return gap_start  # whitespace only — all of it belongs to the declaration

    lines = gap.splitlines(keepends=True)
    kept = 0
    # A gap not ending in a newline shares its last line with the declaration
    # (`export type X = ...`). Splitting there would cut a source line in half.
    if not gap.endswith((b"\n", b"\r")):
        kept = 1
    # Contiguous only: the walk stops at a blank line, because a blank line is the author
    # saying the comment above it is not about the thing below it. The distinction is
    # worth 5 points of recall@5 and was measured, not reasoned: a file's leading
    # `/** ... */` describes the file, and gluing it to whichever constant happens to come
    # first buries the one piece of prose that explains the module among `const X = 12;`
    # lines. A method's JSDoc sits directly on the method and does belong to it.
    while kept < len(lines):
        line = lines[-1 - kept].strip()
        if not line or not line.startswith(_TRIVIA):
            break
        kept += 1
    if not kept:
        return decl_start
    start = decl_start - len(b"".join(lines[len(lines) - kept :]))
    # If trimming leaves nothing but whitespace behind, take that too. `_emit` drops a
    # blank gap rather than emitting it, so leaving it here would delete the blank lines
    # between declarations — text loss, which is the one thing chunking may never do.
    return gap_start if not src[gap_start:start].strip() else start


def _emit(
    node: Node,
    src: bytes,
    prefix: str,
    out: list[Block],
    *,
    depth: int = 0,
    start: int | None = None,
) -> None:
    """Partition `node` into non-overlapping blocks, innermost declarations first.

    A declaration with declarations inside it (a class holding methods) contributes its own
    header — everything before the first inner one — and then each inner one separately.
    That keeps a large class from becoming a single oversized chunk while its field
    declarations and its `class Foo {` line still get indexed under the class's own name.
    """
    begin = node.start_byte if start is None else start
    inner = _inner_definitions(node, src) if depth < 4 else []
    if not inner:
        text = src[begin : node.end_byte].decode("utf-8", "replace")
        if text.strip():
            out.append(Block(prefix or "(file)", text))
        return

    # Every gap is emitted, however small. An earlier version dropped gaps under 40
    # bytes as "a blank line and a brace" — and silently lost `import crypto from
    # 'crypto';` and the `export class Foo {` line itself, because both are shorter than
    # that. Chunking may merge text or re-anchor it; it may never discard it. Small blocks
    # are not a problem to solve here: `_pack` combines them with their neighbours.
    cursor = begin
    for child, name in inner:
        lead = _lead_start(src, cursor, child.start_byte)
        gap = src[cursor:lead].decode("utf-8", "replace")
        if gap.strip():
            out.append(Block(prefix or "(file)", gap))
        path = f"{prefix}.{name}" if prefix else name
        _emit(child, src, path, out, depth=depth + 1, start=lead)
        cursor = child.end_byte

    tail = src[cursor : node.end_byte].decode("utf-8", "replace")
    if tail.strip():
        out.append(Block(prefix or "(file)", tail))


def _coalesce(blocks: list[Block]) -> list[Block]:
    """Fold punctuation-only blocks into their predecessor.

    A grammar's `public_field_definition` stops before the `;` that ends it, so a class of
    ten fields leaves ten one-character blocks between them. Each one is a *block*, so the
    chunker writes its anchor above it, and a chunk of `RelayClient` fields came out as
    `RelayClient\\n;` five times over — noise that pushed the real code out of the chunk and
    the chunk from dense rank 2 to rank 18 on a fixture query. Measured in
    `logs/development/2026-08-22-treesitter-chunking.md`.

    The text is appended, never dropped: `;` belongs to the statement in front of it.
    """
    out: list[Block] = []
    for block in blocks:
        if out and not any(ch.isalnum() for ch in block.text):
            out[-1] = Block(out[-1].anchor, out[-1].text + block.text)
            continue
        out.append(block)
    return out


def blocks_for(text: str, path: Path) -> list[Block] | None:
    """Ordered, non-overlapping blocks covering `text`, or None to use the fallback.

    None means only "there is no grammar for this" — an unsupported language, or a pack
    that failed to load. A file the parser *could* read always returns blocks, even when
    it declares nothing, because the line-based fallback is worse at those files rather
    than better. It never means the file is broken either: tree-sitter parses broken input
    happily, and half a syntax tree still names more symbols correctly than a regex does.
    """
    language = language_for(path)
    if language is None:
        return None
    parser = _parser(language)
    if parser is None:
        return None

    src = text.encode("utf-8")
    try:
        tree = parser.parse(src)
    except (ValueError, RecursionError) as exc:  # pragma: no cover - defensive
        log.warning("rag.parse_failed", path=str(path), error=str(exc))
        return None

    out: list[Block] = []
    _emit(tree.root_node, src, "", out)
    if not out:
        return None
    # A file with no declarations — a barrel of re-exports, or a test file that is
    # nothing but calls — still returns blocks rather than None. Falling back to the line
    # splitter here would re-introduce the exact bug this module exists to fix, on
    # precisely the files that suffered from it worst: `describe(...)` and `it(...)` would
    # become anchors again. "This file declares nothing" is an answer, not a failure.
    return _coalesce(out)
