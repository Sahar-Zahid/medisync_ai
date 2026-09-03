"""
Tests for machine-readable PDF text extraction:
* app.services.pdf_extraction_service.extract_text_from_report
* app.services.report_service.process_report_text_extraction
* POST /patient/reports/{report_id}/process

Mocked DB (unittest.mock) for the service/endpoint tests — same approach
as the other report tests, no live PostgreSQL involved. The extraction
service itself is tested against small, hand-built, deterministic PDF
fixtures written to a temporary storage directory (settings overridden
per-test), so no real patient data or large fixture files are needed.

Run with:
    pytest backend/tests/test_report_extraction.py -v
"""
import hashlib
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core import storage as storage_module
from app.main import app
from app.models.report import Report, ReportStatus
from app.models.user import User, UserRole
from app.services import report_service
from app.services.pdf_extraction_service import (
    PdfExtractionError,
    extract_text_from_report,
)

VALID_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n"


# ---------------------------------------------------------------------------
# Small deterministic PDF builder (no reportlab/fpdf dependency needed) —
# hand-writes minimal-but-valid single/multi-page PDFs with real
# extractable text content streams, or a page with no content stream at
# all for the "image-only, no machine-readable text" case.
# ---------------------------------------------------------------------------

def _content_stream(lines: list[str]) -> bytes:
    ops = "BT /F1 18 Tf 72 720 Td 18 TL\n"
    for line in lines:
        escaped = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        ops += f"({escaped}) Tj T*\n"
    ops += "ET"
    return ops.encode("latin-1")


def build_pdf(pages: list[list[str]]) -> bytes:
    """Build a minimal valid PDF where each element of `pages` is the list
    of text lines for that page. An empty inner list produces a page with
    no content stream at all (no machine-readable text)."""
    objects: list[bytes] = []
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(len(pages)))
    objects.append(b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n")
    objects.append(
        f"2 0 obj\n<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>\nendobj\n".encode(
            "latin-1"
        )
    )

    font_obj_num = 3 + 2 * len(pages)
    for i, lines in enumerate(pages):
        page_num = 3 + 2 * i
        content_num = page_num + 1
        if lines:
            objects.append(
                f"{page_num} 0 obj\n<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 612 792] /Resources << /Font << /F1 "
                f"{font_obj_num} 0 R >> >> /Contents {content_num} 0 R >>\n"
                f"endobj\n".encode("latin-1")
            )
            content_bytes = _content_stream(lines)
            objects.append(
                f"{content_num} 0 obj\n<< /Length {len(content_bytes)} >>\n"
                f"stream\n".encode("latin-1")
                + content_bytes
                + b"\nendstream\nendobj\n"
            )
        else:
            # Page with no /Contents at all -> no machine-readable text,
            # standing in for a scanned/image-only page.
            objects.append(
                f"{page_num} 0 obj\n<< /Type /Page /Parent 2 0 R "
                f"/MediaBox [0 0 612 792] >>\nendobj\n".encode("latin-1")
            )
            objects.append(f"{content_num} 0 obj\n<< /Length 0 >>\nstream\n\nendstream\nendobj\n".encode("latin-1"))

    objects.append(
        f"{font_obj_num} 0 obj\n<< /Type /Font /Subtype /Type1 "
        f"/BaseFont /Helvetica >>\nendobj\n".encode("latin-1")
    )

    pdf = b"%PDF-1.4\n"
    offsets = []
    for obj in objects:
        offsets.append(len(pdf))
        pdf += obj

    xref_offset = len(pdf)
    total = len(objects) + 1
    pdf += f"xref\n0 {total}\n".encode("latin-1")
    pdf += b"0000000000 65535 f \n"
    for off in offsets:
        pdf += f"{off:010d} 00000 n \n".encode("latin-1")
    pdf += f"trailer\n<< /Size {total} /Root 1 0 R >>\n".encode("latin-1")
    pdf += f"startxref\n{xref_offset}\n%%EOF".encode("latin-1")
    return pdf


@pytest.fixture
def temp_storage(tmp_path, monkeypatch):
    """Point the private report storage root at a temp directory for the
    duration of the test, restoring it afterward."""
    original = storage_module.settings.report_storage_dir
    storage_module.settings.report_storage_dir = tmp_path
    yield tmp_path
    storage_module.settings.report_storage_dir = original


def make_user(role: UserRole = UserRole.PATIENT) -> User:
    user = User(
        full_name="Ada Lovelace",
        email="ada@example.com",
        hashed_password="irrelevant-for-these-tests",
        role=role,
    )
    user.id = uuid.uuid4()
    user.created_at = datetime.now(timezone.utc)
    user.updated_at = datetime.now(timezone.utc)
    return user


def make_report(status: ReportStatus = ReportStatus.UPLOADED, storage_path: str = "abc123.pdf") -> Report:
    report = Report(
        patient_id=uuid.uuid4(),
        original_filename="labs.pdf",
        storage_path=storage_path,
        sha256_hash=hashlib.sha256(VALID_PDF_BYTES).hexdigest(),
    )
    report.id = uuid.uuid4()
    report.status = status
    report.extracted_text = None
    report.created_at = datetime.now(timezone.utc)
    return report


def auth_headers(user: User) -> dict:
    from app.core.security import create_access_token

    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# extract_text_from_report — real (small) PDF fixtures on a temp storage dir
# ---------------------------------------------------------------------------

def test_valid_pdf_extracts_expected_text(temp_storage):
    pdf_bytes = build_pdf([["Hello MediSync"]])
    (temp_storage / "report.pdf").write_bytes(pdf_bytes)

    text = extract_text_from_report("report.pdf")

    assert "Hello MediSync" in text


def test_extraction_reads_from_server_private_storage_only(temp_storage):
    """A storage_path that would escape the storage root is rejected."""
    pdf_bytes = build_pdf([["Hello MediSync"]])
    (temp_storage / "report.pdf").write_bytes(pdf_bytes)

    with pytest.raises(PdfExtractionError):
        extract_text_from_report("../outside.pdf")


def test_original_pdf_remains_unchanged(temp_storage):
    pdf_bytes = build_pdf([["Hello MediSync"]])
    path = temp_storage / "report.pdf"
    path.write_bytes(pdf_bytes)

    extract_text_from_report("report.pdf")

    assert path.read_bytes() == pdf_bytes


def test_multi_page_text_is_combined_deterministically(temp_storage):
    pdf_bytes = build_pdf([["Page one text"], ["Page two text"]])
    (temp_storage / "report.pdf").write_bytes(pdf_bytes)

    first_run = extract_text_from_report("report.pdf")
    second_run = extract_text_from_report("report.pdf")

    assert "Page one text" in first_run
    assert "Page two text" in first_run
    assert first_run.index("Page one text") < first_run.index("Page two text")
    # Deterministic: re-running against the same file produces the same
    # combined text.
    assert first_run == second_run


def test_empty_text_pdf_raises_extraction_error_not_ocr(temp_storage):
    """A page with no content stream (standing in for scanned/image-only)
    must raise PdfExtractionError, never invent text or silently
    succeed."""
    pdf_bytes = build_pdf([[]])
    (temp_storage / "report.pdf").write_bytes(pdf_bytes)

    with pytest.raises(PdfExtractionError):
        extract_text_from_report("report.pdf")


def test_unparseable_file_raises_extraction_error(temp_storage):
    (temp_storage / "report.pdf").write_bytes(b"not a real pdf at all")

    with pytest.raises(PdfExtractionError):
        extract_text_from_report("report.pdf")


# ---------------------------------------------------------------------------
# process_report_text_extraction — service-level status-transition tests
# ---------------------------------------------------------------------------

def test_successful_extraction_transitions_to_completed_and_stores_text():
    report = make_report(ReportStatus.UPLOADED)
    db = MagicMock()

    with patch(
        "app.services.report_service.extract_text_from_report",
        return_value="raw extracted text",
    ):
        result = report_service.process_report_text_extraction(db, report)

    assert result.status == ReportStatus.COMPLETED
    assert result.extracted_text == "raw extracted text"


def test_extraction_failure_transitions_to_failed():
    report = make_report(ReportStatus.UPLOADED)
    db = MagicMock()

    with patch(
        "app.services.report_service.extract_text_from_report",
        side_effect=PdfExtractionError("no text"),
    ):
        result = report_service.process_report_text_extraction(db, report)

    assert result.status == ReportStatus.FAILED
    assert result.extracted_text is None


def test_processing_already_completed_report_is_rejected():
    report = make_report(ReportStatus.COMPLETED)
    db = MagicMock()

    with pytest.raises(report_service.InvalidStatusTransitionError):
        report_service.process_report_text_extraction(db, report)


# ---------------------------------------------------------------------------
# POST /patient/reports/{id}/process — endpoint authorization + behavior
# ---------------------------------------------------------------------------

def test_process_endpoint_success():
    user = make_user(role=UserRole.PATIENT)
    report = make_report(ReportStatus.UPLOADED)
    report.patient_id = user.id
    completed = make_report(ReportStatus.COMPLETED)
    completed.id = report.id
    completed.extracted_text = "raw extracted text"

    client = TestClient(app)
    db_mock = MagicMock()
    db_mock.query.return_value.filter.return_value.first.return_value = report

    with patch("app.core.deps.get_user_by_id", return_value=user), \
         patch("app.routers.reports.get_db", return_value=iter([db_mock])), \
         patch(
             "app.routers.reports.process_report_text_extraction",
             return_value=completed,
         ) as mock_process:
        response = client.post(
            f"/patient/reports/{report.id}/process",
            headers=auth_headers(user),
        )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    mock_process.assert_called_once()


def test_client_cannot_submit_extracted_text_or_status():
    """The process endpoint takes no request body at all — anything the
    client sends is ignored, never trusted as extracted text or a target
    status."""
    user = make_user(role=UserRole.PATIENT)
    report = make_report(ReportStatus.UPLOADED)
    report.patient_id = user.id
    completed = make_report(ReportStatus.COMPLETED)
    completed.id = report.id

    client = TestClient(app)
    db_mock = MagicMock()
    db_mock.query.return_value.filter.return_value.first.return_value = report

    with patch("app.core.deps.get_user_by_id", return_value=user), \
         patch("app.routers.reports.get_db", return_value=iter([db_mock])), \
         patch(
             "app.routers.reports.process_report_text_extraction",
             return_value=completed,
         ) as mock_process:
        response = client.post(
            f"/patient/reports/{report.id}/process",
            headers=auth_headers(user),
            json={
                "extracted_text": "client-supplied text should be ignored",
                "status": "completed",
                "storage_path": "/etc/passwd",
            },
        )

    assert response.status_code == 200
    # The (db, report) call signature has no way to receive body fields —
    # confirm the mock was invoked with exactly the report from the DB
    # lookup, not anything derived from the request body.
    args, _ = mock_process.call_args
    assert args[1] is report


def test_client_cannot_submit_arbitrary_filesystem_path():
    """report_id is a UUID path parameter validated by FastAPI; a
    non-UUID / path-traversal-looking value is rejected before it ever
    reaches the database lookup or the extractor."""
    user = make_user(role=UserRole.PATIENT)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user):
        response = client.post(
            "/patient/reports/../../etc/passwd/process",
            headers=auth_headers(user),
        )

    assert response.status_code in (404, 422)


def test_unauthorized_processing_is_rejected_for_unauthenticated_request():
    client = TestClient(app)
    response = client.post(f"/patient/reports/{uuid.uuid4()}/process")
    assert response.status_code == 401


def test_cannot_process_another_patients_report():
    """A report owned by a different patient must look identical to a
    nonexistent report — the lookup filters on patient_id, not just
    report_id."""
    user = make_user(role=UserRole.PATIENT)

    client = TestClient(app)
    db_mock = MagicMock()
    db_mock.query.return_value.filter.return_value.first.return_value = None

    with patch("app.core.deps.get_user_by_id", return_value=user), \
         patch("app.routers.reports.get_db", return_value=iter([db_mock])):
        response = client.post(
            f"/patient/reports/{uuid.uuid4()}/process",
            headers=auth_headers(user),
        )

    assert response.status_code == 404


def test_doctor_cannot_trigger_processing():
    doctor = make_user(role=UserRole.DOCTOR)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=doctor):
        response = client.post(
            f"/patient/reports/{uuid.uuid4()}/process",
            headers=auth_headers(doctor),
        )

    assert response.status_code == 403


def test_already_processed_report_returns_conflict():
    user = make_user(role=UserRole.PATIENT)
    report = make_report(ReportStatus.COMPLETED)
    report.patient_id = user.id

    client = TestClient(app)
    db_mock = MagicMock()
    db_mock.query.return_value.filter.return_value.first.return_value = report

    with patch("app.core.deps.get_user_by_id", return_value=user), \
         patch("app.routers.reports.get_db", return_value=iter([db_mock])), \
         patch(
             "app.routers.reports.process_report_text_extraction",
             side_effect=report_service.InvalidStatusTransitionError(
                 ReportStatus.COMPLETED, ReportStatus.PROCESSING
             ),
         ):
        response = client.post(
            f"/patient/reports/{report.id}/process",
            headers=auth_headers(user),
        )

    assert response.status_code == 409


# ---------------------------------------------------------------------------
# Existing upload/hash/duplicate behavior remains intact
# ---------------------------------------------------------------------------

def test_upload_endpoint_still_present_and_unaffected():
    """Sanity check that adding the process route didn't disturb the
    existing upload route/behavior."""
    report_paths = {
        route.path: route.methods
        for route in app.router.routes
        if getattr(route, "path", "").startswith("/patient/reports")
    }
    assert "/patient/reports" in report_paths
    assert "POST" in report_paths["/patient/reports"]
    assert "/patient/reports/{report_id}/process" in report_paths
    assert report_paths["/patient/reports/{report_id}/process"] == {"POST"}
