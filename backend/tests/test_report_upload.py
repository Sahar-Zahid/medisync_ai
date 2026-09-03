"""
Tests for the patient report-upload endpoint: POST /patient/reports.

Mocked DB/storage (unittest.mock), no live PostgreSQL and no real disk
writes — same approach as test_patient_profile.py / test_patient_doctors.py.
Authentication follows the same pattern used throughout: a real JWT is
created with create_access_token, and app.core.deps.get_user_by_id is
patched so the token resolves to an in-memory User without touching a
real database. save_report_file / delete_report_file / create_report /
get_report_by_patient_and_hash are patched at the point they're imported
into app.routers.reports, so no test in this file touches the real
filesystem or a real database.

Run with:
    pytest backend/tests/test_report_upload.py -v
"""
import hashlib
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.storage import StorageError
from app.main import app
from app.models.report import Report, ReportStatus
from app.models.user import User, UserRole
from app.services.report_service import DuplicateReportError, ReportCreationError

VALID_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n"
# Same bytes, different trailing whitespace -> genuinely different content,
# used for the "different filename, identical bytes" vs "same filename,
# different bytes" distinction below.
OTHER_VALID_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Different true >>\nendobj\ntrailer\n<< >>\n%%EOF\n"
NOT_A_PDF_BYTES = b"just some plain text, not a pdf at all"

VALID_PDF_SHA256 = hashlib.sha256(VALID_PDF_BYTES).hexdigest()


def make_user(role: UserRole = UserRole.PATIENT, full_name: str = "Ada Lovelace", email: str = "ada@example.com") -> User:
    user = User(
        full_name=full_name,
        email=email,
        hashed_password="irrelevant-for-these-tests",
        role=role,
    )
    user.id = uuid.uuid4()
    user.created_at = datetime.now(timezone.utc)
    user.updated_at = datetime.now(timezone.utc)
    return user


def make_report(patient_id: uuid.UUID, storage_path: str = "abc123.pdf", sha256_hash: str = VALID_PDF_SHA256) -> Report:
    report = Report(
        patient_id=patient_id,
        original_filename="labs.pdf",
        storage_path=storage_path,
        sha256_hash=sha256_hash,
    )
    report.id = uuid.uuid4()
    report.status = ReportStatus.UPLOADED
    report.created_at = datetime.now(timezone.utc)
    return report


def auth_headers(user: User) -> dict:
    from app.core.security import create_access_token

    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_overrides():
    """Ensure dependency_overrides never leaks between tests."""
    yield
    app.dependency_overrides.clear()


def upload(client, headers=None, filename="labs.pdf", content=VALID_PDF_BYTES, content_type="application/pdf"):
    files = {"file": (filename, content, content_type)}
    if headers:
        return client.post("/patient/reports", files=files, headers=headers)
    return client.post("/patient/reports", files=files)


def _patch_no_existing_duplicate():
    """Shorthand for the common case: the up-front duplicate lookup finds
    nothing, so the upload proceeds as a normal (non-duplicate) upload."""
    return patch("app.routers.reports.get_report_by_patient_and_hash", return_value=None)


# ---------------------------------------------------------------------------
# Happy path / ownership
# ---------------------------------------------------------------------------

def test_authenticated_patient_can_upload_valid_pdf():
    user = make_user(role=UserRole.PATIENT)
    report = make_report(user.id)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         _patch_no_existing_duplicate(), \
         patch("app.routers.reports.save_report_file", return_value="abc123.pdf") as mock_save, \
         patch("app.routers.reports.create_report", return_value=report) as mock_create:
        response = upload(client, headers=auth_headers(user))

    assert response.status_code == 201
    body = response.json()
    assert body["original_filename"] == "labs.pdf"
    assert body["status"] == "uploaded"
    mock_save.assert_called_once()
    mock_create.assert_called_once()


def test_ownership_comes_from_authenticated_user_not_request_body():
    """There is no patient_id field in the multipart request at all, so
    the only way ownership could leak in would be create_report being
    called with something other than the authenticated session's own
    user id — assert it always is."""
    user = make_user(role=UserRole.PATIENT)
    report = make_report(user.id)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         _patch_no_existing_duplicate(), \
         patch("app.routers.reports.save_report_file", return_value="abc123.pdf"), \
         patch("app.routers.reports.create_report", return_value=report) as mock_create:
        upload(client, headers=auth_headers(user))

    _, kwargs = mock_create.call_args
    assert kwargs["patient_id"] == user.id


def test_response_does_not_expose_internal_filesystem_path():
    user = make_user(role=UserRole.PATIENT)
    report = make_report(user.id, storage_path="/very/secret/internal/path/abc123.pdf")

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         _patch_no_existing_duplicate(), \
         patch("app.routers.reports.save_report_file", return_value="abc123.pdf"), \
         patch("app.routers.reports.create_report", return_value=report):
        response = upload(client, headers=auth_headers(user))

    assert response.status_code == 201
    body_text = response.text
    assert "storage_path" not in body_text
    assert "/very/secret/internal/path" not in body_text
    assert "sha256" not in body_text.lower()


def test_malicious_filename_cannot_escape_private_storage():
    """The client filename must never be used to build a filesystem path.
    save_report_file() is only ever passed raw bytes — never a filename —
    so a path-traversal-style filename has nothing to act on."""
    user = make_user(role=UserRole.PATIENT)
    report = make_report(user.id)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         _patch_no_existing_duplicate(), \
         patch("app.routers.reports.save_report_file", return_value="abc123.pdf") as mock_save, \
         patch("app.routers.reports.create_report", return_value=report):
        response = upload(
            client,
            headers=auth_headers(user),
            filename="../../../../etc/passwd.pdf",
        )

    assert response.status_code == 201
    # save_report_file only ever receives the file content, never a path
    # or filename derived from client input.
    mock_save.assert_called_once_with(VALID_PDF_BYTES)
    # Whatever gets stored as the display filename is defanged of path
    # components.
    assert "/" not in response.json()["original_filename"]


# ---------------------------------------------------------------------------
# AuthN / AuthZ
# ---------------------------------------------------------------------------

def test_unauthenticated_upload_rejected():
    client = TestClient(app)
    with patch("app.routers.reports.save_report_file") as mock_save:
        response = upload(client)

    assert response.status_code == 401
    mock_save.assert_not_called()


def test_doctor_upload_rejected():
    doctor = make_user(role=UserRole.DOCTOR)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=doctor), \
         patch("app.routers.reports.save_report_file") as mock_save:
        response = upload(client, headers=auth_headers(doctor))

    assert response.status_code == 403
    mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def test_non_pdf_file_rejected():
    user = make_user(role=UserRole.PATIENT)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         patch("app.routers.reports.save_report_file") as mock_save:
        response = upload(
            client,
            headers=auth_headers(user),
            filename="labs.pdf",
            content=NOT_A_PDF_BYTES,
            content_type="application/pdf",  # spoofed content-type on purpose
        )

    assert response.status_code == 400
    mock_save.assert_not_called()


def test_oversized_file_rejected(monkeypatch):
    from app.routers import reports as reports_module

    monkeypatch.setattr(reports_module, "_MAX_UPLOAD_BYTES", 10)

    user = make_user(role=UserRole.PATIENT)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         patch("app.routers.reports.save_report_file") as mock_save:
        response = upload(client, headers=auth_headers(user))

    assert response.status_code == 413
    mock_save.assert_not_called()


# ---------------------------------------------------------------------------
# Failure safety
# ---------------------------------------------------------------------------

def test_storage_failure_does_not_create_report_record():
    user = make_user(role=UserRole.PATIENT)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         _patch_no_existing_duplicate(), \
         patch("app.routers.reports.save_report_file", side_effect=StorageError()), \
         patch("app.routers.reports.create_report") as mock_create:
        response = upload(client, headers=auth_headers(user))

    assert response.status_code == 500
    mock_create.assert_not_called()


def test_database_failure_after_storage_cleans_up_stored_file():
    user = make_user(role=UserRole.PATIENT)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         _patch_no_existing_duplicate(), \
         patch("app.routers.reports.save_report_file", return_value="abc123.pdf"), \
         patch("app.routers.reports.create_report", side_effect=ReportCreationError()), \
         patch("app.routers.reports.delete_report_file") as mock_delete:
        response = upload(client, headers=auth_headers(user))

    assert response.status_code == 500
    mock_delete.assert_called_once_with("abc123.pdf")
    body = response.json()
    assert "traceback" not in str(body).lower()
    assert "sqlalchemy" not in str(body).lower()


# ---------------------------------------------------------------------------
# SHA-256 duplicate detection
# ---------------------------------------------------------------------------

def test_sha256_is_computed_from_actual_file_bytes():
    """The hash passed into create_report (and used for the duplicate
    lookup) must be the real SHA-256 of the uploaded bytes — not derived
    from filename/size/timestamp."""
    user = make_user(role=UserRole.PATIENT)
    report = make_report(user.id)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         patch("app.routers.reports.get_report_by_patient_and_hash", return_value=None) as mock_lookup, \
         patch("app.routers.reports.save_report_file", return_value="abc123.pdf"), \
         patch("app.routers.reports.create_report", return_value=report) as mock_create:
        upload(client, headers=auth_headers(user), filename="whatever-name.pdf")

    expected_hash = hashlib.sha256(VALID_PDF_BYTES).hexdigest()
    # Used for the up-front lookup...
    lookup_args, _ = mock_lookup.call_args
    assert lookup_args[2] == expected_hash
    # ...and passed through to create_report as the value to persist.
    _, create_kwargs = mock_create.call_args
    assert create_kwargs["sha256_hash"] == expected_hash


def test_first_upload_succeeds_and_stores_hash():
    user = make_user(role=UserRole.PATIENT)
    report = make_report(user.id)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         _patch_no_existing_duplicate(), \
         patch("app.routers.reports.save_report_file", return_value="abc123.pdf"), \
         patch("app.routers.reports.create_report", return_value=report) as mock_create:
        response = upload(client, headers=auth_headers(user))

    assert response.status_code == 201
    expected_hash = hashlib.sha256(VALID_PDF_BYTES).hexdigest()
    assert mock_create.call_args.kwargs["sha256_hash"] == expected_hash


def test_same_patient_identical_pdf_rejected_as_duplicate():
    user = make_user(role=UserRole.PATIENT)
    existing = make_report(user.id)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         patch("app.routers.reports.get_report_by_patient_and_hash", return_value=existing), \
         patch("app.routers.reports.save_report_file") as mock_save, \
         patch("app.routers.reports.create_report") as mock_create:
        response = upload(client, headers=auth_headers(user))

    assert response.status_code == 409
    body = response.json()
    assert "already been uploaded" in body["detail"]["message"].lower()
    # No new file stored and no second Report created for the duplicate.
    mock_save.assert_not_called()
    mock_create.assert_not_called()


def test_duplicate_response_identifies_existing_report_without_internal_paths():
    user = make_user(role=UserRole.PATIENT)
    existing = make_report(user.id, storage_path="/internal/secret/path.pdf")

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         patch("app.routers.reports.get_report_by_patient_and_hash", return_value=existing), \
         patch("app.routers.reports.save_report_file"):
        response = upload(client, headers=auth_headers(user))

    assert response.status_code == 409
    body_text = response.text
    assert body_text.count(str(existing.id)) >= 1
    assert "/internal/secret/path.pdf" not in body_text
    assert "storage_path" not in body_text
    assert existing.sha256_hash not in body_text


def test_different_patient_same_pdf_is_allowed():
    """The duplicate check is scoped to (patient_id, hash) — the lookup
    must be called with *this* patient's id, and a fresh patient with no
    existing match must be allowed to upload."""
    user = make_user(role=UserRole.PATIENT, email="second-patient@example.com")
    report = make_report(user.id)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         patch("app.routers.reports.get_report_by_patient_and_hash", return_value=None) as mock_lookup, \
         patch("app.routers.reports.save_report_file", return_value="def456.pdf"), \
         patch("app.routers.reports.create_report", return_value=report):
        response = upload(client, headers=auth_headers(user))

    assert response.status_code == 201
    args, _ = mock_lookup.call_args
    assert args[1] == user.id


def test_same_filename_different_content_is_allowed():
    """Same filename, different bytes -> different hash -> not a
    duplicate. Filename must never be used as the duplicate identity."""
    user = make_user(role=UserRole.PATIENT)
    report = make_report(user.id, sha256_hash=hashlib.sha256(OTHER_VALID_PDF_BYTES).hexdigest())

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         patch("app.routers.reports.get_report_by_patient_and_hash", return_value=None) as mock_lookup, \
         patch("app.routers.reports.save_report_file", return_value="ghi789.pdf"), \
         patch("app.routers.reports.create_report", return_value=report):
        response = upload(
            client,
            headers=auth_headers(user),
            filename="labs.pdf",  # same filename as other tests' upload
            content=OTHER_VALID_PDF_BYTES,
        )

    assert response.status_code == 201
    expected_hash = hashlib.sha256(OTHER_VALID_PDF_BYTES).hexdigest()
    assert mock_lookup.call_args[0][2] == expected_hash


def test_different_filename_identical_content_still_detected_as_duplicate():
    user = make_user(role=UserRole.PATIENT)
    existing = make_report(user.id)  # sha256 of VALID_PDF_BYTES

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         patch("app.routers.reports.get_report_by_patient_and_hash", return_value=existing), \
         patch("app.routers.reports.save_report_file") as mock_save:
        response = upload(
            client,
            headers=auth_headers(user),
            filename="a-totally-different-name.pdf",
            content=VALID_PDF_BYTES,
        )

    assert response.status_code == 409
    mock_save.assert_not_called()


def test_database_uniqueness_conflict_handled_as_safe_duplicate_response():
    """Simulates the race case: the up-front check found nothing, but the
    database's unique constraint rejects the insert because a concurrent
    upload of the same bytes won first. create_report() is expected to
    turn that into DuplicateReportError; the router must turn that into
    the same safe 409, not a generic 500."""
    user = make_user(role=UserRole.PATIENT)
    winner = make_report(user.id)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         _patch_no_existing_duplicate(), \
         patch("app.routers.reports.save_report_file", return_value="race123.pdf"), \
         patch("app.routers.reports.create_report", side_effect=DuplicateReportError(winner)):
        response = upload(client, headers=auth_headers(user))

    assert response.status_code == 409
    body = response.json()
    assert "already been uploaded" in body["detail"]["message"].lower()


def test_report_service_create_report_cleans_up_file_on_integrity_error():
    """Unit-level check (not through the HTTP layer) that create_report
    itself deletes the just-stored file and raises DuplicateReportError
    when the DB raises IntegrityError, and that it looks up the winning
    row via get_report_by_patient_and_hash rather than inventing one."""
    from sqlalchemy.exc import IntegrityError

    from app.services import report_service

    user_id = uuid.uuid4()
    winner = make_report(user_id, storage_path="winner.pdf")

    db = MagicMock()
    db.commit.side_effect = IntegrityError("stmt", "params", Exception("dup"))

    with patch("app.services.report_service.delete_report_file") as mock_delete, \
         patch.object(report_service, "get_report_by_patient_and_hash", return_value=winner):
        with pytest.raises(report_service.DuplicateReportError) as exc_info:
            report_service.create_report(
                db,
                patient_id=user_id,
                original_filename="labs.pdf",
                storage_path="loser.pdf",
                sha256_hash=VALID_PDF_SHA256,
            )

    assert exc_info.value.existing_report is winner
    mock_delete.assert_called_once_with("loser.pdf")
    db.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# Existing non-PDF/size/authentication/security behavior stays intact
# ---------------------------------------------------------------------------

def test_non_pdf_still_rejected_before_any_duplicate_check():
    """Guards against the duplicate-check work accidentally being moved
    ahead of PDF validation."""
    user = make_user(role=UserRole.PATIENT)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         patch("app.routers.reports.get_report_by_patient_and_hash") as mock_lookup:
        response = upload(client, headers=auth_headers(user), content=NOT_A_PDF_BYTES)

    assert response.status_code == 400
    mock_lookup.assert_not_called()
