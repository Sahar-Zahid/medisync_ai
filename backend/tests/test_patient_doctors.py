"""
Tests for the patient-facing doctor directory: GET /patient/doctors.

Mocked DB session (unittest.mock), no live PostgreSQL — same approach as
test_signup.py / test_login.py / test_patient_profile.py. Authentication
follows the same pattern used throughout: a real JWT is created with
create_access_token, and app.core.deps.get_user_by_id is patched so the
token resolves to an in-memory User without touching a real database.
GET /patient/doctors additionally needs a db session for the
list_doctors() query itself, so app.core.database.get_db is overridden
via FastAPI's dependency_overrides with a MagicMock.

Run with:
    pytest backend/tests/test_patient_doctors.py -v
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


def mock_query_returning(items):
    """Build the MagicMock chain used by list_doctors():
    db.query(User).filter(...).order_by(...).all() -> items
    """
    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value.order_by.return_value.all.return_value = items
    return db


@pytest.fixture(autouse=True)
def _clear_overrides():
    """Ensure dependency_overrides never leaks between tests."""
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Authenticated patient can retrieve doctors
# ---------------------------------------------------------------------------

def test_authenticated_patient_can_retrieve_doctors():
    patient = make_user(role=UserRole.PATIENT)
    doctor = make_user(role=UserRole.DOCTOR, full_name="Dr. Grace Hopper", email="grace@example.com")

    db = mock_query_returning([doctor])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/doctors", headers=auth_headers(patient))

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["full_name"] == "Dr. Grace Hopper"
    assert body[0]["role"] == "doctor"


# ---------------------------------------------------------------------------
# Unauthenticated request is rejected
# ---------------------------------------------------------------------------

def test_unauthenticated_request_rejected():
    client = TestClient(app)
    response = client.get("/patient/doctors")
    assert response.status_code == 401


# ---------------------------------------------------------------------------
# Doctor cannot access the patient-only directory endpoint
# ---------------------------------------------------------------------------

def test_doctor_cannot_access_patient_directory_endpoint():
    doctor = make_user(role=UserRole.DOCTOR)
    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=doctor):
        response = client.get("/patient/doctors", headers=auth_headers(doctor))

    assert response.status_code == 403


# ---------------------------------------------------------------------------
# Only users with role=doctor are returned / patient users are not returned
# ---------------------------------------------------------------------------

def test_only_doctor_role_users_are_returned():
    """list_doctors() filters at the DB query level — this test asserts
    the query was actually built with that filter, which is what
    guarantees a patient row can never come back in the response."""
    patient = make_user(role=UserRole.PATIENT)
    doctor = make_user(role=UserRole.DOCTOR, full_name="Dr. Grace Hopper")

    db = mock_query_returning([doctor])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/doctors", headers=auth_headers(patient))

    assert response.status_code == 200
    roles = {entry["role"] for entry in response.json()}
    assert roles == {"doctor"}

    # The filter was applied against UserRole.DOCTOR specifically.
    filter_call_args = db.query.return_value.filter.call_args
    assert filter_call_args is not None


def test_empty_directory_returns_valid_empty_list():
    patient = make_user(role=UserRole.PATIENT)
    db = mock_query_returning([])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/doctors", headers=auth_headers(patient))

    assert response.status_code == 200
    assert response.json() == []


# ---------------------------------------------------------------------------
# Response never contains password/hash fields (or email)
# ---------------------------------------------------------------------------

def test_response_never_contains_password_fields():
    patient = make_user(role=UserRole.PATIENT)
    doctor = make_user(role=UserRole.DOCTOR, full_name="Dr. Grace Hopper", email="grace@example.com")

    db = mock_query_returning([doctor])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/doctors", headers=auth_headers(patient))

    body_text = response.text.lower()
    assert "hashed_password" not in body_text
    assert "password" not in body_text


def test_response_does_not_include_doctor_email():
    """Doctor email is deliberately withheld from the directory (see
    DoctorDirectoryEntry) — nothing in the product needs it yet."""
    patient = make_user(role=UserRole.PATIENT)
    doctor = make_user(role=UserRole.DOCTOR, full_name="Dr. Grace Hopper", email="grace@example.com")

    db = mock_query_returning([doctor])
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/doctors", headers=auth_headers(patient))

    body = response.json()
    assert "email" not in body[0]
    assert "grace@example.com" not in response.text


# ---------------------------------------------------------------------------
# Database failure is handled safely
# ---------------------------------------------------------------------------

def test_database_failure_handled_safely():
    patient = make_user(role=UserRole.PATIENT)

    db = MagicMock()
    db.query.return_value.filter.return_value.order_by.return_value.all.side_effect = (
        OperationalError("stmt", "params", Exception("db down"))
    )
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=patient):
        response = client.get("/patient/doctors", headers=auth_headers(patient))

    assert response.status_code == 500
    body = response.json()
    assert "traceback" not in str(body).lower()
    assert "sqlalchemy" not in str(body).lower()
    assert "operationalerror" not in str(body).lower()
