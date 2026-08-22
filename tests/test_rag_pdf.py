"""PDFs: the text layer, page anchors, and every way a PDF can fail to be one.

The failure cases matter more than the happy path here. A PDF is the only content type in
this corpus that can be *present, readable, and empty* — a scan is a stack of images with no
text in it — and the wrong response to that is to index nothing and say nothing, leaving the
user to wonder why a document they can see never appears in an answer.

Hermetic: the fixtures are assembled from raw PDF syntax rather than read from
`C:/Users/.../Documents`, so the suite does not depend on this machine's files
(docs/TESTING.md). The real 510-page textbook was measured by hand and is recorded in
`logs/development/2026-08-22-pdf.md`.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from oracle.rag.chunking import chunk_pdf
from oracle.rag.collections import ContentKind, Document, classify
from oracle.rag.indexer import provenance_of
from oracle.rag.pdf import PAGE_BREAK, extract


def build_pdf(pages: list[str]) -> bytes:
    """A minimal, valid PDF with a real text layer, assembled from raw syntax.

    Written by hand rather than by `pypdfium2` for two reasons. The library has no
    high-level text-authoring API in 5.x, so the alternative is a page of `raw` ctypes
    calls that would make this a test of pdfium's *writer*. And a fixture produced by the
    same library that reads it can hide a bug the two share — this one cannot.
    """
    count = len(pages)
    font_id = 3 + 2 * count
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(count))
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        f"<< /Type /Pages /Kids [{kids}] /Count {count} >>".encode(),
    ]
    for i, body in enumerate(pages):
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 400 600] "
                f"/Contents {4 + 2 * i} 0 R "
                f"/Resources << /Font << /F1 {font_id} 0 R >> >> >>"
            ).encode()
        )
        escaped = body.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
        stream = f"BT /F1 12 Tf 40 500 Td ({escaped}) Tj ET".encode() if body else b""
        objects.append(
            b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream"
        )
    objects.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for number, body_bytes in enumerate(objects, start=1):
        offsets.append(len(out))
        out += str(number).encode() + b" 0 obj\n" + body_bytes + b"\nendobj\n"
    start_xref = len(out)
    out += b"xref\n0 " + str(len(objects) + 1).encode() + b"\n0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        b"trailer\n<< /Size "
        + str(len(objects) + 1).encode()
        + b" /Root 1 0 R >>\nstartxref\n"
        + str(start_xref).encode()
        + b"\n%%EOF\n"
    )
    return bytes(out)


def make_pdf(path: Path, pages: list[str]) -> Path:
    path.write_bytes(build_pdf(pages))
    return path


def doc(path: Path) -> Document:
    return Document(
        collection="notes",
        project="MLAI NOTES",
        path=f"MLAI NOTES/{path.name}",
        abs_path=path,
        kind=ContentKind.PDF,
        size=path.stat().st_size if path.exists() else 0,
        mtime_ns=0,
    )


class TestExtraction:
    def test_pages_come_back_separated(self, tmp_path: Path) -> None:
        path = make_pdf(tmp_path / "notes.pdf", ["Gradient descent converges", "Second page"])
        text = extract(path)
        assert text is not None
        assert text.count(PAGE_BREAK) == 1
        assert "Gradient descent converges" in text

    def test_a_scan_reports_no_text_layer(self, tmp_path: Path) -> None:
        """Pages with no text at all. Returning "" here would index an empty document and
        hide the one fact the user needs: this file has nothing to search."""
        assert extract(make_pdf(tmp_path / "scan.pdf", ["", ""])) is None

    def test_a_corrupt_file_is_not_an_exception(self, tmp_path: Path) -> None:
        """A malformed PDF is a document ORACLE does not know about, not a failed index."""
        broken = tmp_path / "broken.pdf"
        broken.write_bytes(b"%PDF-1.7\nnot actually a pdf\n")
        assert extract(broken) is None

    def test_a_missing_file_is_not_an_exception(self, tmp_path: Path) -> None:
        assert extract(tmp_path / "gone.pdf") is None


class TestChunking:
    def test_chunks_are_anchored_on_the_page(self, tmp_path: Path) -> None:
        """The only citation a PDF can offer that a reader can act on: there is no heading
        path to recover and no symbol to name."""
        long_page = "Backpropagation applies the chain rule through the network. " * 30
        path = make_pdf(tmp_path / "book.pdf", [long_page, long_page])
        text = extract(path)
        assert text is not None
        chunks = chunk_pdf(doc(path), text)
        assert chunks
        assert all(c.anchor.startswith("p. ") for c in chunks)
        assert chunks[0].anchor == "p. 1"

    def test_pages_are_packed_rather_than_one_chunk_each(self, tmp_path: Path) -> None:
        """A page is a printing artefact, not a unit of meaning. One embedding per page
        would be hundreds of vectors of running text cut mid-sentence."""
        pages = [f"Page {n} of a short document." for n in range(8)]
        path = make_pdf(tmp_path / "short.pdf", pages)
        text = extract(path)
        assert text is not None
        assert len(chunk_pdf(doc(path), text)) < 8

    def test_a_blank_page_does_not_become_a_chunk(self, tmp_path: Path) -> None:
        path = make_pdf(tmp_path / "gaps.pdf", ["Real content here, quite a lot of it. " * 20, ""])
        text = extract(path)
        assert text is not None
        assert all(c.text.strip() for c in chunk_pdf(doc(path), text))


class TestProvenance:
    def test_a_pdf_is_foreign(self, tmp_path: Path) -> None:
        """Nobody writes a PDF in Obsidian — every one in this corpus was acquired.

        The rule is a generalisation, and it is the one that fails safe: being wrong
        escalates the policy tier of a plan built on the content and never relaxes it
        (SECURITY.md §6).
        """
        assert provenance_of(doc(tmp_path / "textbook.pdf")) == "local_foreign"

    def test_a_note_beside_it_is_not(self, tmp_path: Path) -> None:
        markdown = Document(
            collection="notes",
            project="MLAI NOTES",
            path="MLAI NOTES/my thoughts.md",
            abs_path=tmp_path / "my thoughts.md",
            kind=ContentKind.MARKDOWN,
            size=0,
            mtime_ns=0,
        )
        assert provenance_of(markdown) == "local_owned"


@pytest.mark.parametrize("suffix", [".pdf", ".PDF"])
def test_classify_recognises_a_pdf_in_either_case(suffix: str) -> None:
    assert classify(Path(f"book{suffix}")) is ContentKind.PDF
