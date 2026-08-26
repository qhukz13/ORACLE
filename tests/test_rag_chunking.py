"""Chunking: boundaries, ancestry, and the things that silently ruin retrieval.

Chunk boundaries decide retrieval quality more than the embedding model does
(docs/RAG.md#3), and nothing about a bad boundary raises an error — it just returns a
worse answer. So the properties that matter are asserted here rather than eyeballed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oracle.rag.chunking import (
    MAX_CHARS,
    MIN_CHARS,
    chunk_code,
    chunk_document,
    chunk_markdown,
)
from oracle.rag.collections import ContentKind, Document


def doc(path: str, kind: ContentKind = ContentKind.MARKDOWN) -> Document:
    return Document(
        collection="notes",
        project="AI-ML-Vault",
        path=path,
        abs_path=Path("C:/nowhere") / path,
        kind=kind,
        size=0,
        mtime_ns=0,
    )


NOTE = """---
type: concept
domain: nlp
tags: [transformers, attention]
---

# Fine-Tuning

## What is it?
Taking a pretrained model and continuing to train it on a smaller dataset, so its
behaviour specialises to one task. It is the practical face of [[Transfer Learning]].

## Intuition
Pretraining teaches language in general; fine-tuning teaches it your job. Because it
starts from a strong initialisation it converges fast and needs far fewer examples
than training from scratch would, which is the whole reason anyone does it. #nlp
"""


class TestMarkdown:
    def test_heading_path_is_in_the_chunk_text(self) -> None:
        """The single highest-value property in this module.

        A body reading "it converges fast" names nothing. Retrieval has to see
        `Fine-Tuning > Intuition` attached to it, or the question that asks about
        fine-tuning cannot match the paragraph that answers it.
        """
        chunks = chunk_markdown(doc("Fine-Tuning.md"), NOTE, obsidian=True)
        assert chunks
        joined = "\n".join(c.text for c in chunks)
        assert "Fine-Tuning > Intuition" in joined
        assert "AI-ML-Vault / Fine-Tuning.md" in joined

    def test_front_matter_becomes_tags(self) -> None:
        chunks = chunk_markdown(doc("Fine-Tuning.md"), NOTE, obsidian=True)
        tags = set(chunks[0].tags)
        assert {"transformers", "attention"} <= tags
        assert "type:concept" in tags and "domain:nlp" in tags

    def test_front_matter_is_not_indexed_as_body_text(self) -> None:
        """`---` delimiters and raw YAML in the body would be embedded as prose."""
        chunks = chunk_markdown(doc("Fine-Tuning.md"), NOTE, obsidian=True)
        assert not any(c.text.lstrip().startswith("---") for c in chunks)

    def test_wikilinks_are_extracted(self) -> None:
        chunks = chunk_markdown(doc("Fine-Tuning.md"), NOTE, obsidian=True)
        assert "Transfer Learning" in {link for c in chunks for link in c.links}

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("[[Embeddings]]", "Embeddings"),
            ("[[Embeddings|vectors]]", "Embeddings"),
            ("[[Embeddings#Intuition]]", "Embeddings"),
        ],
    )
    def test_wikilink_forms(self, raw: str, expected: str) -> None:
        """Aliases and heading anchors both resolve to the note, not to the display text
        — a link table keyed on "vectors" would expand to nothing."""
        text = "# T\n\nA sentence long enough to survive the minimum chunk size, "
        text += f"mentioning {raw} in passing, and then continuing for a while yet."
        chunks = chunk_markdown(doc("T.md"), text, obsidian=True)
        assert expected in {link for c in chunks for link in c.links}

    def test_hashtags_become_tags(self) -> None:
        chunks = chunk_markdown(doc("Fine-Tuning.md"), NOTE, obsidian=True)
        assert "nlp" in set(chunks[0].tags)

    def test_malformed_front_matter_does_not_raise(self) -> None:
        text = "---\n: : not yaml : :\n---\n\n# T\n\n" + "x" * 200
        assert chunk_markdown(doc("T.md"), text, obsidian=True)


class TestCode:
    def test_symbol_name_is_the_anchor_and_is_in_the_text(self) -> None:
        src = "\n".join(
            [
                "import crypto from 'crypto';",
                "",
                "export class TokenService {",
                "  signAccessToken(payload: Payload): string {",
                "    " + "// signs an access token, valid for fifteen minutes\n    " * 12,
                "  }",
                "}",
            ]
        )
        chunks = chunk_code(doc("apps/server/TokenService.ts", ContentKind.CODE), src)
        assert chunks
        assert any("TokenService" in c.text for c in chunks)
        assert any(c.anchor in {"TokenService", "signAccessToken"} for c in chunks)

    def test_control_flow_is_not_a_symbol(self) -> None:
        """`if (ok) {` at two spaces of indentation looks exactly like a method to a
        line matcher. Treating it as one both mis-names the citation and cuts the
        function in half."""
        src = "function handle() {\n" + "\n".join(
            f"  if (cond{i}) {{ doSomething({i}); }}" for i in range(40)
        )
        anchors = {c.anchor for c in chunk_code(doc("h.ts", ContentKind.CODE), src)}
        assert "if" not in anchors

    def test_a_call_statement_is_not_a_declaration(self) -> None:
        """Measured, not imagined: before this rule, `equal` was the most common symbol
        in the whole corpus at 863 occurrences, because `  equal(a, b);` inside test
        files was opening a new chunk every time."""
        src = "describe('thing', () => {\n" + "\n".join(
            f"  equal(actual{i}, expected{i});" for i in range(40)
        )
        anchors = {c.anchor for c in chunk_code(doc("t.ts", ContentKind.CODE), src)}
        assert "equal" not in anchors


class TestBounds:
    """`MAX_CHARS` is a bound on the **rendered** chunk, not on the body inside it.

    These assertions were `<= MAX_CHARS * 2` until 2026-08-26, and the slack was hiding a
    real bug: `_pack` counted block bodies while emitting `header + anchor + body` per
    block, and `_window` counted lines while emitting `prefix + lines`. On the real corpus
    that produced 4,055-character chunks from an 1,800-character cap, and 27% of embedded
    chunks past the model's token window. A budget with a factor-of-two tolerance is not a
    budget."""

    def test_no_chunk_exceeds_the_budget(self) -> None:
        text = "# T\n\n" + ("a paragraph of prose that goes on and on. " * 400)
        for chunk in chunk_markdown(doc("T.md"), text):
            assert len(chunk.text) <= MAX_CHARS

    def test_the_header_and_the_anchors_are_inside_the_budget_not_beside_it(self) -> None:
        """The regression test for the bug above. A deep path and long headings make the
        non-body part of a chunk large; if the cap is applied to the body alone, this is
        where it shows."""
        deep = doc(
            "a-very-long-vault-directory-name/another-nested-section-directory/"
            "and-one-more-level-for-good-measure/A Note With A Long Title.md"
        )
        heading = "## " + ("A Heading With Quite A Lot Of Words In It " * 3) + "\n\n"
        body = "sentences that carry the section. " * 30 + "\n\n"
        chunks = chunk_markdown(deep, "# Top\n\n" + (heading + body) * 12)
        assert chunks
        assert max(len(c.text) for c in chunks) <= MAX_CHARS
        # And the prefix really is in there — otherwise this measures nothing.
        assert any("A Note With A Long Title.md" in c.text for c in chunks)

    def test_a_single_enormous_line_is_still_split(self) -> None:
        """A 176 KB single-line JSON blob got through the first benchmark run and became
        one 176 KB chunk. A line-oriented splitter emits whatever it cannot split."""
        blob = doc("data.json", ContentKind.CONFIG)
        chunks = chunk_document(blob, '{"k":"' + "x" * 200_000 + '"}')
        assert chunks
        assert max(len(c.text) for c in chunks) <= MAX_CHARS

    def test_every_kind_of_document_respects_the_budget(self) -> None:
        """One assertion over the dispatch, so a new `ContentKind` cannot arrive with its
        own idea of how big a chunk may be."""
        code = "\n".join(f"export function fn{i}() {{\n  return {i};\n}}" for i in range(120))
        config = "{\n" + "\n".join(f'  "k{i}": {i},' for i in range(600)) + "\n}"
        cases = [
            (doc("n.md"), "# H\n\n" + "prose. " * 900),
            (doc("c.ts", ContentKind.CODE), code),
            (doc("t.txt", ContentKind.TEXT), "a line of running text. " * 600),
            (doc("c.json", ContentKind.CONFIG), config),
        ]
        for document, text in cases:
            chunks = chunk_document(document, text)
            assert chunks, document.path
            assert max(len(c.text) for c in chunks) <= MAX_CHARS, document.path

    def test_tiny_blocks_are_packed_not_emitted_individually(self) -> None:
        """Thirty chunks of forty tokens each is thirty rows that match nothing."""
        src = "\n".join(f"export const KEY_{i} = {i};" for i in range(60))
        chunks = chunk_code(doc("keys.ts", ContentKind.CODE), src)
        assert len(chunks) <= 4
        assert all(len(c.text) >= MIN_CHARS for c in chunks)

    def test_ordinals_are_dense_and_ordered(self) -> None:
        """Chunk identity is derived from the ordinal (RAG.md §6); a gap or a repeat
        would collide two chunks of the same file."""
        text = "# T\n\n" + ("prose. " * 2000)
        ordinals = [c.ordinal for c in chunk_markdown(doc("T.md"), text)]
        assert ordinals == list(range(len(ordinals)))


class TestDispatch:
    def test_config_is_chunked_but_never_semantic(self) -> None:
        """Config is searchable by exact string and must not reach the vector index."""
        chunks = chunk_document(doc("tsconfig.json", ContentKind.CONFIG), '{"a": 1}\n' * 40)
        assert chunks
        assert not any(c.semantic for c in chunks)

    def test_code_and_markdown_are_semantic(self) -> None:
        assert all(c.semantic for c in chunk_markdown(doc("T.md"), NOTE))
        src = "export function f() {\n" + "  const x = 1;\n" * 40 + "}\n"
        assert all(c.semantic for c in chunk_code(doc("f.ts", ContentKind.CODE), src))

    def test_empty_input_yields_nothing_rather_than_raising(self) -> None:
        assert chunk_document(doc("empty.md"), "") == []
        assert chunk_document(doc("empty.ts", ContentKind.CODE), "") == []
