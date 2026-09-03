"""
Tests for the Report status state machine (app.services.report_service).

There is no endpoint that lets a client mutate report status, so per the
task these are service-level tests of transition_report_status() and the
ALLOWED_STATUS_TRANSITIONS table, plus a couple of upload-endpoint tests
confirming upload never triggers processing and existing upload/hash/
duplicate behavior is unaffected.

Mocked DB (unittest.mock) — same approach as the other report tests, no
live PostgreSQL involved.

Run with:
    pytest backend/tests/test_report_status.py -v
"""
import hashlib
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import SQLAlchemyError

from app.main import app
from app.models.report import Report, ReportStatus
from app.models.user import User, UserRole
from app.services import report_service

VALID_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< >>\nendobj\ntrailer\n<< >>\n%%EOF\n"


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


def make_report(status: ReportStatus = ReportStatus.UPLOADED) -> Report:
    report = Report(
        patient_id=uuid.uuid4(),
        original_filename="labs.pdf",
        storage_path="abc123.pdf",
        sha256_hash=hashlib.sha256(VALID_PDF_BYTES).hexdigest(),
    )
    report.id = uuid.uuid4()
    report.status = status
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
# Valid transitions
# ---------------------------------------------------------------------------

def test_uploaded_to_processing_is_valid():
    report = make_report(ReportStatus.UPLOADED)
    db = MagicMock()

    result = report_service.transition_report_status(db, report, ReportStatus.PROCESSING)

    assert result.status == ReportStatus.PROCESSING
    db.commit.assert_called_once()


def test_processing_to_completed_is_valid():
    report = make_report(ReportStatus.PROCESSING)
    db = MagicMock()

    result = report_service.transition_report_status(db, report, ReportStatus.COMPLETED)

    assert result.status == ReportStatus.COMPLETED


def test_processing_to_failed_is_valid():
    report = make_report(ReportStatus.PROCESSING)
    db = MagicMock()

    result = report_service.transition_report_status(db, report, ReportStatus.FAILED)

    assert result.status == ReportStatus.FAILED


# ---------------------------------------------------------------------------
# Invalid transitions
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "current,requested",
    [
        (ReportStatus.UPLOADED, ReportStatus.COMPLETED),   # skips PROCESSING
        (ReportStatus.UPLOADED, ReportStatus.FAILED),       # skips PROCESSING
        (ReportStatus.COMPLETED, ReportStatus.PROCESSING),  # terminal state
        (ReportStatus.COMPLETED, ReportStatus.UPLOADED),    # terminal state, backwards
        (ReportStatus.FAILED, ReportStatus.PROCESSING),     # terminal state
        (ReportStatus.FAILED, ReportStatus.COMPLETED),      # terminal state
        (ReportStatus.PROCESSING, ReportStatus.UPLOADED),   # backwards
    ],
)
def test_invalid_transitions_are_rejected(current, requested):
    report = make_report(current)
    db = MagicMock()

    with pytest.raises(report_service.InvalidStatusTransitionError):
        report_service.transition_report_status(db, report, requested)

    # Status must be left exactly as it was, and nothing written to the DB.
    assert report.status == current
    db.commit.assert_not_called()


def test_invalid_transition_does_not_touch_the_database():
    """The rejection must happen before any db.commit()/db.add() —
    an invalid transition should never even attempt to persist."""
    report = make_report(ReportStatus.UPLOADED)
    db = MagicMock()

    with pytest.raises(report_service.InvalidStatusTransitionError):
        report_service.transition_report_status(db, report, ReportStatus.FAILED)

    db.commit.assert_not_called()
    db.rollback.assert_not_called()


def test_database_failure_during_valid_transition_rolls_back():
    report = make_report(ReportStatus.UPLOADED)
    db = MagicMock()
    db.commit.side_effect = SQLAlchemyError("db down")

    with pytest.raises(report_service.ReportUpdateError):
        report_service.transition_report_status(db, report, ReportStatus.PROCESSING)

    db.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# No endpoint lets a patient set status directly
# ---------------------------------------------------------------------------

def test_no_patient_status_update_route_exists():
    """There must be no PATCH/PUT/POST route under /patient/reports other
    than the upload endpoint itself — i.e. no general-purpose status
    mutation route was added for this feature."""
    report_paths = {
        route.path: route.methods
        for route in app.router.routes
        if getattr(route, "path", "").startswith("/patient/reports")
    }
    for path, methods in report_paths.items():
        mutating = methods & {"PATCH", "PUT", "POST", "DELETE"}
        if path == "/patient/reports":
            assert mutating == {"POST"}, "only the upload POST should exist here"
        else:
            assert not mutating, f"unexpected mutating route added at {path}: {methods}"


# ---------------------------------------------------------------------------
# Upload behavior: starts UPLOADED, never auto-processes, existing
# hash/duplicate/validation behavior intact
# ---------------------------------------------------------------------------

def test_newly_uploaded_report_starts_as_uploaded_and_nothing_auto_processes():
    user = make_user(role=UserRole.PATIENT)
    report = make_report(ReportStatus.UPLOADED)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         patch("app.routers.reports.get_report_by_patient_and_hash", return_value=None), \
         patch("app.routers.reports.save_report_file", return_value="abc123.pdf"), \
         patch("app.routers.reports.create_report", return_value=report) as mock_create, \
         patch("app.services.report_service.transition_report_status") as mock_transition:
        response = client.post(
            "/patient/reports",
            files={"file": ("labs.pdf", VALID_PDF_BYTES, "application/pdf")},
            headers=auth_headers(user),
        )

    assert response.status_code == 201
    assert response.json()["status"] == "uploaded"
    # create_report is never called with any status other than the
    # implicit default, and nothing calls the transition function during
    # upload — upload is upload-only, no processing kicked off.
    _, kwargs = mock_create.call_args
    assert "status" not in kwargs
    mock_transition.assert_not_called()


def test_existing_duplicate_and_validation_behavior_still_intact():
    """Sanity check that adding new enum states didn't disturb the
    existing hash/duplicate short-circuit or PDF validation."""
    user = make_user(role=UserRole.PATIENT)
    existing = make_report(ReportStatus.UPLOADED)

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user), \
         patch("app.routers.reports.get_report_by_patient_and_hash", return_value=existing), \
         patch("app.routers.reports.save_report_file") as mock_save:
        response = client.post(
            "/patient/reports",
            files={"file": ("labs.pdf", VALID_PDF_BYTES, "application/pdf")},
            headers=auth_headers(user),
        )

    assert response.status_code == 409
    mock_save.assert_not_called()

    with patch("app.core.deps.get_user_by_id", return_value=user):
        bad_response = client.post(
            "/patient/reports",
            files={"file": ("labs.pdf", b"not a pdf", "application/pdf")},
            headers=auth_headers(user),
        )
    assert bad_response.status_code == 400
