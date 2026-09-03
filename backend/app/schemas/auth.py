"""
Pydantic schemas for authentication (login).

Kept separate from app/schemas/user.py since these describe the login
request/response shapes, not the User resource itself.
"""
from pydantic import BaseModel, EmailStr, field_validator

from app.models.user import UserRole


class LoginRequest(BaseModel):
    """Input shape for POST /auth/login.

    `role` here is only the role the client is requesting to log in as —
    the login service compares it against the user's actual database role
    and rejects the login on any mismatch. This schema does not, and must
    not, ever be treated as authoritative about who the user is.
    """

    email: EmailStr
    password: str
    role: UserRole

    @field_validator("email")
    @classmethod
    def normalize_email(cls, v: str) -> str:
        # Matches the lowercase storage/lookup convention used by signup.
        return v.lower()


class TokenResponse(BaseModel):
    """Response shape for a successful login.

    The frontend does not read or store `access_token` from this body —
    the real session is the HttpOnly cookie set alongside this response
    (see app/routers/auth.py). This field exists so the endpoint stays
    directly testable (curl, pytest, other API clients) without a cookie
    jar.
    """

    access_token: str
    token_type: str = "bearer"
