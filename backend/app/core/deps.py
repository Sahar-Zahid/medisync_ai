"""
Reusable FastAPI authentication/authorization dependencies.

This is the single centralized place "who is the current user, and are
they allowed to be here" logic lives. Every future endpoint that needs an
authenticated user should depend on get_current_user (or one of the
role-scoped wrappers below) rather than re-parsing tokens itself.

The database role is always the source of truth. Nothing here ever trusts
a role claimed by the frontend — only the role stored on the User row
looked up from the token's user ID.
"""
from fastapi import Cookie, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.security import TokenError, decode_access_token
from app.models.user import User, UserRole
from app.services.user_service import get_user_by_id

# Name of the HttpOnly authentication cookie set by POST /auth/login and
# cleared by POST /auth/logout.
COOKIE_NAME = "access_token"

_NOT_AUTHENTICATED = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated.",
    headers={"WWW-Authenticate": "Bearer"},
)


def _extract_token(authorization: str | None, access_token_cookie: str | None) -> str:
    """Prefer a Bearer Authorization header (useful for API clients/tests),
    falling back to the HttpOnly cookie the frontend actually relies on."""
    if authorization:
        scheme, _, param = authorization.partition(" ")
        if scheme.lower() == "bearer" and param:
            return param

    if access_token_cookie:
        return access_token_cookie

    raise _NOT_AUTHENTICATED


def get_current_user(
    authorization: str | None = Header(default=None),
    access_token: str | None = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    """
    Resolve the authenticated User for the current request.

    1. Read the token (Bearer header, or the access_token cookie).
    2. Validate its signature and expiration.
    3. Extract the user ID.
    4. Look the user up in the database.
    5. Reject if the token is invalid/expired or the user no longer
       exists (e.g. deleted).

    Raises 401 for every failure case, with the same generic message —
    never distinguishing "no token", "expired token", or "deleted user"
    in the response.
    """
    token = _extract_token(authorization, access_token)

    try:
        payload = decode_access_token(token)
    except TokenError:
        raise _NOT_AUTHENTICATED

    user_id = payload.get("sub")
    if not user_id:
        raise _NOT_AUTHENTICATED

    user = get_user_by_id(db, user_id)
    if user is None:
        raise _NOT_AUTHENTICATED

    return user


def require_patient(current_user: User = Depends(get_current_user)) -> User:
    """Authorization helper for future patient-only endpoints. Role comes
    from the database (via get_current_user), never from the client."""
    if current_user.role != UserRole.PATIENT:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action is only available to patient accounts.",
        )
    return current_user


def require_doctor(current_user: User = Depends(get_current_user)) -> User:
    """Authorization helper for future doctor-only endpoints. Role comes
    from the database (via get_current_user), never from the client."""
    if current_user.role != UserRole.DOCTOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This action is only available to doctor accounts.",
        )
    return current_user
