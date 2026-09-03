"""
Tests for the read-only trusted-results history/timeline path:
GET /patient/results/history.

Mocked DB session (unittest.mock), no live PostgreSQL — same approach as
test_patient_results.py. Authentication follows the same pattern used
throughout: a real JWT is created with create_access_token, and
app.core.deps.get_user_by_id is patched so the token resolves to an
in-memory User without touching a real database.

Covers:
    1. authenticated patient can retrieve their history
    2. unauthenticated request rejected
    3. doctor cannot access the patient history endpoint
    4. only the authenticated patient's own TestResult rows are queried
       (current_user.id, never a client-supplied id)
    5. VERIFIED results appear
    6. CORRECTED results appear
    7/8. PENDING and REJECTED are excluded (same trust filter as
         GET /patient/results — no separate/weaker filter exists)
    9. deterministic newest-first ordering
    10. deterministic same-date ordering (verified_at, then id tiebreak)
    11. empty history returns an empty list, not an error
    12. no mutation occurs (no db.add/commit/delete)
    13. no internal identifiers/secrets/storage fields leak
    14. the route is GET-only and reuses the existing trusted-results
        query rather than a new/duplicate one

Run with:
    pytest backend/tests/test_results_history.py -v
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
from app.services import patient_result_service as svc

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
    result_date: date | None = date(2026, 6, 15),
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
    tr.result_date = result_date
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
    """Same MagicMock chain shape as test_patient_results.py's helper:
    db.query(TestResult).join(...).join(...).options(...).filter(...)
      .order_by(...).all() -> items
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
# 1. Authenticated patient can retrieve their history
# ---------------------------------------------------------------------------

def test_authenticated_patient_can_retrieve_history():
    patient = make_user(role=UserRole.PATIENT)
    result = make_test_result(status=TestResultStatus.VERIFIED)

    db, _ = mock_query_returning([result])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/results/history", headers=auth_headers(patient))

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["test_name"] == "Hemoglobin"


# ---------------------------------------------------------------------------
# 2. Unauthenticated request rejected
# ---------------------------------------------------------------------------

def test_unauthenticated_request_rejected():
    client = TestClient(app)
    response = client.get("/patient/results/history")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# 3. Doctor cannot access the patient history endpoint
# ---------------------------------------------------------------------------

def test_doctor_cannot_access_history_endpoint():
    doctor = make_user(role=UserRole.DOCTOR)
    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=doctor):
        response = client.get("/patient/results/history", headers=auth_headers(doctor))
    assert response.status_code == 403


# ---------------------------------------------------------------------------
# 4. Only the authenticated patient's own rows are queried
# ---------------------------------------------------------------------------

def test_service_called_with_current_users_id_directly():
    import inspect

    from app.routers.results import get_my_results_history

    sig = inspect.signature(get_my_results_history)
    assert "patient_id" not in sig.parameters
    assert "current_user" in sig.parameters


def test_history_service_delegates_to_existing_trusted_results_function():
    """The history service must not define its own query — it must
    reuse get_patient_trusted_results, the same already-audited function
    GET /patient/results uses, so ownership scoping and the trust filter
    can never drift between the two endpoints."""
    patient_id = uuid.uuid4()
    db = MagicMock()

    with patch.object(svc, "get_patient_trusted_results", return_value=[]) as mock_get:
        svc.get_patient_trusted_results_history(db, patient_id)

    mock_get.assert_called_once_with(db, patient_id)


def test_query_scoped_to_callers_own_patient_id():
    db, filter_chain = mock_query_returning([])
    svc.get_patient_trusted_results_history(db, uuid.uuid4())

    filter_args = filter_chain.call_args[0]
    filter_strs = [str(cond) for cond in filter_args]
    assert any("patient_id" in s for s in filter_strs)


# ---------------------------------------------------------------------------
# 5/6. VERIFIED and CORRECTED both appear
# ---------------------------------------------------------------------------

def test_verified_and_corrected_results_both_appear():
    patient = make_user(role=UserRole.PATIENT)
    verified = make_test_result(status=TestResultStatus.VERIFIED, test_name="Hemoglobin")
    corrected = make_test_result(status=TestResultStatus.CORRECTED, test_name="Glucose")

    db, _ = mock_query_returning([verified, corrected])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/results/history", headers=auth_headers(patient))

    assert response.status_code == 200
    body = response.json()
    statuses = {row["status"] for row in body}
    assert statuses == {"verified", "corrected"}


# ---------------------------------------------------------------------------
# 7/8. PENDING and REJECTED are excluded — same trust filter as
# GET /patient/results (_TRUSTED_STATUSES), no separate/weaker filter.
# ---------------------------------------------------------------------------

def test_history_uses_the_same_trusted_status_filter_as_results():
    assert set(svc._TRUSTED_STATUSES) == {
        TestResultStatus.VERIFIED,
        TestResultStatus.CORRECTED,
    }
    assert TestResultStatus.PENDING not in svc._TRUSTED_STATUSES
    assert TestResultStatus.REJECTED not in svc._TRUSTED_STATUSES

    db, filter_chain = mock_query_returning([])
    svc.get_patient_trusted_results_history(db, uuid.uuid4())

    filter_args = filter_chain.call_args[0]
    filter_strs = [str(cond) for cond in filter_args]
    assert any("status IN" in s for s in filter_strs)


def test_history_service_never_queries_candidate_result():
    """Structural proof: the history path has no import of
    CandidateResult at all — PENDING/REJECTED candidate data is
    structurally unreachable through this endpoint."""
    import app.services.patient_result_service as svc_module

    assert "CandidateResult" not in dir(svc_module)


# ---------------------------------------------------------------------------
# 9/10. Deterministic ordering (newest-first, then a stable same-date
# tiebreak) — asserted at the query-construction level, same as
# test_patient_results.py::test_ordering_is_deterministic.
# ---------------------------------------------------------------------------

def test_newest_first_ordering_is_deterministic():
    db, filter_chain = mock_query_returning([])
    svc.get_patient_trusted_results_history(db, uuid.uuid4())

    order_by_call = filter_chain.return_value.order_by
    assert order_by_call.called
    order_args = [str(arg) for arg in order_by_call.call_args[0]]
    # result_date drives primary ordering, descending (newest first).
    assert any("result_date" in a and "DESC" in a for a in order_args)


def test_same_date_ordering_has_deterministic_tiebreak():
    db, filter_chain = mock_query_returning([])
    svc.get_patient_trusted_results_history(db, uuid.uuid4())

    order_by_call = filter_chain.return_value.order_by
    order_args = [str(arg) for arg in order_by_call.call_args[0]]
    # verified_at is the secondary key, id is the final, always-unique
    # tiebreaker — ties never reorder between requests.
    assert any("verified_at" in a for a in order_args)
    assert any("id" in a for a in order_args)


# ---------------------------------------------------------------------------
# 11. Empty history returns an empty list, not an error
# ---------------------------------------------------------------------------

def test_empty_history_returns_empty_list():
    patient = make_user(role=UserRole.PATIENT)

    db, _ = mock_query_returning([])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/results/history", headers=auth_headers(patient))

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# 12. No mutation occurs
# ---------------------------------------------------------------------------

def test_endpoint_performs_no_writes():
    patient = make_user(role=UserRole.PATIENT)
    result = make_test_result()

    db, _ = mock_query_returning([result])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/results/history", headers=auth_headers(patient))

    assert response.status_code == 200
    db.add.assert_not_called()
    db.commit.assert_not_called()
    db.delete.assert_not_called()


# ---------------------------------------------------------------------------
# 13. No internal identifiers/secrets/storage fields leak
# ---------------------------------------------------------------------------

def test_response_never_exposes_internal_or_secret_fields():
    patient = make_user(role=UserRole.PATIENT)
    doctor_id = uuid.uuid4()
    result = make_test_result(doctor_id=doctor_id)

    db, _ = mock_query_returning([result])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/results/history", headers=auth_headers(patient))

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
# 14. Route is GET-only and existing routes remain registered
# ---------------------------------------------------------------------------

def test_history_route_is_get_only():
    from app.routers.results import router as results_router

    history_routes = [r for r in results_router.routes if r.path == "/patient/results/history"]
    assert len(history_routes) == 1
    assert history_routes[0].methods == {"GET"}


def test_existing_results_summary_and_history_routes_all_registered():
    from app.routers.results import router as results_router

    route_paths = {route.path for route in results_router.routes}
    assert "/patient/results" in route_paths
    assert "/patient/results/summary" in route_paths
    assert "/patient/results/history" in route_paths
