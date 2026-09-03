"""
Tests for signup: password hashing, email normalization, duplicate-email
handling, role validation, and response shape.

These tests use a mocked database session (unittest.mock), not a live
PostgreSQL connection — running them only requires the packages in
requirements.txt, not a running database server.

Run with:
    pytest backend/tests/test_signup.py -v
"""
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from app.core.security import hash_password, verify_password
from app.main import app
from app.models.user import User, UserRole
from app.schemas.user import UserCreate
from app.services import user_service
from app.services.user_service import (
    EmailAlreadyRegisteredError,
    UserCreationError,
    create_user,
)


def make_user_create(**overrides) -> UserCreate:
    data = dict(
        full_name="Ada Lovelace",
        email="Ada@Example.com",
        password="a-strong-password",
        role="patient",
    )
    data.update(overrides)
    return UserCreate(**data)


# ---------------------------------------------------------------------------
# 1 & 2. Valid patient / doctor signup succeeds (service layer)
# ---------------------------------------------------------------------------

def test_valid_patient_signup_succeeds():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    user_in = make_user_create(role="patient")
    user = create_user(db, user_in)

    assert user.role == UserRole.PATIENT
    db.add.assert_called_once()
    db.commit.assert_called_once()
    db.refresh.assert_called_once()


def test_valid_doctor_signup_succeeds():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    user_in = make_user_create(role="doctor")
    user = create_user(db, user_in)

    assert user.role == UserRole.DOCTOR
    db.add.assert_called_once()
    db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# 3. Duplicate email is rejected
# ---------------------------------------------------------------------------

def test_duplicate_email_rejected():
    db = MagicMock()
    existing = User(
        full_name="Existing User",
        email="ada@example.com",
        hashed_password="irrelevant",
        role=UserRole.PATIENT,
    )
    db.query.return_value.filter.return_value.first.return_value = existing

    user_in = make_user_create(email="ada@example.com")

    with pytest.raises(EmailAlreadyRegisteredError):
        create_user(db, user_in)

    db.add.assert_not_called()
    db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# 4. Email capitalization does not allow duplicate accounts
# ---------------------------------------------------------------------------

def test_email_capitalization_normalized():
    user_in = make_user_create(email="Ada@EXAMPLE.com")
    assert user_in.email == "ada@example.com"


def test_duplicate_email_race_condition_still_rejected():
    """If two signups race past the pre-check, the DB's unique constraint
    (surfaced as IntegrityError) must still be treated as a conflict, not
    a generic 500."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.commit.side_effect = IntegrityError("stmt", "params", Exception("dup"))

    user_in = make_user_create()

    with pytest.raises(EmailAlreadyRegisteredError):
        create_user(db, user_in)

    db.rollback.assert_called_once()


# ---------------------------------------------------------------------------
# 5. Invalid role is rejected
# ---------------------------------------------------------------------------

def test_invalid_role_rejected():
    with pytest.raises(ValidationError):
        make_user_create(role="nurse")


# ---------------------------------------------------------------------------
# 6. Password is stored only as a hash
# ---------------------------------------------------------------------------

def test_password_stored_only_as_hash():
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None

    user_in = make_user_create(password="a-strong-password")
    user = create_user(db, user_in)

    assert user.hashed_password != "a-strong-password"
    assert verify_password("a-strong-password", user.hashed_password)


def test_hash_password_produces_different_output_each_time():
    # Argon2id derives a fresh random salt per call, so hashing the same
    # password twice must not produce identical output.
    h1 = hash_password("same-password")
    h2 = hash_password("same-password")
    assert h1 != h2
    assert verify_password("same-password", h1)
    assert verify_password("same-password", h2)


def test_hash_password_uses_argon2id():
    # Encoded hash strings from argon2-cffi are prefixed with the variant
    # used, so this confirms Argon2id specifically (not Argon2i/Argon2d)
    # without hardcoding parameter values that might change later.
    encoded = hash_password("a-strong-password")
    assert encoded.startswith("$argon2id$")


def test_verify_password_rejects_wrong_password():
    hashed = hash_password("the-real-password")
    assert verify_password("a-completely-different-password", hashed) is False


def test_verify_password_rejects_malformed_hash():
    # A corrupt/unrecognized hash must fail closed (return False), not
    # raise, so callers never need special-case error handling.
    assert verify_password("anything", "not-a-real-hash") is False


# ---------------------------------------------------------------------------
# 7 & 8. API response excludes password / hashed_password
# ---------------------------------------------------------------------------

def test_signup_response_excludes_password_fields():
    client = TestClient(app)

    fake_user = User(
        full_name="Ada Lovelace",
        email="ada@example.com",
        hashed_password="$argon2id$v=19$m=19456,t=3,p=1$c29tZXNhbHQ$ZmFrZWhhc2h2YWx1ZQ",
        role=UserRole.PATIENT,
    )
    # created_at/updated_at are normally set by the DB server_default;
    # set them directly here since no live DB is involved in this test.
    import uuid
    from datetime import datetime, timezone

    fake_user.id = uuid.uuid4()
    fake_user.created_at = datetime.now(timezone.utc)
    fake_user.updated_at = datetime.now(timezone.utc)

    with patch.object(user_service, "create_user", return_value=fake_user):
        # The router calls create_user via the module-level import, so we
        # also patch it where it's looked up (app.routers.auth).
        with patch("app.routers.auth.create_user", return_value=fake_user):
            response = client.post(
                "/auth/signup",
                json={
                    "full_name": "Ada Lovelace",
                    "email": "Ada@Example.com",
                    "password": "a-strong-password",
                    "role": "patient",
                },
            )

    assert response.status_code == 201
    body = response.json()
    assert "password" not in body
    assert "hashed_password" not in body
    assert body["email"] == "ada@example.com"
    assert body["role"] == "patient"


def test_signup_duplicate_email_returns_409():
    client = TestClient(app)

    with patch(
        "app.routers.auth.create_user",
        side_effect=EmailAlreadyRegisteredError("ada@example.com"),
    ):
        response = client.post(
            "/auth/signup",
            json={
                "full_name": "Ada Lovelace",
                "email": "ada@example.com",
                "password": "a-strong-password",
                "role": "patient",
            },
        )

    assert response.status_code == 409
    assert "password" not in response.text.lower()


# ---------------------------------------------------------------------------
# 9. Database failure rolls back safely
# ---------------------------------------------------------------------------

def test_database_failure_rolls_back():
    from sqlalchemy.exc import OperationalError

    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = None
    db.commit.side_effect = OperationalError("stmt", "params", Exception("db down"))

    user_in = make_user_create()

    with pytest.raises(UserCreationError):
        create_user(db, user_in)

    db.rollback.assert_called_once()


def test_signup_db_failure_returns_500_without_leaking_internals():
    client = TestClient(app)

    with patch("app.routers.auth.create_user", side_effect=UserCreationError()):
        response = client.post(
            "/auth/signup",
            json={
                "full_name": "Ada Lovelace",
                "email": "ada@example.com",
                "password": "a-strong-password",
                "role": "patient",
            },
        )

    assert response.status_code == 500
    body = response.json()
    assert "traceback" not in str(body).lower()
    assert "sqlalchemy" not in str(body).lower()
