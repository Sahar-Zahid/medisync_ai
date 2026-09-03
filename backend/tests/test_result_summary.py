"""
Tests for the read-only AI trusted-results summary feature:
* GET /patient/results/summary
* app.services.patient_summary_service (trust-boundary orchestration)
* app.services.gemini_summary_service (schema validation boundary)

Mocked DB session and mocked Gemini boundary throughout (unittest.mock) —
same approach as test_patient_results.py / test_candidate_extraction.py.
No live PostgreSQL and no live Gemini API calls are made anywhere in this
file.

Covers (see task's Step 8 requirements):
    1. unauthenticated request blocked
    2. doctor blocked
    3. patient can only access their own trusted data
    4. only VERIFIED/CORRECTED TestResults are used
    5. PENDING candidates are excluded (never reachable at all)
    6. REJECTED candidates are excluded (never reachable at all)
    7. zero trusted results -> Gemini is NOT called
    8. Gemini receives only trusted-result data (no internal ids)
    9. Gemini output is schema validated
    10. malformed Gemini output is rejected safely
    11. Gemini failure does not create/modify any medical record
    12. endpoint performs no DB writes
    13. no verification status is changed
    14. no TestResult is created by summary generation
    15. summary contains the required disclaimer/safe framing

Run with:
    pytest backend/tests/test_result_summary.py -v
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.extraction import AbnormalityStatus, TestResult, TestResultStatus
from app.models.user import User, UserRole
from app.schemas.summary import GeminiSummaryResponse, SummaryInputResult
from app.services import patient_summary_service as svc
from app.services.gemini_summary_service import (
    GeminiSummaryNotConfiguredError,
    GeminiSummaryRequestError,
    GeminiSummaryValidationError,
)


# --- Helpers (mirrors test_patient_results.py) ---

def make_user(role: UserRole = UserRole.PATIENT, full_name: str = "Ada Lovelace", email: str = "ada@example.com") -> User:
    user = User(
        full_name=full_name,
        email=email,
        hashed_password=hash_password("a-strong-password"),
        role=role,
    )
    user.id = uuid.uuid4()
    user.created_at = datetime.now(timezone.utc)
    user.updated_at = datetime.now(timezone.utc)
    return user


def make_test_result(
    status: TestResultStatus = TestResultStatus.VERIFIED,
    test_name: str = "Hemoglobin",
    doctor_id: uuid.UUID | None = None,
) -> MagicMock:
    tr = MagicMock(spec=TestResult)
    tr.id = uuid.uuid4()
    tr.candidate_result_id = uuid.uuid4()
    tr.extraction_run_id = uuid.uuid4()
    tr.status = status
    tr.canonical_test = None
    tr.test_name = test_name
    tr.raw_value = "14.2"
    tr.normalized_value = Decimal("14.2")
    tr.normalized_unit = "g/dL"
    tr.result_date = date(2026, 6, 15)
    tr.reference_range_lower = Decimal("12.0")
    tr.reference_range_upper = Decimal("17.5")
    tr.reference_range_inclusive_lower = True
    tr.reference_range_inclusive_upper = True
    tr.abnormality_status = AbnormalityStatus.NORMAL
    tr.doctor_id = doctor_id or uuid.uuid4()
    tr.verified_at = datetime.now(timezone.utc)
    tr.correction_note = "internal doctor note — must never leak"
    tr.created_at = datetime.now(timezone.utc)
    return tr


def auth_headers(user: User) -> dict:
    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. Unauthenticated request blocked
# ---------------------------------------------------------------------------

def test_unauthenticated_request_rejected():
    client = TestClient(app)
    response = client.get("/patient/results/summary")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# 2. Doctor blocked
# ---------------------------------------------------------------------------

def test_doctor_cannot_access_summary_endpoint():
    doctor = make_user(role=UserRole.DOCTOR)
    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=doctor):
        response = client.get("/patient/results/summary", headers=auth_headers(doctor))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# 3. Patient can only access their own trusted data — no patient_id param
# ---------------------------------------------------------------------------

def test_service_called_with_current_users_id_directly():
    import inspect

    from app.routers.results import get_my_result_summary

    sig = inspect.signature(get_my_result_summary)
    assert "patient_id" not in sig.parameters
    assert "current_user" in sig.parameters


def test_get_patient_trusted_results_called_with_callers_id():
    patient = make_user(role=UserRole.PATIENT)
    db = MagicMock()

    with patch.object(svc, "get_patient_trusted_results", return_value=[]) as mock_get:
        svc.get_patient_result_summary(db, patient.id)

    mock_get.assert_called_once_with(db, patient.id)


# ---------------------------------------------------------------------------
# 4/5/6. Only VERIFIED/CORRECTED TestResults are used; the service never
# queries CandidateResult (PENDING/REJECTED) at all — it reuses the same
# get_patient_trusted_results() the existing endpoint uses, which already
# filters to VERIFIED/CORRECTED only (see test_patient_results.py for the
# filter-level assertions on that function itself).
# ---------------------------------------------------------------------------

def test_summary_reuses_existing_trusted_results_query_no_new_query():
    """The summary service must not define its own DB query — it must
    call the exact same, already-audited get_patient_trusted_results
    function the read-only results endpoint uses."""
    import inspect

    source = inspect.getsource(svc)
    assert "get_patient_trusted_results" in source
    # No import of CandidateResult anywhere in this module — PENDING/
    # REJECTED candidate data is structurally unreachable here. (The
    # module docstring mentions the name in prose, so check imports
    # specifically rather than the raw source text.)
    import app.services.patient_summary_service as svc_module

    assert "CandidateResult" not in dir(svc_module)


# ---------------------------------------------------------------------------
# 7. Zero trusted results -> Gemini is NOT called
# ---------------------------------------------------------------------------

def test_zero_trusted_results_returns_empty_state_without_calling_gemini():
    patient = make_user(role=UserRole.PATIENT)
    db = MagicMock()

    with patch.object(svc, "get_patient_trusted_results", return_value=[]), \
         patch(
             "app.services.patient_summary_service.generate_summary_from_trusted_results"
         ) as mock_gemini:
        result = svc.get_patient_result_summary(db, patient.id)

    mock_gemini.assert_not_called()
    assert result.has_trusted_results is False
    assert result.result_count == 0
    assert "No trusted laboratory results" in result.observations[0]
    assert result.generated_at is None


def test_empty_state_via_http_endpoint():
    patient = make_user(role=UserRole.PATIENT)
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient), \
         patch.object(svc, "get_patient_trusted_results", return_value=[]), \
         patch(
             "app.services.patient_summary_service.generate_summary_from_trusted_results"
         ) as mock_gemini:
        response = client.get("/patient/results/summary", headers=auth_headers(patient))

    assert response.status_code == 200
    body = response.json()
    assert body["has_trusted_results"] is False
    assert mock_gemini.assert_not_called() is None


# ---------------------------------------------------------------------------
# 8. Gemini receives only trusted-result data (no internal ids)
# ---------------------------------------------------------------------------

def test_gemini_receives_only_safe_fields_no_internal_ids():
    patient = make_user(role=UserRole.PATIENT)
    result = make_test_result()
    db = MagicMock()

    captured = {}

    def fake_generate(results):
        captured["results"] = results
        return GeminiSummaryResponse(observations=[])

    with patch.object(svc, "get_patient_trusted_results", return_value=[result]), \
         patch.object(svc, "generate_summary_from_trusted_results", side_effect=fake_generate):
        svc.get_patient_result_summary(db, patient.id)

    assert len(captured["results"]) == 1
    item = captured["results"][0]
    assert isinstance(item, SummaryInputResult)
    dumped = item.model_dump()
    forbidden_keys = {
        "id",
        "doctor_id",
        "candidate_result_id",
        "extraction_run_id",
        "correction_note",
        "canonical_test_id",
    }
    assert forbidden_keys.isdisjoint(dumped.keys())
    assert dumped["test_name"] == "Hemoglobin"
    assert str(result.doctor_id) not in str(dumped)


# ---------------------------------------------------------------------------
# 9/10. Gemini output is schema validated; malformed output rejected safely
# ---------------------------------------------------------------------------

def test_malformed_gemini_output_is_rejected_safely():
    patient = make_user(role=UserRole.PATIENT)
    result = make_test_result()
    db = MagicMock()

    with patch.object(svc, "get_patient_trusted_results", return_value=[result]), \
         patch.object(
             svc,
             "generate_summary_from_trusted_results",
             side_effect=GeminiSummaryValidationError(
                 "Gemini's response did not match the required schema."
             ),
         ):
        with pytest.raises(svc.SummaryGenerationError) as exc_info:
            svc.get_patient_result_summary(db, patient.id)

    assert "schema" in str(exc_info.value)


def test_gemini_summary_response_rejects_extra_fields():
    with pytest.raises(ValidationError):
        GeminiSummaryResponse.model_validate(
            {"observations": [{"text": "ok"}], "unexpected_field": "nope"}
        )


def test_gemini_summary_observation_rejects_empty_text():
    with pytest.raises(ValidationError):
        GeminiSummaryResponse.model_validate({"observations": [{"text": "   "}]})


def test_malformed_output_surfaces_as_503_via_http():
    patient = make_user(role=UserRole.PATIENT)
    result = make_test_result()
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient), \
         patch.object(svc, "get_patient_trusted_results", return_value=[result]), \
         patch.object(
             svc,
             "generate_summary_from_trusted_results",
             side_effect=GeminiSummaryValidationError(
                 "Gemini's response did not match the required schema."
             ),
         ):
        response = client.get("/patient/results/summary", headers=auth_headers(patient))

    assert response.status_code == 503
    # Never leaks raw SDK internals.
    assert "Traceback" not in response.text


# ---------------------------------------------------------------------------
# 11/12/13/14. Gemini failure (and success) never touches the DB — no
# writes, no verification-status changes, no TestResult creation.
# ---------------------------------------------------------------------------

def test_endpoint_performs_no_db_writes_on_success():
    patient = make_user(role=UserRole.PATIENT)
    result = make_test_result()
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient), \
         patch.object(svc, "get_patient_trusted_results", return_value=[result]), \
         patch.object(
             svc,
             "generate_summary_from_trusted_results",
             return_value=GeminiSummaryResponse(observations=[]),
         ):
        response = client.get("/patient/results/summary", headers=auth_headers(patient))

    assert response.status_code == 200
    db.add.assert_not_called()
    db.commit.assert_not_called()
    db.delete.assert_not_called()


def test_endpoint_performs_no_db_writes_on_gemini_failure():
    patient = make_user(role=UserRole.PATIENT)
    result = make_test_result()
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient), \
         patch.object(svc, "get_patient_trusted_results", return_value=[result]), \
         patch.object(
             svc,
             "generate_summary_from_trusted_results",
             side_effect=GeminiSummaryRequestError("Gemini request failed."),
         ):
        response = client.get("/patient/results/summary", headers=auth_headers(patient))

    assert response.status_code == 503
    db.add.assert_not_called()
    db.commit.assert_not_called()
    db.delete.assert_not_called()


def test_service_module_has_no_verification_status_mutation():
    """Structural proof: the summary service module never references the
    VERIFY/CORRECT/REJECT state machine or VerificationHistory at all."""
    import inspect

    import app.services.patient_summary_service as svc_module

    # No import of the VerificationHistory model or its action enum —
    # the module docstring mentions the name in prose, so check imports
    # specifically rather than the raw source text.
    assert "VerificationHistory" not in dir(svc_module)

    source = inspect.getsource(svc)
    for forbidden in (
        "verification_status =",
        ".status = TestResultStatus",
    ):
        assert forbidden not in source


def test_gemini_summary_service_module_never_imports_db_session():
    """Structural proof: the Gemini boundary itself has no database
    access at all, so it cannot write to TestResult/CandidateResult even
    if a caller tried to misuse it."""
    from app.services import gemini_summary_service as gemini_svc
    import inspect

    source = inspect.getsource(gemini_svc)
    assert "Session" not in source
    assert "db.add" not in source
    assert "db.commit" not in source


# ---------------------------------------------------------------------------
# 15. Summary contains the required disclaimer/safe framing
# ---------------------------------------------------------------------------

def test_disclaimer_always_present_and_server_controlled():
    patient = make_user(role=UserRole.PATIENT)
    result = make_test_result()
    db = MagicMock()

    with patch.object(svc, "get_patient_trusted_results", return_value=[result]), \
         patch.object(
             svc,
             "generate_summary_from_trusted_results",
             return_value=GeminiSummaryResponse(
                 observations=[{"text": "Hemoglobin is within its reference range."}]
             ),
         ):
        summary = svc.get_patient_result_summary(db, patient.id)

    assert "not a diagnosis" in summary.disclaimer
    assert "not medical advice" in summary.disclaimer


def test_disclaimer_present_even_in_empty_state():
    patient = make_user(role=UserRole.PATIENT)
    db = MagicMock()

    with patch.object(svc, "get_patient_trusted_results", return_value=[]):
        summary = svc.get_patient_result_summary(db, patient.id)

    assert "not a diagnosis" in summary.disclaimer


# ---------------------------------------------------------------------------
# Gemini not configured
# ---------------------------------------------------------------------------

def test_gemini_not_configured_raises_summary_generation_error():
    from app.services import gemini_summary_service as gemini_svc

    with patch.object(gemini_svc.settings, "gemini_api_key", None):
        with pytest.raises(GeminiSummaryNotConfiguredError):
            gemini_svc._get_client()


# ---------------------------------------------------------------------------
# Existing routes/behavior untouched
# ---------------------------------------------------------------------------

def test_existing_results_route_and_summary_route_both_registered():
    from app.routers.results import router as results_router

    route_paths = {route.path for route in results_router.routes}
    assert "/patient/results" in route_paths
    assert "/patient/results/summary" in route_paths
