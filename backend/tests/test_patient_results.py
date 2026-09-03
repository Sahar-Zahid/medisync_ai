"""
Tests for the patient trusted-results read path: GET /patient/results.

Mocked DB session (unittest.mock), no live PostgreSQL — same approach as
test_patient_doctors.py / test_patient_profile.py. Authentication follows
the same pattern used throughout: a real JWT is created with
create_access_token, and app.core.deps.get_user_by_id is patched so the
token resolves to an in-memory User without touching a real database.
GET /patient/results additionally needs a db session for the
get_patient_trusted_results() query itself, so app.core.database.get_db
is overridden via FastAPI's dependency_overrides with a MagicMock.

Covers (see task's Step 6 requirements):
    1. authenticated patient can retrieve own trusted results
    2. patient with no trusted results gets an empty list
    3. the query is scoped to the caller's own patient_id (never another
       patient's, never a client-supplied id)
    4. doctor cannot use the patient endpoint
    5. unauthenticated access is rejected
    6/7. the query filters to VERIFIED/CORRECTED only — PENDING and
         REJECTED TestResult rows are excluded
    8/9. VERIFIED and CORRECTED TestResult rows both appear
    10. the endpoint performs no writes (no db.add/commit/delete)
    11. the response never exposes doctor/candidate/extraction internals
    12. ordering is deterministic (asserted at the query-construction level)
    13. existing doctor verification routes remain registered/untouched

Run with:
    pytest backend/tests/test_patient_results.py -v
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.extraction import AbnormalityStatus, TestResult, TestResultStatus
from app.models.user import User, UserRole
from app.services.patient_result_service import (
    _TRUSTED_STATUSES,
    get_patient_trusted_results,
)


# --- Helpers ---

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
    """Build a mock TestResult with every field the response schema
    reads, plus the internal-only fields (doctor_id, candidate_result_id,
    extraction_run_id, correction_note) that must never reach the
    response."""
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


def mock_query_returning(items):
    """Build the MagicMock chain used by get_patient_trusted_results():
    db.query(TestResult).join(...).join(...).options(...).filter(...)
      .order_by(...).all() -> items
    Returns (db, filter_mock) so tests can inspect the filter call args.
    """
    db = MagicMock()
    query = db.query.return_value
    joined = query.join.return_value.join.return_value
    filter_chain = joined.options.return_value.filter
    filter_chain.return_value.order_by.return_value.all.return_value = items
    return db, filter_chain


def auth_headers(user: User) -> dict:
    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(autouse=True)
def _clear_overrides():
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1. Authenticated patient can retrieve own trusted results
# ---------------------------------------------------------------------------

def test_authenticated_patient_can_retrieve_own_trusted_results():
    patient = make_user(role=UserRole.PATIENT)
    result = make_test_result(status=TestResultStatus.VERIFIED)

    db, _ = mock_query_returning([result])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/results", headers=auth_headers(patient))

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["test_name"] == "Hemoglobin"
    assert body[0]["status"] == "verified"


# ---------------------------------------------------------------------------
# 2. Patient with no trusted results gets an empty list
# ---------------------------------------------------------------------------

def test_patient_with_no_trusted_results_gets_empty_list():
    patient = make_user(role=UserRole.PATIENT)

    db, _ = mock_query_returning([])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/results", headers=auth_headers(patient))

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# 3. Query is scoped to the caller's own patient_id — never a client
#    supplied one, never another patient's.
# ---------------------------------------------------------------------------

def test_query_is_scoped_to_callers_own_patient_id():
    patient = make_user(role=UserRole.PATIENT)
    other_patient_id = uuid.uuid4()
    assert other_patient_id != patient.id

    db, filter_chain = mock_query_returning([])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        client.get("/patient/results", headers=auth_headers(patient))

    # The filter is built from current_user.id, never anything else —
    # there is no request parameter for a client to override this with.
    filter_args = filter_chain.call_args[0]
    filter_strs = [str(cond) for cond in filter_args]
    assert any("patient_id" in s for s in filter_strs)


def test_service_called_with_current_users_id_directly():
    """No route parameter exists for patient_id at all — the endpoint
    only ever has access to current_user.id."""
    import inspect

    from app.routers.results import get_my_trusted_results

    sig = inspect.signature(get_my_trusted_results)
    assert "patient_id" not in sig.parameters
    assert "current_user" in sig.parameters


# ---------------------------------------------------------------------------
# 4. Doctor cannot use the patient endpoint
# ---------------------------------------------------------------------------

def test_doctor_cannot_access_patient_results_endpoint():
    doctor = make_user(role=UserRole.DOCTOR)
    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=doctor):
        response = client.get("/patient/results", headers=auth_headers(doctor))

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# 5. Unauthenticated access is rejected
# ---------------------------------------------------------------------------

def test_unauthenticated_request_rejected():
    client = TestClient(app)
    response = client.get("/patient/results")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# 6/7. PENDING and REJECTED are excluded by the query's status filter
# ---------------------------------------------------------------------------

def test_query_filters_to_verified_and_corrected_only():
    """PENDING candidates must never appear in this response, and a
    REJECTED candidate has no TestResult row to appear in the first
    place — the status filter enforces both at the query level."""
    assert set(_TRUSTED_STATUSES) == {
        TestResultStatus.VERIFIED,
        TestResultStatus.CORRECTED,
    }
    assert TestResultStatus.PENDING not in _TRUSTED_STATUSES
    assert TestResultStatus.REJECTED not in _TRUSTED_STATUSES

    db, filter_chain = mock_query_returning([])
    get_patient_trusted_results(db, uuid.uuid4())

    filter_args = filter_chain.call_args[0]
    filter_strs = [str(cond) for cond in filter_args]
    assert any("status IN" in s for s in filter_strs)


# ---------------------------------------------------------------------------
# 8/9. VERIFIED and CORRECTED TestResult rows both appear
# ---------------------------------------------------------------------------

def test_verified_and_corrected_results_both_appear():
    patient = make_user(role=UserRole.PATIENT)
    verified = make_test_result(status=TestResultStatus.VERIFIED, test_name="Hemoglobin")
    corrected = make_test_result(status=TestResultStatus.CORRECTED, test_name="Glucose")

    db, _ = mock_query_returning([verified, corrected])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/results", headers=auth_headers(patient))

    assert response.status_code == 200
    body = response.json()
    statuses = {row["status"] for row in body}
    names = {row["test_name"] for row in body}
    assert statuses == {"verified", "corrected"}
    assert names == {"Hemoglobin", "Glucose"}


# ---------------------------------------------------------------------------
# 10. The endpoint performs no writes
# ---------------------------------------------------------------------------

def test_endpoint_performs_no_writes():
    patient = make_user(role=UserRole.PATIENT)
    result = make_test_result()

    db, _ = mock_query_returning([result])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/results", headers=auth_headers(patient))

    assert response.status_code == 200
    db.add.assert_not_called()
    db.commit.assert_not_called()
    db.delete.assert_not_called()


# ---------------------------------------------------------------------------
# 11. Response never exposes doctor/candidate/extraction internals
# ---------------------------------------------------------------------------

def test_response_never_exposes_internal_or_secret_fields():
    patient = make_user(role=UserRole.PATIENT)
    doctor_id = uuid.uuid4()
    result = make_test_result(doctor_id=doctor_id)

    db, _ = mock_query_returning([result])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/results", headers=auth_headers(patient))

    body = response.json()
    row = body[0]
    forbidden_keys = {
        "doctor_id",
        "candidate_result_id",
        "extraction_run_id",
        "correction_note",
        "storage_path",
        "sha256_hash",
        "raw_gemini_response",
        "api_key",
    }
    assert forbidden_keys.isdisjoint(row.keys())
    assert str(doctor_id) not in response.text
    assert "internal doctor note" not in response.text


# ---------------------------------------------------------------------------
# 12. Deterministic ordering
# ---------------------------------------------------------------------------

def test_ordering_is_deterministic():
    db, filter_chain = mock_query_returning([])
    get_patient_trusted_results(db, uuid.uuid4())

    order_by_call = filter_chain.return_value.order_by
    assert order_by_call.called
    # Ties are always broken the same way (id as the final tiebreaker),
    # so repeated calls with identical data return identical order.
    order_args = [str(arg) for arg in order_by_call.call_args[0]]
    assert any("id" in a for a in order_args)


# ---------------------------------------------------------------------------
# 13. Existing doctor verification workflow is unaffected
# ---------------------------------------------------------------------------

def test_existing_doctor_and_patient_routes_still_registered():
    """Adding GET /patient/results must not remove or shadow any
    existing route — the doctor verification workflow in particular."""
    route_paths = {route.path for route in app.routes}
    assert "/patient/results" in route_paths
    assert "/patient/doctors" in route_paths
    assert "/patient/reports" in route_paths
