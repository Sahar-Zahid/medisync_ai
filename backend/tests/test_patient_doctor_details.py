"""
Tests for the patient-facing doctor-details endpoint:
GET /patient/doctors/{doctor_id}.

Mocked DB session (unittest.mock), no live PostgreSQL — same approach as
test_patient_doctors.py. Authentication follows the same pattern used
throughout: a real JWT is created with create_access_token, and
app.core.deps.get_user_by_id is patched so the token resolves to an
in-memory User without touching a real database. This endpoint
additionally needs a db session for the get_doctor_by_id() query itself,
so app.core.database.get_db is overridden via FastAPI's
dependency_overrides with a MagicMock.

Run with:
    pytest backend/tests/test_patient_doctor_details.py -v
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError

from app.core.database import get_db
from app.core.security import create_access_token, hash_password
from app.main import app
from app.models.user import User, UserRole


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


def auth_headers(user: User) -> dict:
    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    return {"Authorization": f"Bearer {token}"}


def mock_query_returning_first(item):
    """Build the MagicMock chain used by get_doctor_by_id():
    db.query(User).filter(...).first() -> item
    """
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = item
    return db


@pytest.fixture(autouse=True)
def _clear_overrides():
    """Ensure dependency_overrides never leaks between tests."""
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# 1 & 2. Authenticated patient can retrieve an existing doctor, correctly
# ---------------------------------------------------------------------------

def test_authenticated_patient_can_retrieve_existing_doctor():
    patient = make_user(role=UserRole.PATIENT)
    doctor = make_user(role=UserRole.DOCTOR, full_name="Dr. Grace Hopper", email="grace@example.com")

    db = mock_query_returning_first(doctor)
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get(f"/patient/doctors/{doctor.id}", headers=auth_headers(patient))

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == str(doctor.id)
    assert body["full_name"] == "Dr. Grace Hopper"
    assert body["role"] == "doctor"


# ---------------------------------------------------------------------------
# 3. Unauthenticated user is rejected
# ---------------------------------------------------------------------------

def test_unauthenticated_request_rejected():
    doctor_id = uuid.uuid4()
    client = TestClient(app)
    response = client.get(f"/patient/doctors/{doctor_id}")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# 4. Doctor cannot access the patient-only endpoint
# ---------------------------------------------------------------------------

def test_doctor_cannot_access_patient_only_endpoint():
    doctor = make_user(role=UserRole.DOCTOR)
    other_doctor_id = uuid.uuid4()

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=doctor):
        response = client.get(f"/patient/doctors/{other_doctor_id}", headers=auth_headers(doctor))

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# 5. A patient's ID cannot be retrieved through this endpoint
# ---------------------------------------------------------------------------

def test_patient_id_is_not_retrievable_as_a_doctor():
    """get_doctor_by_id() filters on role=doctor at the query level, so a
    patient ID must come back as a 404 — this test asserts the query was
    actually built with both the ID and the role filter."""
    patient_caller = make_user(role=UserRole.PATIENT)
    target_patient_id = uuid.uuid4()

    # The mocked query returns None, simulating the DB-level role filter
    # excluding a patient row even though the ID technically exists.
    db = mock_query_returning_first(None)
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient_caller):
        response = client.get(
            f"/patient/doctors/{target_patient_id}", headers=auth_headers(patient_caller)
        )

    assert response.status_code == 404
    filter_call_args = db.query.return_value.filter.call_args
    assert filter_call_args is not None


# ---------------------------------------------------------------------------
# 6. Unknown doctor UUID returns 404
# ---------------------------------------------------------------------------

def test_unknown_doctor_uuid_returns_404():
    patient = make_user(role=UserRole.PATIENT)
    unknown_id = uuid.uuid4()

    db = mock_query_returning_first(None)
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get(f"/patient/doctors/{unknown_id}", headers=auth_headers(patient))

    assert response.status_code == 404
    assert response.json()["detail"] == "Doctor not found."


# ---------------------------------------------------------------------------
# 7. Malformed UUID is handled safely
# ---------------------------------------------------------------------------

def test_malformed_uuid_returns_404_not_a_500():
    patient = make_user(role=UserRole.PATIENT)

    # No DB override needed: get_doctor_by_id() catches the malformed ID
    # before ever building a query, so db.query should never be called.
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/doctors/not-a-valid-uuid", headers=auth_headers(patient))

    assert response.status_code == 404
    db.query.assert_not_called()


# ---------------------------------------------------------------------------
# 8. Password/hash fields are never present in the response
# ---------------------------------------------------------------------------

def test_response_never_contains_password_fields():
    patient = make_user(role=UserRole.PATIENT)
    doctor = make_user(role=UserRole.DOCTOR, full_name="Dr. Grace Hopper", email="grace@example.com")

    db = mock_query_returning_first(doctor)
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get(f"/patient/doctors/{doctor.id}", headers=auth_headers(patient))

    body_text = response.text.lower()
    assert "hashed_password" not in body_text
    assert "password" not in body_text
    # Same withholding as the directory listing (DoctorDirectoryEntry has
    # no email field at all).
    assert "email" not in response.json()
    assert "grace@example.com" not in response.text


# ---------------------------------------------------------------------------
# 9. Database failure is handled according to existing project conventions
# ---------------------------------------------------------------------------

def test_database_failure_handled_safely():
    patient = make_user(role=UserRole.PATIENT)
    doctor_id = uuid.uuid4()

    db = MagicMock()
    db.query.return_value.filter.return_value.first.side_effect = OperationalError(
        "stmt", "params", Exception("db down")
    )
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get(f"/patient/doctors/{doctor_id}", headers=auth_headers(patient))

    assert response.status_code == 500
    body = response.json()
    assert "traceback" not in str(body).lower()
    assert "sqlalchemy" not in str(body).lower()
    assert "operationalerror" not in str(body).lower()
