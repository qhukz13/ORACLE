"""PDF text extraction — the text layer, and nothing else (RAG.md §2).

**No OCR.** A scanned page yields nothing here and that is the intended answer: OCR is a
second model, a second failure mode and a second thing to keep current, for content that on
this corpus is one file. A PDF with no text layer is reported as empty rather than
half-guessed.

`pypdfium2` rather than `PyMuPDF`: same underlying quality, Apache/BSD instead of AGPL, and
the licensing entanglement is not worth it for a text layer (TECH_STACK §4 ledger).

Pages are joined with `\\f`, the ASCII page separator, so the page structure survives the
trip to the chunker without this module needing to know what a chunk is. `chunk_pdf` splits
on it again to anchor each chunk on the page it came from — which is the only citation a
PDF can offer that a reader can act on.
"""

from __future__ import annotations

from pathlib import Path

from oracle.logsink import get_logger

log = get_logger(__name__)

#: Page separator. Chosen because extracted text does not contain it: `pypdfium2` returns
#: the glyphs on the page, and a form feed is not a glyph.
PAGE_BREAK = "\f"

#: A cap on pages, not on bytes — `max_file_bytes` already bounds the file. A 510-page
#: textbook is the largest thing in this corpus and is well inside this; the limit exists so
#: that a pathological or generated PDF cannot silently become a third of the index.
MAX_PAGES = 2000


def extract(path: Path) -> str | None:
    """Every page's text, separated by `PAGE_BREAK`, or None if there is nothing to read.

    Never raises. A malformed PDF is a document ORACLE does not know about, not a failed
    index — the same rule the chunker follows for a half-written source file.
    """
    try:
        import pypdfium2 as pdfium
    except ImportError:  # pragma: no cover - the dependency is declared, this is belt
        log.warning("rag.pdf_unavailable", path=str(path))
        return None

    try:
        document = pdfium.PdfDocument(path)
    except Exception as exc:  # pdfium raises its own error types for encrypted/corrupt
        log.warning("rag.pdf_unreadable", path=str(path), error=str(exc))
        return None

    try:
        pages = len(document)
        if pages > MAX_PAGES:
            log.warning("rag.pdf_too_many_pages", path=str(path), pages=pages)
            pages = MAX_PAGES
        out: list[str] = []
        for number in range(pages):
            try:
                text = document[number].get_textpage().get_text_bounded() or ""
            except Exception as exc:  # pragma: no cover - one bad page, not a bad file
                log.debug("rag.pdf_page_failed", path=str(path), page=number, error=str(exc))
                text = ""
            out.append(text)
    finally:
        document.close()

    joined = PAGE_BREAK.join(out)
    if not joined.strip():
        # Almost certainly a scan. Saying so is more useful than indexing a file of
        # nothing and leaving the user to wonder why it never comes back in an answer.
        log.info("rag.pdf_no_text_layer", path=str(path), pages=len(out))
        return None
    return joined
