"""
OCR fallback text extraction for image-only/scanned PDFs.

This is the second report-processing step, used only when
app.services.pdf_extraction_service.extract_text_from_report finds no
usable machine-readable text (or can't parse the PDF at all). It renders
each page of the server-stored PDF to a local image and runs local OCR
(Tesseract, via pytesseract) against it — no network access, no cloud OCR
service.

Deliberately does NOT:
* call Gemini or any other AI/cloud model
* correct medical spelling, infer missing characters, or infer values
* interpret units, test names, dates, or patient identity
* decide whether a result is abnormal
* structure or normalize the text in any way

The output is raw OCR text only, kept in a separate `ocr_text` field on
the report — it is never merged into or used to overwrite
`extracted_text`, which stays a faithful record of the PDF's own
machine-readable content.
"""
import pypdfium2 as pdfium
import pytesseract

from app.core.storage import StorageError, resolve_report_path
from app.services.text_utils import has_usable_text

# MVP resource limits — deliberately simple, not a generalized
# resource-management framework:
# * a hard cap on pages OCR'd per report, so one large scanned PDF can't
#   consume unbounded CPU/memory rendering high-resolution page images
# * a fixed, moderate render scale (roughly 144 DPI, since pdfium's base
#   page raster is 72 DPI) — enough for Tesseract to read typical printed
#   scanned lab-report text without ballooning memory per page
_MAX_OCR_PAGES = 30
_RENDER_SCALE = 2.0


class OcrExtractionError(Exception):
    """Raised when the stored PDF could not be OCR'd: the file couldn't
    be opened/rendered, a page failed to OCR, the page count exceeds the
    MVP per-report limit, or the combined OCR output contains no usable
    text. Never carries raw parser/OCR/filesystem internals — callers
    must not leak this to the client."""
    pass


def extract_text_via_ocr(storage_path: str) -> str:
    """
    OCR the PDF at `storage_path`, page by page, in order.

    `storage_path` must be a server-generated identifier resolved from an
    authenticated Report row — never a client-supplied path. Reuses
    app.core.storage.resolve_report_path exactly as the native extractor
    does, so OCR can only ever operate on the server-controlled stored
    PDF, never an arbitrary filesystem/image path.

    Each page is rendered to an in-memory image and OCR'd independently;
    results are joined in page order with a blank line between pages,
    the same deterministic joining rule the native extractor uses. The
    original PDF file on disk is opened read-only and is never modified.

    Raises OcrExtractionError if the file can't be located/opened/
    rendered, the OCR engine fails on a page, the PDF's page count
    exceeds the MVP limit, or the combined OCR output has no usable
    (non-whitespace) text — this is never treated as a successful
    extraction of empty text.
    """
    try:
        path = resolve_report_path(storage_path)
    except StorageError:
        # Never leak raw storage/filesystem internals from here — an
        # unresolvable storage path is an OCR failure like any other.
        raise OcrExtractionError("Could not locate report file for OCR.") from None

    try:
        pdf = pdfium.PdfDocument(str(path))
    except Exception:
        raise OcrExtractionError("Could not open PDF for OCR.") from None

    try:
        page_count = len(pdf)
        if page_count == 0:
            raise OcrExtractionError("PDF has no pages to OCR.")
        if page_count > _MAX_OCR_PAGES:
            raise OcrExtractionError("PDF exceeds the maximum page count for OCR.")

        page_texts: list[str] = []
        for index in range(page_count):
            try:
                page = pdf[index]
                try:
                    bitmap = page.render(scale=_RENDER_SCALE)
                    image = bitmap.to_pil()
                    try:
                        page_text = pytesseract.image_to_string(image) or ""
                    finally:
                        image.close()
                finally:
                    page.close()
            except Exception:
                # pypdfium2/pytesseract can raise a variety of exception
                # types (render failures, missing/broken Tesseract
                # binary, etc.). The caller must never see raw
                # parser/OCR internals, so every failure here is
                # normalized to a single, safe error.
                raise OcrExtractionError("OCR failed while processing a page.") from None
            page_texts.append(page_text.strip())
    finally:
        pdf.close()

    combined_text = "\n\n".join(page_texts).strip()

    if not has_usable_text(combined_text):
        raise OcrExtractionError("OCR produced no usable text.")

    return combined_text
