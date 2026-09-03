"""
Password hashing and verification.

Uses argon2-cffi (the "argon2" package), a currently maintained Python
binding to the reference Argon2 implementation, configured for the
Argon2id variant — the variant recommended for general-purpose password
hashing (resistant to both GPU cracking and side-channel attacks). We
never implement the hashing algorithm ourselves.

This is the single, centralized place hashing configuration lives. The
rest of the app must only ever call hash_password() / verify_password()
from here — never construct a PasswordHasher or duplicate this logic
elsewhere.

Nothing in this module logs a plaintext or hashed password.
"""
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher, Type
from argon2.exceptions import VerifyMismatchError, VerificationError, InvalidHash

from app.core.config import settings

# Parameters appropriate for a normal web application's login/signup path
# (OWASP-aligned starting point for Argon2id on typical server hardware):
#   time_cost    - number of iterations
#   memory_cost  - memory usage in KiB (19 MiB here)
#   parallelism  - number of parallel threads
#   hash_len     - length of the derived hash, in bytes
#   salt_len     - length of the random salt, in bytes (auto-generated
#                  fresh per call, so identical passwords never produce
#                  identical hashes)
_hasher = PasswordHasher(
    time_cost=3,
    memory_cost=19 * 1024,
    parallelism=1,
    hash_len=32,
    salt_len=16,
    type=Type.ID,  # Argon2id
)


def hash_password(plain_password: str) -> str:
    """Hash a plaintext password. The result is what gets stored in
    User.hashed_password — the plaintext itself is never persisted.
    Encodes the algorithm, version, and parameters alongside the salt and
    hash, so verification works even if defaults above change later."""
    return _hasher.hash(plain_password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Check a plaintext password against a stored Argon2id hash. Used by
    login in a later step; included here since it belongs in the same
    service. Returns False (rather than raising) for a wrong password or
    an unrecognized/corrupt hash — callers never need to catch exceptions
    just to check a password."""
    try:
        return _hasher.verify(hashed_password, plain_password)
    except (VerifyMismatchError, VerificationError, InvalidHash):
        return False


# ---------------------------------------------------------------------------
# JWT creation / validation.
#
# Uses PyJWT (the "pyjwt" package), a well-established, actively maintained
# Python JWT library — we never hand-roll token signing/parsing. Like the
# hashing helpers above, this is the single centralized place JWT logic
# lives; nothing else in the app should call jwt.encode/jwt.decode directly.
# ---------------------------------------------------------------------------


class TokenError(Exception):
    """Raised for any invalid, expired, or malformed JWT. Deliberately a
    single generic exception type — callers (get_current_user) turn this
    into one generic 401, never distinguishing "expired" from "malformed"
    in the response, since that distinction has no legitimate use for a
    client and only helps an attacker."""
    pass


def create_access_token(data: dict) -> str:
    """
    Create a signed JWT access token.

    `data` should contain only what's needed to identify the authenticated
    user for later requests (e.g. {"sub": user_id, "role": role}) — never
    a password, hashed password, or other sensitive/medical information.
    An `exp` (expiration) claim is added automatically from
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES; callers must not pass their own `exp`.
    """
    if not settings.jwt_secret_key:
        raise RuntimeError(
            "JWT_SECRET_KEY is not set. Copy backend/.env.example to "
            "backend/.env and set JWT_SECRET_KEY to a strong secret."
        )

    to_encode = dict(data)
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=settings.jwt_access_token_expire_minutes
    )
    to_encode["exp"] = expire

    return jwt.encode(to_encode, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> dict:
    """
    Validate and decode a JWT access token.

    Checks both the signature and expiration (PyJWT does this
    automatically as part of jwt.decode). Raises TokenError — never a raw
    PyJWT exception — for any invalid or expired token, so callers have
    one exception type to handle.
    """
    if not settings.jwt_secret_key:
        raise RuntimeError(
            "JWT_SECRET_KEY is not set. Copy backend/.env.example to "
            "backend/.env and set JWT_SECRET_KEY to a strong secret."
        )

    try:
        return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])
    except jwt.PyJWTError:
        raise TokenError("Invalid or expired token.") from None
