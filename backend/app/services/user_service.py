"""
User-related business logic (signup for now).

Kept separate from the router so the logic is testable without spinning up
FastAPI, and reusable later (e.g. from a login flow or an admin tool).
"""
import uuid

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.security import hash_password, verify_password
from app.models.user import User, UserRole
from app.schemas.user import UserCreate, UserProfileUpdate


class EmailAlreadyRegisteredError(Exception):
    """Raised when signup is attempted with an email that's already in use."""
    pass


class UserCreationError(Exception):
    """Raised when the user record could not be created for any other
    (non-duplicate-email) database reason. Never carries raw DB internals —
    the router turns this into a generic client-safe error message."""
    pass


class UserUpdateError(Exception):
    """Raised when a profile update could not be persisted for any
    database reason. Never carries raw DB internals — the router turns
    this into a generic client-safe error message."""
    pass


class DoctorListError(Exception):
    """Raised when the doctor directory could not be retrieved for any
    database reason. Never carries raw DB internals — the router turns
    this into a generic client-safe error message."""
    pass


def get_user_by_email(db: Session, email: str) -> User | None:
    """Case-insensitive-safe lookup: callers must pass an already-lowercased
    email (UserCreate.normalize_email handles this for signup input)."""
    return db.query(User).filter(User.email == email).first()


def get_user_by_id(db: Session, user_id: str) -> User | None:
    """Look up a user by primary key, given the string form of their UUID
    (e.g. the "sub" claim decoded from a JWT). Used by get_current_user.
    Returns None — never raises — for a malformed ID, so a tampered or
    garbage token just fails authentication like any other invalid token."""
    try:
        parsed_id = uuid.UUID(str(user_id))
    except (ValueError, AttributeError, TypeError):
        return None
    return db.query(User).filter(User.id == parsed_id).first()


# A precomputed Argon2id hash of a password nobody will ever type, used only
# to give authenticate_user() a verify_password() call to make when the
# email doesn't exist. Without this, a lookup miss returns almost instantly
# while a real password check takes measurably longer — a timing
# side-channel an attacker could use to enumerate registered emails. This
# hash is never compared against anything real.
_DUMMY_PASSWORD_HASH = hash_password("dummy-password-for-timing-parity")


def authenticate_user(
    db: Session, email: str, password: str, role: UserRole
) -> User | None:
    """
    Verify login credentials.

    Returns the User on success. Returns None — with no indication of
    *why* — for every failure mode: unknown email, wrong password, or
    correct email/password but a requested role that doesn't match the
    user's actual database role. Callers (the /auth/login route) must
    turn a None result into one single generic error message, never
    revealing which of those cases occurred.

    `email` is expected already normalized to lowercase (LoginRequest does
    this, matching UserCreate's convention).
    """
    user = get_user_by_email(db, email)

    if user is None:
        # Still do a (deliberately wasted) hash verification so this path
        # takes roughly as long as the "user exists" path below — see
        # _DUMMY_PASSWORD_HASH.
        verify_password(password, _DUMMY_PASSWORD_HASH)
        return None

    if not verify_password(password, user.hashed_password):
        return None

    # The frontend's role selection is only a request. The database role
    # is the sole source of truth — a correct email/password with the
    # wrong requested role must still fail.
    if user.role != role:
        return None

    return user


def create_user(db: Session, user_in: UserCreate) -> User:
    """
    Create a new user record.

    - Email is expected already normalized to lowercase (UserCreate does
      this), so "User@x.com" and "user@x.com" collide as the same account.
    - Role is whatever the validated UserCreate.role is (patient or doctor)
      — the frontend's toggle is just a UI hint; this value, once written
      to the users table, is the source of truth. At this MVP stage both
      roles are created immediately with no extra verification step. If a
      later requirement adds doctor approval/verification, that becomes an
      additional gate checked elsewhere (e.g. a `doctor_verified` flag or
      separate approval workflow) rather than a change to this function's
      shape — signup here only ever asserts "this account was requested
      with this role", not "this doctor is confirmed practicing".
    - Raises EmailAlreadyRegisteredError if the email is taken.
    - Raises UserCreationError (and rolls back) on any other DB failure,
      without leaking DB internals.
    """
    if get_user_by_email(db, user_in.email) is not None:
        raise EmailAlreadyRegisteredError(user_in.email)

    user = User(
        full_name=user_in.full_name,
        email=user_in.email,
        hashed_password=hash_password(user_in.password),
        role=user_in.role,
    )

    try:
        db.add(user)
        db.commit()
        db.refresh(user)
    except IntegrityError:
        # Most likely a duplicate email that slipped past the earlier check
        # due to a race condition — the unique constraint is the real
        # source of truth. Treat it the same as the pre-check case.
        db.rollback()
        raise EmailAlreadyRegisteredError(user_in.email) from None
    except SQLAlchemyError:
        db.rollback()
        raise UserCreationError() from None

    return user


def update_user_profile(db: Session, user: User, update_in: UserProfileUpdate) -> User:
    """
    Update the given (already-authenticated) user's editable profile
    fields in place.

    `user` must be the User row resolved from the authenticated session
    (get_current_user / require_patient) — this function never looks a
    user up by an ID supplied from outside, so there is no way to update
    anyone but the caller's own record.

    Only full_name is touched. id, email, role, hashed_password,
    created_at, and updated_at are never assigned here; updated_at is
    refreshed automatically via the model's onupdate=func.now().

    Raises UserUpdateError (and rolls back) on any DB failure, without
    leaking DB internals.
    """
    user.full_name = update_in.full_name

    try:
        db.commit()
        db.refresh(user)
    except SQLAlchemyError:
        db.rollback()
        raise UserUpdateError() from None

    return user


def list_doctors(db: Session) -> list[User]:
    """
    Return every user with role=doctor, for the patient-facing doctor
    directory (GET /patient/doctors).

    Filters at the database level (never fetches all users and filters
    in Python), so a patient account can never end up in the result.
    Ordered by full_name for a stable, predictable listing in the UI.
    Returns an empty list — never raises — when there are no doctors.

    Raises DoctorListError on any DB failure, without leaking DB
    internals. This is a read, so there is nothing to roll back.
    """
    try:
        return (
            db.query(User)
            .filter(User.role == UserRole.DOCTOR)
            .order_by(User.full_name.asc())
            .all()
        )
    except SQLAlchemyError:
        raise DoctorListError() from None


def get_doctor_by_id(db: Session, doctor_id: str) -> User | None:
    """
    Look up a single doctor by ID, for the patient-facing doctor-details
    view (GET /patient/doctors/{doctor_id}).

    Returns None — never raises — for a malformed UUID (same handling as
    get_user_by_id), for an ID that doesn't exist, and for an ID that
    belongs to a non-doctor user. That last case is deliberate: the
    router must treat "exists but is a patient" identically to "doesn't
    exist" (a 404), rather than confirming that a given account exists.
    The role filter happens in the query itself, at the database level,
    for the same reason list_doctors() filters there — a patient row can
    never make it out of this function.

    Raises DoctorListError on any other DB failure, without leaking DB
    internals — reusing the same error type as list_doctors() since both
    are read failures the router handles the same way.
    """
    try:
        parsed_id = uuid.UUID(str(doctor_id))
    except (ValueError, AttributeError, TypeError):
        return None

    try:
        return (
            db.query(User)
            .filter(User.id == parsed_id, User.role == UserRole.DOCTOR)
            .first()
        )
    except SQLAlchemyError:
        raise DoctorListError() from None
