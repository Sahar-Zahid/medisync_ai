"""
Tests for the OCR fallback feature:
* app.services.ocr_extraction_service.extract_text_via_ocr
* the native-extraction -> OCR-fallback decision logic in
  app.services.report_service.process_report_text_extraction
* app.services.text_utils.has_usable_text

Two kinds of tests here, clearly separated:

1. Mocked / dependency-free tests (no real PDF rendering, no real OCR
   engine) — exercise the *decision logic* only: does a report go down
   the native path or the OCR path, and does an OCR failure/success land
   on the right status. These always run.

2. Real-OCR tests, built with a genuine image-only PDF (text rendered to
   a raster image with Pillow, then wrapped in a PDF with img2pdf — no
   /Contents text stream at all, so pypdf finds nothing) and run through
   the *actual* pypdfium2 + Tesseract pipeline. These are skipped
   automatically if the `tesseract` binary isn't available in the
   environment, so the mocked suite above still fully exercises the
   decision logic even where real OCR can't run.

Run with:
    pytest backend/tests/test_report_ocr.py -v
"""
import shutil
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.core import storage as storage_module
from app.models.report import Report, ReportStatus
from app.services import report_service
from app.services.ocr_extraction_service import OcrExtractionError, extract_text_via_ocr
from app.services.pdf_extraction_service import PdfExtractionError
from app.services.text_utils import has_usable_text

_TESSERACT_AVAILABLE = shutil.which("tesseract") is not None

requires_tesseract = pytest.mark.skipif(
    not _TESSERACT_AVAILABLE,
    reason="tesseract binary not available in this environment",
)


def make_report(status: ReportStatus = ReportStatus.UPLOADED, storage_path: str = "abc123.pdf") -> Report:
    report = Report(
        patient_id=uuid.uuid4(),
        original_filename="labs.pdf",
        storage_path=storage_path,
        sha256_hash="0" * 64,
    )
    report.id = uuid.uuid4()
    report.status = status
    report.extracted_text = None
    report.ocr_text = None
    report.created_at = datetime.now(timezone.utc)
    return report


@pytest.fixture
def temp_storage(tmp_path, monkeypatch):
    original = storage_module.settings.report_storage_dir
    storage_module.settings.report_storage_dir = tmp_path
    yield tmp_path
    storage_module.settings.report_storage_dir = original


def _image_only_pdf_bytes(lines: list[list[str]]) -> bytes:
    """Build a real image-only PDF: each element of `lines` is the text
    for one page, rendered into a raster image with Pillow and wrapped in
    a PDF page via img2pdf. There is no PDF text/content stream at all —
    pypdf finds nothing — so this is a genuine scanned/image-only PDF,
    not a stand-in."""
    import io

    import img2pdf
    from PIL import Image, ImageDraw, ImageFont

    font = ImageFont.truetype(
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 40
    )

    page_images = []
    for page_lines in lines:
        image = Image.new("RGB", (900, 300), "white")
        draw = ImageDraw.Draw(image)
        y = 20
        for line in page_lines:
            draw.text((20, y), line, fill="black", font=font)
            y += 60
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        page_images.append(buf.getvalue())

    return img2pdf.convert(page_images)


# ---------------------------------------------------------------------------
# has_usable_text — small, dependency-free decision-logic unit
# ---------------------------------------------------------------------------

def test_has_usable_text_true_for_real_content():
    assert has_usable_text("Hello MediSync") is True


@pytest.mark.parametrize("value", [None, "", "   ", "\n\t  \n"])
def test_has_usable_text_false_for_empty_or_whitespace(value):
    assert has_usable_text(value) is False


# ---------------------------------------------------------------------------
# process_report_text_extraction — decision logic, fully mocked
# (no real PDF, no real OCR engine — runs unconditionally)
# ---------------------------------------------------------------------------

def test_usable_native_text_takes_native_path_and_never_invokes_ocr():
    report = make_report(ReportStatus.UPLOADED)
    db = MagicMock()

    with patch(
        "app.services.report_service.extract_text_from_report",
        return_value="native text",
    ) as mock_native, patch(
        "app.services.report_service.extract_text_via_ocr"
    ) as mock_ocr:
        result = report_service.process_report_text_extraction(db, report)

    mock_native.assert_called_once()
    mock_ocr.assert_not_called()
    assert result.status == ReportStatus.COMPLETED
    assert result.extracted_text == "native text"
    assert result.ocr_text is None


def test_empty_native_text_takes_ocr_path():
    report = make_report(ReportStatus.UPLOADED)
    db = MagicMock()

    with patch(
        "app.services.report_service.extract_text_from_report",
        side_effect=PdfExtractionError("no machine-readable text"),
    ), patch(
        "app.services.report_service.extract_text_via_ocr",
        return_value="ocr text",
    ) as mock_ocr:
        result = report_service.process_report_text_extraction(db, report)

    mock_ocr.assert_called_once()
    assert result.status == ReportStatus.COMPLETED
    assert result.ocr_text == "ocr text"
    # extracted_text (native field) must stay untouched/None — OCR text
    # is never written into it.
    assert result.extracted_text is None


def test_ocr_success_stores_ocr_text_and_completes():
    report = make_report(ReportStatus.UPLOADED)
    db = MagicMock()

    with patch(
        "app.services.report_service.extract_text_from_report",
        side_effect=PdfExtractionError("no text"),
    ), patch(
        "app.services.report_service.extract_text_via_ocr",
        return_value="raw ocr output",
    ):
        result = report_service.process_report_text_extraction(db, report)

    assert result.status == ReportStatus.COMPLETED
    assert result.ocr_text == "raw ocr output"


def test_ocr_failure_transitions_to_failed_not_completed():
    report = make_report(ReportStatus.UPLOADED)
    db = MagicMock()

    with patch(
        "app.services.report_service.extract_text_from_report",
        side_effect=PdfExtractionError("no text"),
    ), patch(
        "app.services.report_service.extract_text_via_ocr",
        side_effect=OcrExtractionError("ocr failed"),
    ):
        result = report_service.process_report_text_extraction(db, report)

    assert result.status == ReportStatus.FAILED
    assert result.ocr_text is None
    assert result.extracted_text is None


def test_unparseable_native_pdf_also_falls_back_to_ocr():
    """An unparseable PDF (not just a parseable-but-empty one) is also
    treated as "no usable native text" and gets an OCR attempt, same as
    a scanned/image-only PDF."""
    report = make_report(ReportStatus.UPLOADED)
    db = MagicMock()

    with patch(
        "app.services.report_service.extract_text_from_report",
        side_effect=PdfExtractionError("could not parse"),
    ), patch(
        "app.services.report_service.extract_text_via_ocr",
        return_value="ocr recovered text",
    ) as mock_ocr:
        result = report_service.process_report_text_extraction(db, report)

    mock_ocr.assert_called_once()
    assert result.status == ReportStatus.COMPLETED
    assert result.ocr_text == "ocr recovered text"


# ---------------------------------------------------------------------------
# extract_text_via_ocr — storage/path safety (no real OCR engine needed)
# ---------------------------------------------------------------------------

def test_ocr_cannot_access_arbitrary_filesystem_paths(temp_storage):
    """A storage_path that would escape the private storage root is
    rejected before any rendering/OCR is attempted."""
    with pytest.raises(OcrExtractionError):
        extract_text_via_ocr("../outside.pdf")


def test_ocr_raises_when_stored_file_missing(temp_storage):
    with pytest.raises(OcrExtractionError):
        extract_text_via_ocr("does-not-exist.pdf")


# ---------------------------------------------------------------------------
# extract_text_via_ocr — real image-only PDF + real Tesseract
# (skipped automatically if tesseract isn't installed)
# ---------------------------------------------------------------------------

@requires_tesseract
def test_image_only_pdf_is_ocrd_successfully(temp_storage):
    pdf_bytes = _image_only_pdf_bytes([["MEDISYNC LAB REPORT"]])
    (temp_storage / "scan.pdf").write_bytes(pdf_bytes)

    text = extract_text_via_ocr("scan.pdf")

    assert "MEDISYNC" in text.upper()


@requires_tesseract
def test_multi_page_ocr_preserves_page_order(temp_storage):
    pdf_bytes = _image_only_pdf_bytes([["PAGE ONE MARKER"], ["PAGE TWO MARKER"]])
    (temp_storage / "scan.pdf").write_bytes(pdf_bytes)

    text = extract_text_via_ocr("scan.pdf")
    upper = text.upper()

    assert "PAGE ONE MARKER" in upper
    assert "PAGE TWO MARKER" in upper
    assert upper.index("PAGE ONE MARKER") < upper.index("PAGE TWO MARKER")


@requires_tesseract
def test_original_pdf_unchanged_after_ocr(temp_storage):
    pdf_bytes = _image_only_pdf_bytes([["UNCHANGED CHECK"]])
    path = temp_storage / "scan.pdf"
    path.write_bytes(pdf_bytes)

    extract_text_via_ocr("scan.pdf")

    assert path.read_bytes() == pdf_bytes


@requires_tesseract
def test_blank_image_only_pdf_raises_ocr_error(temp_storage):
    """A page with no text content at all -> OCR produces no usable
    text -> OcrExtractionError, never a silent 'success' with empty
    text."""
    pdf_bytes = _image_only_pdf_bytes([[]])
    (temp_storage / "blank.pdf").write_bytes(pdf_bytes)

    with pytest.raises(OcrExtractionError):
        extract_text_via_ocr("blank.pdf")


@requires_tesseract
def test_end_to_end_scanned_pdf_completes_via_ocr(temp_storage):
    """Full process_report_text_extraction() run against a real
    image-only PDF: native extraction finds nothing, OCR fallback runs
    for real, report ends up COMPLETED with ocr_text populated and
    extracted_text left None."""
    pdf_bytes = _image_only_pdf_bytes([["END TO END SCAN TEST"]])
    (temp_storage / "scan.pdf").write_bytes(pdf_bytes)

    report = make_report(ReportStatus.UPLOADED, storage_path="scan.pdf")
    db = MagicMock()

    result = report_service.process_report_text_extraction(db, report)

    assert result.status == ReportStatus.COMPLETED
    assert result.extracted_text is None
    assert result.ocr_text is not None
    assert "END TO END SCAN TEST" in result.ocr_text.upper()
