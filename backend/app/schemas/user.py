"""
Pydantic schemas for User.

These describe request/response shapes only — no signup or login endpoints
are created here. Password hashing itself happens in a later authentication
step; UserCreate carries a plaintext `password` purely as API input, which
a future signup endpoint will hash before it ever touches the database (the
User model only ever stores `hashed_password`, never plaintext).
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.user import UserRole


class UserCreate(BaseModel):
    """Input shape for creating a user. Not wired to an endpoint yet."""

    full_name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8)
    role: UserRole

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        # Matches the lowercase storage convention in the User model, so
        # duplicate accounts can't be created via capitalization alone.
        return v.lower()


class UserResponse(BaseModel):
    """Safe user representation. Never includes password or hashed_password."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    email: EmailStr
    role: UserRole
    created_at: datetime
    updated_at: datetime


class UserProfileUpdate(BaseModel):
    """Input shape for PATCH /patient/profile.

    Deliberately the only editable field for this first implementation.
    There is no id/email/role/password field here at all — not just
    ignored on input, but structurally impossible to submit through this
    schema, so the router body can't accidentally trust a client-supplied
    value for any of those.
    """

    full_name: str = Field(min_length=1, max_length=255)

    @field_validator("full_name")
    @classmethod
    def strip_and_require_nonempty(cls, v: str) -> str:
        stripped = v.strip()
        if not stripped:
            raise ValueError("full_name must not be empty.")
        return stripped


class DoctorDirectoryEntry(BaseModel):
    """Safe, patient-facing representation of a single doctor, used by
    GET /patient/doctors.

    Deliberately narrower than UserResponse: no email (nothing in the
    product today needs a patient to see a doctor's email, so it's left
    out rather than exposed "just in case"), and of course never
    password/hashed_password. Only fields that exist on the real `users`
    row are included — no specialty/qualification/hospital/rating
    fields, since none of that data exists yet.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    full_name: str
    role: UserRole
    created_at: datetime
