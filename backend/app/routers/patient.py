"""
Patient-scoped routes.

Profile endpoints (GET/PATCH /patient/profile), a read-only doctor
directory (GET /patient/doctors), and a single doctor's details
(GET /patient/doctors/{doctor_id}). Deliberately does not grow into
medical reports or appointments here — those are separate, later
routers.
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_patient
from app.models.user import User
from app.schemas.user import DoctorDirectoryEntry, UserProfileUpdate, UserResponse
from app.services.user_service import (
    DoctorListError,
    UserUpdateError,
    get_doctor_by_id,
    list_doctors,
    update_user_profile,
)

router = APIRouter(prefix="/patient", tags=["patient"])


@router.get("/profile", response_model=UserResponse)
def get_profile(current_user: User = Depends(require_patient)) -> UserResponse:
    """
    Return the authenticated patient's own profile.

    require_patient already guarantees (via the database role, never a
    client-supplied value) that current_user is both authenticated and a
    patient. There is no user ID parameter anywhere in this route — the
    profile returned is always the caller's own.
    """
    return current_user


@router.patch("/profile", response_model=UserResponse)
def update_profile(
    update_in: UserProfileUpdate,
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> UserResponse:
    """
    Update the authenticated patient's own profile.

    Only full_name is accepted (see UserProfileUpdate — id/email/role/
    password fields don't exist on that schema at all, so there's nothing
    for a malicious body to smuggle in). The user to update is always
    current_user, resolved from the authenticated session — never from a
    path or body parameter — so there is no way to edit anyone else's
    profile.
    """
    try:
        updated_user = update_user_profile(db, current_user, update_in)
    except UserUpdateError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not update profile. Please try again.",
        )

    return updated_user


@router.get("/doctors", response_model=list[DoctorDirectoryEntry])
def get_doctors(
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> list[DoctorDirectoryEntry]:
    """
    Return the doctor directory: every user with role=doctor, safe for a
    patient to see.

    require_patient guarantees the caller is an authenticated patient
    (database role, never client-supplied) before this ever runs. The
    filtering to role=doctor happens in list_doctors() at the database
    level, so a patient account can never appear in the response.
    DoctorDirectoryEntry has no email/password/hashed_password fields at
    all, so there's nothing sensitive for the response_model to leak
    even by accident.
    """
    try:
        return list_doctors(db)
    except DoctorListError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load the doctor directory. Please try again.",
        )


@router.get("/doctors/{doctor_id}", response_model=DoctorDirectoryEntry)
def get_doctor(
    doctor_id: str,
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> DoctorDirectoryEntry:
    """
    Return a single doctor's safe public details, for the doctor-details
    view a patient reaches by selecting a doctor from the directory.

    require_patient guarantees the caller is an authenticated patient
    before this runs. get_doctor_by_id() filters to role=doctor at the
    database level and returns None for a malformed UUID, an ID that
    doesn't exist, or an ID that belongs to a patient — all three cases
    are surfaced here as the same 404, so a patient account's existence
    is never confirmed or denied by this endpoint. Reuses
    DoctorDirectoryEntry (the same schema GET /patient/doctors already
    returns) rather than adding a near-duplicate schema, since a single
    doctor's safe fields are identical to one directory entry's fields.
    """
    try:
        doctor = get_doctor_by_id(db, doctor_id)
    except DoctorListError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load this doctor. Please try again.",
        )

    if doctor is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Doctor not found.",
        )

    return doctor
