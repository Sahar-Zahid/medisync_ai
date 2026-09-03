"""
Authentication routes: signup, login, logout, and "who am I".

Still no doctor-patient relationships or dashboard/data endpoints — this
router stays auth-scoped only.
"""
from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import COOKIE_NAME, get_current_user
from app.core.security import create_access_token
from app.models.user import User
from app.schemas.auth import LoginRequest, TokenResponse
from app.schemas.user import UserCreate, UserResponse
from app.services.user_service import (
    EmailAlreadyRegisteredError,
    UserCreationError,
    authenticate_user,
    create_user,
)

router = APIRouter(prefix="/auth", tags=["auth"])

# Single generic message for every login failure mode (unknown email,
# wrong password, or correct credentials with the wrong requested role).
# Deliberately never more specific than this — see authenticate_user().
_GENERIC_LOGIN_ERROR = "Invalid email, password, or role."


@router.post("/signup", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
def signup(user_in: UserCreate, db: Session = Depends(get_db)) -> UserResponse:
    """
    Register a new user (patient or doctor).

    UserCreate already validates and normalizes the request: email is
    lowercased and must be a valid address, password has a minimum length,
    and role must be exactly "patient" or "doctor" — Pydantic rejects
    anything else before this function body ever runs.
    """
    try:
        user = create_user(db, user_in)
    except EmailAlreadyRegisteredError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An account with this email already exists.",
        )
    except UserCreationError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create account. Please try again.",
        )

    return user


@router.post("/login", response_model=TokenResponse)
def login(
    credentials: LoginRequest, response: Response, db: Session = Depends(get_db)
) -> TokenResponse:
    """
    Authenticate a user and start a session.

    credentials.role is only the role requested from the frontend's login
    toggle; authenticate_user() checks it against the user's actual
    database role and fails the login on any mismatch. Every failure mode
    (unknown email, wrong password, wrong role) returns the exact same
    401 + generic message, so the response never reveals which occurred.

    The token is set as an HttpOnly, Secure cookie — this is the real
    session mechanism the frontend relies on; JavaScript cannot read it.
    It's also included in the JSON body so this endpoint stays directly
    testable via curl/pytest/other API clients without a cookie jar; the
    frontend itself never stores this field.
    """
    user = authenticate_user(db, credentials.email, credentials.password, credentials.role)
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=_GENERIC_LOGIN_ERROR,
        )

    # Only enough to identify the authenticated user later — never a
    # password, hashed password, or medical/personal information.
    token = create_access_token({"sub": str(user.id), "role": user.role.value})

    # secure=True works over http://localhost too (browsers treat
    # localhost as a secure context); a real deployment must be served
    # over HTTPS for this cookie to be sent at all. samesite="lax" is
    # sufficient for same-site dev (localhost:5173 <-> localhost:8000);
    # a production deployment on separate domains would need
    # samesite="none" (still with secure=True) instead.
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        httponly=True,
        secure=True,
        samesite="lax",
        max_age=settings.jwt_access_token_expire_minutes * 60,
        path="/",
    )

    return TokenResponse(access_token=token, token_type="bearer")


@router.post("/logout")
def logout(response: Response) -> dict:
    """
    End the current session.

    This is the actual sign-out: it clears the HttpOnly authentication
    cookie server-side. Just navigating away or discarding client state
    would leave a still-valid cookie/token behind, so the frontend must
    call this endpoint rather than treating a route change as "logout".
    """
    response.delete_cookie(key=COOKIE_NAME, path="/")
    return {"message": "Logged out."}


@router.get("/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)) -> UserResponse:
    """
    Return the currently authenticated user.

    Not a dashboard endpoint — just an identity check. Since the JWT lives
    in an HttpOnly cookie the frontend can't read directly, this is how
    the small frontend auth-state helper determines whether a session is
    active and, if so, which role to route to.
    """
    return current_user
