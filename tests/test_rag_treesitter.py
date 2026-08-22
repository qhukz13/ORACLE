"""Code chunking on a real syntax tree.

The regex this replaced failed in one specific, measured way: a call that takes a callback
is indistinguishable from a declaration to a line matcher. Across the corpus its most
common "symbol" was `equal` (548 occurrences) and its second was `useEffect` (219) — both
calls. So the tests here are mostly about *what must not become an anchor*, using the exact
shapes that broke it.

Hermetic by construction: the samples are inline rather than read from `C:/Projects`, so
the suite does not depend on this machine's checkouts (docs/TESTING.md). The corpus-wide
version of the same assertion was run by hand and is recorded in
`logs/development/2026-08-22-treesitter-chunking.md`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oracle.rag.chunking import chunk_code as _chunk_code
from oracle.rag.collections import ContentKind, Document
from oracle.rag.treesitter import LANGUAGES, blocks_for, language_for

TS = """\
import crypto from 'crypto';

export class TokenService {
  private jwtSecret: string = '';

  signAccessToken(payload: Payload): string {
    if (!payload) { throw new Error('no payload'); }
    for (const k of Object.keys(payload)) { check(k); }
    return sign(payload);
  }

  async refresh(token: string): Promise<string> {
    return this.signAccessToken(await verify(token));
  }
}

export const tokenService = new TokenService();
"""

TEST_FILE = """\
import { thing } from './thing';

describe('the thing', () => {
  beforeEach(() => { reset(); });
  it('works', () => {
    assert.equal(actual, expected);
    expect(thing).toBe(true);
  });
});
"""

REACT = """\
export function ChangesView({ repo }: Props) {
  useEffect(() => {
    load(repo);
  }, [repo]);
  return <div>{repo}</div>;
}
"""

PY = """\
import os


class Store:
    def __init__(self, path):
        self.path = path

    def put(self, key, value):
        if key is None:
            raise ValueError(key)
        for attempt in range(3):
            try:
                self._write(key, value)
            except OSError:
                continue
        return True


def helper(x):
    return x + 1
"""

RUST = """\
use std::fs;

pub struct Viewer {
    path: String,
}

impl Viewer {
    pub fn open(path: &str) -> Self {
        if path.is_empty() { panic!("empty"); }
        Viewer { path: path.to_string() }
    }
}

fn main() {
    let v = Viewer::open("demo");
}
"""

#: Anything here appearing as an anchor means a call or a keyword was mistaken for a
#: declaration — the exact regression this module exists to prevent.
FORBIDDEN = {
    "if", "for", "while", "switch", "catch", "return", "do", "else", "try", "except",
    "finally", "await", "yield", "throw", "new", "typeof", "in", "of", "match", "case",
    "describe", "it", "beforeEach", "afterEach", "expect", "assert", "useEffect",
    "useState", "setTimeout", "console",
}  # fmt: skip


def doc(name: str) -> Document:
    return Document(
        collection="projects",
        project="Asterim",
        path=f"src/{name}",
        abs_path=Path("C:/Projects/Asterim/src") / name,
        kind=ContentKind.CODE,
        size=0,
        mtime_ns=0,
    )


def chunk_code(doc: Document, text: str):
    """Always the syntax-aware path.

    `chunking.SYNTAX_AWARE` is False — the line matcher is what ships, on measured
    retrieval evidence (see the constant). These tests are about the syntax tree, so they
    ask for it explicitly rather than depending on a default that is deliberately off.
    """
    return _chunk_code(doc, text, syntax_aware=True)


def anchors(name: str, source: str) -> set[str]:
    """Every anchor component the parser produces for one file.

    Read from the blocks rather than from the packed chunks: `_pack` merges small blocks
    and a chunk then carries only the first block's anchor, so a small file collapses to
    one anchor even though every symbol is named inside it.
    """
    blocks = blocks_for(source, doc(name).abs_path)
    if blocks is None:  # no grammar — the line splitter owns this file
        return {part for c in chunk_code(doc(name), source) for part in c.anchor.split(".")}
    return {part for b in blocks for part in b.anchor.split(".")}


class TestNothingButDeclarations:
    """The regression suite. Each case is a shape that defeated the regex."""

    @pytest.mark.parametrize(
        ("name", "source"),
        [
            ("TokenService.ts", TS),
            ("thing.test.ts", TEST_FILE),
            ("ChangesView.tsx", REACT),
            ("store.py", PY),
            ("viewer.rs", RUST),
        ],
    )
    def test_no_keyword_or_call_becomes_an_anchor(self, name: str, source: str) -> None:
        assert not (anchors(name, source) & FORBIDDEN)

    def test_a_call_taking_a_callback_is_not_a_declaration(self) -> None:
        """`describe('x', () => {})` and `useEffect(() => {}, [])` both open a block and
        both are calls. This is the single distinction the regex could not draw."""
        assert "describe" not in anchors("thing.test.ts", TEST_FILE)
        assert "useEffect" not in anchors("ChangesView.tsx", REACT)

    def test_a_function_genuinely_named_equal_is_still_found(self) -> None:
        """The fix must not be "ban these names".

        Real code declares `function equal(...)` as a test helper — 14 times in this
        corpus — and those are correct anchors. Only the *calls* had to go.
        """
        source = "function equal(label: string, a: unknown, b: unknown): void {\n  log(label);\n}\n"
        assert "equal" in anchors("helpers.ts", source)


class TestNamesAndAncestry:
    def test_methods_carry_their_class(self) -> None:
        """RAG.md §3 asks for the enclosing signature path. The regex never had one."""
        blocks = blocks_for(TS, doc("TokenService.ts").abs_path)
        assert blocks is not None
        found = {b.anchor for b in blocks}
        assert "TokenService.signAccessToken" in found
        assert "TokenService.refresh" in found

    def test_python_methods_too(self) -> None:
        blocks = blocks_for(PY, doc("store.py").abs_path)
        assert blocks is not None
        assert "Store.put" in {b.anchor for b in blocks}

    def test_the_ancestry_reaches_the_chunk_text(self) -> None:
        """Packing collapses a small file into one chunk, so the anchor of that chunk is
        only the first block's. Every symbol still has to be *findable* — the per-block
        anchors are written into the text, which is what gets embedded."""
        text = "\n".join(c.text for c in chunk_code(doc("TokenService.ts"), TS))
        assert "TokenService.signAccessToken" in text
        assert "TokenService.refresh" in text

    def test_a_top_level_function_is_named(self) -> None:
        assert "helper" in anchors("store.py", PY)

    def test_rust_impl_blocks_resolve_to_methods(self) -> None:
        assert "open" in anchors("viewer.rs", RUST)
        assert "main" in anchors("viewer.rs", RUST)


class TestCoverage:
    def test_imports_and_top_level_code_are_still_indexed(self) -> None:
        """A chunker that only emits declarations silently stops indexing the rest of the
        file. The gaps between declarations are their own blocks."""
        text = "\n".join(c.text for c in chunk_code(doc("TokenService.ts"), TS))
        assert "import crypto" in text
        assert "tokenService" in text

    def test_every_line_of_the_body_survives(self) -> None:
        joined = "\n".join(c.text for c in chunk_code(doc("store.py"), PY))
        for fragment in ("self.path = path", "raise ValueError(key)", "return x + 1"):
            assert fragment in joined


class TestFallback:
    def test_an_unknown_language_returns_none(self) -> None:
        """PowerShell has no grammar in the pack, so it keeps the line splitter."""
        assert language_for(Path("deploy.ps1")) is None
        assert blocks_for("function Get-Thing { }", Path("deploy.ps1")) is None

    def test_a_file_of_only_calls_does_not_fall_back_to_the_regex(self) -> None:
        """The regression this nearly shipped with.

        A test file that is nothing but `describe(...)` and `it(...)` declares nothing.
        Returning None here would hand it to the line splitter, which anchors on exactly
        those calls — re-introducing the bug on the files that had it worst. "This file
        declares nothing" is an answer, not a failure.
        """
        blocks = blocks_for(TEST_FILE, Path("thing.test.ts"))
        assert blocks is not None
        assert {b.anchor for b in blocks} == {"(file)"}

    def test_a_barrel_file_is_anchored_on_the_file(self) -> None:
        blocks = blocks_for("export * from './a';\nexport * from './b';\n", Path("index.ts"))
        assert blocks is not None
        assert all(b.anchor == "(file)" for b in blocks)

    def test_broken_syntax_does_not_raise(self) -> None:
        """tree-sitter is error-tolerant on purpose. A half-written file being saved must
        never take the index down — the watcher will re-read it in two seconds anyway."""
        broken = "export class Thing {\n  method(: {{{ unclosed\n"
        chunk_code(doc("broken.ts"), broken)  # must not raise

    def test_an_empty_file_yields_nothing(self) -> None:
        assert chunk_code(doc("empty.ts"), "") == []

    def test_every_mapped_language_has_a_working_grammar(self) -> None:
        """The wheel is verified at import time rather than trusted.

        This is the OQ-09 lesson applied to a new dependency: check the thing loads on
        this machine before writing code that assumes it does.
        """
        sample = {"python": "def f():\n    return 1\n", "rust": "fn f() { }\n"}
        for suffix, language in LANGUAGES.items():
            source = sample.get(language, "function f() { }\n")
            # Not asserting it finds declarations in a sample written for another
            # language — only that asking for the grammar does not blow up.
            blocks_for(source, Path(f"probe{suffix}"))


TRIVIA = """\
/** AES-256-GCM's nonce. 12 bytes is the size the mode is defined for. */
const IV_BYTES = 12;

export type SecretKey = string;

export class Vault {
  /** Where the key comes from: PBKDF2 over the machine salt. */
  private key(): Buffer {
    return derive(this.salt);
  }
}
"""


class TestLeadingTrivia:
    """A declaration begins at its doc comment, not at the node the grammar reports.

    Found by comparing chunk anchors against the regex chunker on the same corpus: the
    grammar reports `type SecretKey` starting *after* `export`, and a doc comment is a
    sibling of the thing it documents rather than part of it. Emitting the gap before the
    declaration therefore severed every `/** ... */` from what it explains and left the
    bare word `export` as a block — and cost one fixture case, the only measured
    difference between the two chunkers. Recorded in
    `logs/development/2026-08-22-treesitter-chunking.md`.
    """

    def blocks(self) -> list:
        found = blocks_for(TRIVIA, Path("vault.ts"))
        assert found is not None
        return found

    def test_a_doc_comment_stays_with_what_it_documents(self) -> None:
        by_anchor = {b.anchor: b.text for b in self.blocks()}
        assert "AES-256-GCM" in by_anchor["IV_BYTES"]
        assert "PBKDF2 over the machine salt" in by_anchor["Vault.key"]

    def test_export_is_not_stranded_as_its_own_block(self) -> None:
        """`export` alone is not valid source and reads as nothing. Splitting a line in
        half is the one thing the lead scan must never do."""
        blocks = self.blocks()
        assert not any(b.text.strip() == "export" for b in blocks)
        assert any("export type SecretKey" in b.text for b in blocks)

    def test_the_file_is_still_covered_exactly_once(self) -> None:
        """Moving a boundary must not duplicate or drop text — the property the whole
        module rests on.

        Compared against the source minus its trailing newline: the root node ends at the
        last token, so whitespace after it is outside the tree. That is the only text the
        walk does not reproduce, and `_pack` strips it anyway.
        """
        assert "".join(b.text for b in self.blocks()) == TRIVIA.rstrip("\n")


FIELDS = """\
export class RelayClient {
  private clientKeys = new Map<string, CryptoKey>();
  private authenticatedClients = new Set<string>();
  private socket: Socket | null = null;

  connect(url: string): void {
    this.socket = io(url);
  }
}
"""


class TestPunctuationIsNotABlock:
    """`public_field_definition` stops before its `;`, so each field leaves one behind.

    Left alone, every one of those became a block, and a block gets its anchor written
    above it — so a chunk of class fields read `RelayClient` / `;` / `RelayClient` / `;`
    and the real code was crowded out. It moved one fixture from dense rank 2 to rank 18,
    which is what makes this a correctness test rather than a tidiness one.
    """

    def blocks(self) -> list:
        found = blocks_for(FIELDS, Path("RelayClient.ts"))
        assert found is not None
        return found

    def test_no_block_is_only_punctuation(self) -> None:
        assert all(any(ch.isalnum() for ch in b.text) for b in self.blocks())

    def test_the_semicolons_are_still_there(self) -> None:
        """Folded into the statement in front of them, not deleted."""
        assert "".join(b.text for b in self.blocks()) == FIELDS.rstrip("\n")
        by_anchor = {b.anchor: b.text for b in self.blocks()}
        assert by_anchor["RelayClient.clientKeys"].rstrip().endswith(";")

    def test_the_chunk_text_is_not_padded_with_anchors(self) -> None:
        text = "\n".join(c.text for c in chunk_code(doc("RelayClient.ts"), FIELDS))
        assert "RelayClient\n;" not in text
