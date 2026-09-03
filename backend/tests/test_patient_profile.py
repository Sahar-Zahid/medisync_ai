"""
Tests for the patient profile endpoints: GET /patient/profile and
PATCH /patient/profile.

Mocked DB session (unittest.mock), no live PostgreSQL — same approach as
test_signup.py / test_login.py. Authentication follows the same pattern
as test_login.py's /auth/me tests: a real JWT is created with
create_access_token, and app.core.deps.get_user_by_id is patched so the
token resolves to an in-memory User without touching a real database.
For PATCH, app.core.database.get_db is overridden via FastAPI's
dependency_overrides so update_user_profile's db.commit()/db.refresh()
calls hit a MagicMock instead of a real connection.

Run with:
    pytest backend/tests/test_patient_profile.py -v
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


def make_user(role: UserRole = UserRole.PATIENT, full_name: str = "Ada Lovelace") -> User:
    user = User(
        full_name=full_name,
        email="ada@example.com",
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


@pytest.fixture(autouse=True)
def _clear_overrides():
    """Ensure dependency_overrides never leaks between tests."""
    yield
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# GET /patient/profile
# ---------------------------------------------------------------------------

def test_authenticated_patient_can_get_profile():
    user = make_user(role=UserRole.PATIENT)
    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user):
        response = client.get("/patient/profile", headers=auth_headers(user))

    assert response.status_code == 200
    body = response.json()
    assert body["full_name"] == "Ada Lovelace"
    assert body["email"] == "ada@example.com"
    assert body["role"] == "patient"


def test_unauthenticated_get_profile_rejected():
    client = TestClient(app)
    response = client.get("/patient/profile")
    assert response.status_code == 401


def test_doctor_cannot_get_patient_profile():
    doctor = make_user(role=UserRole.DOCTOR)
    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=doctor):
        response = client.get("/patient/profile", headers=auth_headers(doctor))

    assert response.status_code == 403


def test_get_profile_never_returns_password_fields():
    user = make_user(role=UserRole.PATIENT)
    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user):
        response = client.get("/patient/profile", headers=auth_headers(user))

    body_text = response.text.lower()
    assert "hashed_password" not in body_text
    assert "password" not in body_text


# ---------------------------------------------------------------------------
# PATCH /patient/profile
# ---------------------------------------------------------------------------

def test_patient_can_update_own_full_name():
    user = make_user(role=UserRole.PATIENT, full_name="Old Name")
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user):
        response = client.patch(
            "/patient/profile",
            json={"full_name": "New Name"},
            headers=auth_headers(user),
        )

    assert response.status_code == 200
    assert response.json()["full_name"] == "New Name"
    db.commit.assert_called_once()
    db.refresh.assert_called_once()


def test_updated_full_name_is_persisted_on_the_user_object():
    """update_user_profile mutates the same User instance passed in (the
    one resolved from the authenticated session) rather than looking up a
    separate row — this asserts that mutation actually happened."""
    user = make_user(role=UserRole.PATIENT, full_name="Old Name")
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user):
        client.patch(
            "/patient/profile",
            json={"full_name": "Updated Name"},
            headers=auth_headers(user),
        )

    assert user.full_name == "Updated Name"


def test_empty_full_name_rejected():
    user = make_user(role=UserRole.PATIENT)
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user):
        response = client.patch(
            "/patient/profile",
            json={"full_name": "   "},
            headers=auth_headers(user),
        )

    assert response.status_code == 422
    db.commit.assert_not_called()


def test_missing_full_name_rejected():
    user = make_user(role=UserRole.PATIENT)
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user):
        response = client.patch(
            "/patient/profile", json={}, headers=auth_headers(user)
        )

    assert response.status_code == 422


def test_full_name_over_255_chars_rejected():
    user = make_user(role=UserRole.PATIENT)
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user):
        response = client.patch(
            "/patient/profile",
            json={"full_name": "x" * 256},
            headers=auth_headers(user),
        )

    assert response.status_code == 422


def test_full_name_is_stripped_of_surrounding_whitespace():
    user = make_user(role=UserRole.PATIENT, full_name="Old Name")
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user):
        response = client.patch(
            "/patient/profile",
            json={"full_name": "  Padded Name  "},
            headers=auth_headers(user),
        )

    assert response.status_code == 200
    assert response.json()["full_name"] == "Padded Name"


def test_email_cannot_be_changed_through_profile_update():
    user = make_user(role=UserRole.PATIENT)
    original_email = user.email
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user):
        response = client.patch(
            "/patient/profile",
            json={"full_name": "Ada Lovelace", "email": "hacker@example.com"},
            headers=auth_headers(user),
        )

    assert response.status_code == 200
    assert user.email == original_email
    assert response.json()["email"] == original_email


def test_role_cannot_be_changed_through_profile_update():
    user = make_user(role=UserRole.PATIENT)
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user):
        response = client.patch(
            "/patient/profile",
            json={"full_name": "Ada Lovelace", "role": "doctor"},
            headers=auth_headers(user),
        )

    assert response.status_code == 200
    assert user.role == UserRole.PATIENT
    assert response.json()["role"] == "patient"


def test_patch_profile_never_returns_password_fields():
    user = make_user(role=UserRole.PATIENT)
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user):
        response = client.patch(
            "/patient/profile",
            json={"full_name": "Ada Lovelace"},
            headers=auth_headers(user),
        )

    body_text = response.text.lower()
    assert "hashed_password" not in body_text
    assert "password" not in body_text


def test_unauthenticated_patch_profile_rejected():
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    response = client.patch("/patient/profile", json={"full_name": "New Name"})

    assert response.status_code == 401
    db.commit.assert_not_called()


def test_doctor_cannot_patch_patient_profile():
    doctor = make_user(role=UserRole.DOCTOR)
    db = MagicMock()
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=doctor):
        response = client.patch(
            "/patient/profile",
            json={"full_name": "New Name"},
            headers=auth_headers(doctor),
        )

    assert response.status_code == 403
    db.commit.assert_not_called()


def test_database_failure_on_update_rolls_back_safely():
    user = make_user(role=UserRole.PATIENT)
    db = MagicMock()
    db.commit.side_effect = OperationalError("stmt", "params", Exception("db down"))
    app.dependency_overrides[get_db] = lambda: db

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user):
        response = client.patch(
            "/patient/profile",
            json={"full_name": "New Name"},
            headers=auth_headers(user),
        )

    assert response.status_code == 500
    body = response.json()
    assert "traceback" not in str(body).lower()
    assert "sqlalchemy" not in str(body).lower()
    assert "operationalerror" not in str(body).lower()
    db.rollback.assert_called_once()
