"""
Machine-readable PDF text extraction.

This is the first report-processing step: given a report's private
storage_path, open the PDF from server-controlled storage and pull out
whatever machine-readable text already exists in it, page by page.

Deliberately does NOT:
* perform OCR
* call Gemini or any other AI model
* identify tests, values, units, or dates
* interpret or structure the text in any way

The output is raw extracted text only — nothing more.
"""
from pypdf import PdfReader

from app.core.storage import StorageError, resolve_report_path
from app.services.text_utils import has_usable_text


class PdfExtractionError(Exception):
    """Raised when the stored PDF could not be opened/parsed, or when it
    contains no machine-readable text at all (e.g. a scanned/image-only
    PDF). Both cases are treated identically by callers: an extraction
    failure, never a reason to fall back to OCR/AI. Never carries raw
    parser/filesystem internals — callers must not leak this to the
    client."""
    pass


def extract_text_from_report(storage_path: str) -> str:
    """
    Extract machine-readable text from the PDF at `storage_path`.

    `storage_path` must be a server-generated identifier resolved from
    an authenticated Report row — never a client-supplied path (see
    app.core.storage.resolve_report_path, which this uses and which
    refuses to resolve anything outside the private storage root).

    Reads pages in order and joins each page's extracted text with a
    blank line between pages, so the combined result is deterministic
    across repeated runs against the same file. The original PDF is
    opened read-only and is never modified.

    Raises PdfExtractionError if:
    * the file can't be opened or parsed as a PDF,
    * the PDF is encrypted, or
    * the PDF contains no machine-readable text at all (e.g. it's a
      scanned/image-only PDF) — this is reported the same way as a parse
      failure, never treated as a successful extraction of empty text.
    """
    try:
        path = resolve_report_path(storage_path)
    except StorageError:
        # Never leak raw storage/filesystem internals from here — an
        # unresolvable storage path is an extraction failure like any
        # other.
        raise PdfExtractionError("Could not locate report file for extraction.") from None

    try:
        reader = PdfReader(str(path))
        try:
            if reader.is_encrypted:
                raise PdfExtractionError("Encrypted PDF cannot be read.")
            page_texts = [page.extract_text() or "" for page in reader.pages]
        finally:
            # PdfReader keeps the underlying file handle open for lazy
            # page access; make sure it's released even if extraction
            # above raised.
            stream = getattr(reader, "stream", None)
            if stream is not None and not stream.closed:
                stream.close()
    except PdfExtractionError:
        raise
    except Exception:
        # pypdf can raise a variety of exception types for malformed or
        # unsupported PDFs. The caller must never see raw parser
        # internals or a traceback, so every parse failure is normalized
        # to a single, safe error.
        raise PdfExtractionError("Could not parse PDF for text extraction.") from None

    combined_text = "\n\n".join(text.strip() for text in page_texts).strip()

    if not has_usable_text(combined_text):
        raise PdfExtractionError("No machine-readable text found in PDF.")

    return combined_text
