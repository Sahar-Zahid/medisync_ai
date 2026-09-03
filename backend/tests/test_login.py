"""
Tests for login: credential verification, role matching, generic error
messages, JWT issuance/validation, get_current_user, and logout.

Mocked DB session (unittest.mock), no live PostgreSQL — same approach as
test_signup.py. Running these only requires the packages in
requirements-dev.txt, not a running database server.

Run with:
    pytest backend/tests/test_login.py -v
"""
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import jwt
import pytest
from fastapi.testclient import TestClient

from app.core.config import settings
from app.core.security import (
    TokenError,
    create_access_token,
    decode_access_token,
    hash_password,
)
from app.main import app
from app.models.user import User, UserRole
from app.services.user_service import authenticate_user


def make_user(role: UserRole = UserRole.PATIENT, password: str = "a-strong-password") -> User:
    user = User(
        full_name="Ada Lovelace",
        email="ada@example.com",
        hashed_password=hash_password(password),
        role=role,
    )
    user.id = uuid.uuid4()
    user.created_at = datetime.now(timezone.utc)
    user.updated_at = datetime.now(timezone.utc)
    return user


# ---------------------------------------------------------------------------
# 1 & 2. Correct patient / doctor login succeeds (service layer)
# ---------------------------------------------------------------------------

def test_correct_patient_login_succeeds():
    user = make_user(role=UserRole.PATIENT)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user

    result = authenticate_user(db, "ada@example.com", "a-strong-password", UserRole.PATIENT)
    assert result is user


def test_correct_doctor_login_succeeds():
    user = make_user(role=UserRole.DOCTOR)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user

    result = authenticate_user(db, "ada@example.com", "a-strong-password", UserRole.DOCTOR)
    assert result is user


# ---------------------------------------------------------------------------
# 3. Wrong password fails
# ---------------------------------------------------------------------------

def test_wrong_password_fails():
    user = make_user()
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user

    result = authenticate_user(db, "ada@example.com", "totally-wrong-password", UserRole.PATIENT)
    assert result is None


# ---------------------------------------------------------------------------
# 4. Unknown email fails
# ---------------------------------------------------------------------------

def test_unknown_email_fails():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    result = authenticate_user(db, "nobody@example.com", "whatever", UserRole.PATIENT)
    assert result is None


# ---------------------------------------------------------------------------
# 5. Correct credentials with wrong selected role fails
# ---------------------------------------------------------------------------

def test_correct_credentials_wrong_role_fails():
    user = make_user(role=UserRole.DOCTOR)
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = user

    result = authenticate_user(db, "ada@example.com", "a-strong-password", UserRole.PATIENT)
    assert result is None


# ---------------------------------------------------------------------------
# API-level: every failure returns the same generic message, never
# revealing which check actually failed
# ---------------------------------------------------------------------------

def test_login_endpoint_returns_generic_error_on_failure():
    client = TestClient(app)
    with patch("app.routers.auth.authenticate_user", return_value=None):
        response = client.post(
            "/auth/login",
            json={"email": "ada@example.com", "password": "wrong", "role": "patient"},
        )
    assert response.status_code == 401
    assert response.json()["detail"] == "Invalid email, password, or role."


def test_login_endpoint_succeeds_and_sets_cookie():
    user = make_user(role=UserRole.PATIENT)
    client = TestClient(app)
    with patch("app.routers.auth.authenticate_user", return_value=user):
        response = client.post(
            "/auth/login",
            json={"email": "ada@example.com", "password": "a-strong-password", "role": "patient"},
        )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert "access_token" in body
    assert "access_token" in response.cookies


# ---------------------------------------------------------------------------
# 6. Expired/invalid JWT fails
# ---------------------------------------------------------------------------

def test_expired_token_rejected():
    expired_payload = {
        "sub": str(uuid.uuid4()),
        "role": "patient",
        "exp": datetime.now(timezone.utc) - timedelta(minutes=1),
    }
    token = jwt.encode(expired_payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)
    with pytest.raises(TokenError):
        decode_access_token(token)


def test_malformed_token_rejected():
    with pytest.raises(TokenError):
        decode_access_token("not-a-real-token")


def test_token_signed_with_wrong_secret_rejected():
    token = jwt.encode(
        {"sub": str(uuid.uuid4()), "exp": datetime.now(timezone.utc) + timedelta(minutes=5)},
        "a-completely-different-secret",
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(TokenError):
        decode_access_token(token)


# ---------------------------------------------------------------------------
# 7. Missing authentication fails
# ---------------------------------------------------------------------------

def test_me_endpoint_without_token_returns_401():
    client = TestClient(app)
    response = client.get("/auth/me")
    assert response.status_code == 401


def test_me_endpoint_with_invalid_token_returns_401():
    client = TestClient(app)
    response = client.get("/auth/me", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_me_endpoint_with_valid_token_returns_user():
    user = make_user(role=UserRole.PATIENT)
    token = create_access_token({"sub": str(user.id), "role": user.role.value})

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=user):
        response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["email"] == "ada@example.com"
    assert "password" not in body
    assert "hashed_password" not in body


def test_me_endpoint_rejects_deleted_or_unknown_user():
    """A well-formed, unexpired token for a user that no longer exists
    (e.g. deleted) must still be rejected."""
    token = create_access_token({"sub": str(uuid.uuid4()), "role": "patient"})

    client = TestClient(app)
    with patch("app.core.deps.get_user_by_id", return_value=None):
        response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401


# ---------------------------------------------------------------------------
# 8 & 9. Password / hashed password never returned
# ---------------------------------------------------------------------------

def test_login_response_excludes_password_fields():
    user = make_user()
    client = TestClient(app)
    with patch("app.routers.auth.authenticate_user", return_value=user):
        response = client.post(
            "/auth/login",
            json={"email": "ada@example.com", "password": "a-strong-password", "role": "patient"},
        )
    body_text = response.text.lower()
    assert "hashed_password" not in body_text
    assert "a-strong-password" not in body_text


# ---------------------------------------------------------------------------
# 10. JWT does not contain password or hashed_password
# ---------------------------------------------------------------------------

def test_jwt_payload_excludes_password_fields():
    user = make_user()
    token = create_access_token({"sub": str(user.id), "role": user.role.value})
    payload = jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])

    assert "password" not in payload
    assert "hashed_password" not in payload
    assert set(payload.keys()) <= {"sub", "role", "exp"}


# ---------------------------------------------------------------------------
# 11. Logout clears the authentication state
# ---------------------------------------------------------------------------

def test_logout_clears_cookie():
    client = TestClient(app)
    response = client.post("/auth/logout")
    assert response.status_code == 200

    set_cookie = response.headers.get("set-cookie", "")
    assert "access_token=" in set_cookie
    # A cleared cookie is expired/zero-lifetime, not just re-sent.
    assert ('Max-Age=0' in set_cookie) or ('expires=' in set_cookie.lower())
