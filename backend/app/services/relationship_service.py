"""
Doctor-Patient relationship business logic.

This is the single authoritative place for all relationship lifecycle
management. It enforces:
    - Role validation (patient_id must be PATIENT, doctor_id must be DOCTOR)
    - No self-linking
    - Valid status transitions
    - Duplicate prevention
    - Authorization checks

The router calls these functions; they never trust client-supplied
status values or user IDs for authorization decisions.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.relationship import (
    DoctorPatientLink,
    DoctorPatientLinkStatus,
    LinkInitiatedBy,
)
from app.models.user import User, UserRole


class RelationshipError(Exception):
    """Base error for relationship operations. Never carries raw DB
    internals — the router turns this into a generic client-safe error."""
    pass


class InvalidRelationshipError(RelationshipError):
    """Raised when the relationship request is invalid (wrong roles,
    self-link, etc.)."""
    pass


class RelationshipNotFoundError(RelationshipError):
    """Raised when the requested relationship doesn't exist."""
    pass


class InvalidTransitionError(RelationshipError):
    """Raised when the requested status transition is not allowed."""
    pass


class UnauthorizedActionError(RelationshipError):
    """Raised when the user is not authorized to perform this action."""
    pass


# --- Valid status transitions ---
# Explicitly defined — no arbitrary client-supplied status changes.
_VALID_TRANSITIONS = {
    DoctorPatientLinkStatus.PENDING: {
        DoctorPatientLinkStatus.ACTIVE,
        DoctorPatientLinkStatus.DECLINED,
    },
    DoctorPatientLinkStatus.ACTIVE: {
        DoctorPatientLinkStatus.REVOKED,
    },
    # DECLINED and REVOKED are terminal — no transitions allowed.
}


def _validate_user_exists_and_has_role(
    db: Session, user_id: uuid.UUID, expected_role: UserRole
) -> User:
    """Look up a user and verify they exist with the expected role.
    Raises InvalidRelationshipError if the user doesn't exist or has
    the wrong role."""
    user = db.query(User).filter(User.id == user_id).first()
    if user is None:
        raise InvalidRelationshipError("User not found.")
    if user.role != expected_role:
        raise InvalidRelationshipError(
            f"User must be a {expected_role.value}, not {user.role.value}."
        )
    return user


def create_relationship_request(
    db: Session,
    initiator_id: uuid.UUID,
    initiator_role: UserRole,
    target_id: uuid.UUID,
) -> DoctorPatientLink:
    """
    Create a new doctor-patient relationship request.

    The initiator can be either the patient or the doctor. The target
    must have the opposite role. The function enforces:
        - Both users exist and have correct roles
        - No self-linking
        - No duplicate active/pending relationships

    Returns the created DoctorPatientLink with status=PENDING.
    """
    now = datetime.now(timezone.utc)

    # Determine patient_id and doctor_id based on who initiated
    if initiator_role == UserRole.PATIENT:
        patient_id = initiator_id
        doctor_id = target_id
        initiated_by = LinkInitiatedBy.PATIENT
    elif initiator_role == UserRole.DOCTOR:
        patient_id = target_id
        doctor_id = initiator_id
        initiated_by = LinkInitiatedBy.DOCTOR
    else:
        raise InvalidRelationshipError("Invalid initiator role.")

    # Prevent self-linking
    if patient_id == doctor_id:
        raise InvalidRelationshipError(
            "A user cannot create a relationship with themselves."
        )

    # Validate both users exist and have correct roles
    _validate_user_exists_and_has_role(db, patient_id, UserRole.PATIENT)
    _validate_user_exists_and_has_role(db, doctor_id, UserRole.DOCTOR)

    # Create the link
    link = DoctorPatientLink(
        patient_id=patient_id,
        doctor_id=doctor_id,
        status=DoctorPatientLinkStatus.PENDING,
        initiated_by=initiated_by,
        initiated_at=now,
    )

    try:
        db.add(link)
        db.commit()
        db.refresh(link)
    except IntegrityError:
        db.rollback()
        # The partial unique index rejected this because an active/pending
        # link already exists between this doctor and patient.
        raise RelationshipError(
            "An active or pending relationship already exists between "
            "this doctor and patient."
        ) from None
    except SQLAlchemyError:
        db.rollback()
        raise RelationshipError() from None

    return link


def get_relationship(db: Session, link_id: uuid.UUID) -> DoctorPatientLink | None:
    """Look up a single relationship by ID. Returns None if not found."""
    return (
        db.query(DoctorPatientLink)
        .filter(DoctorPatientLink.id == link_id)
        .first()
    )


def get_pending_requests_for_doctor(
    db: Session, doctor_id: uuid.UUID
) -> list[DoctorPatientLink]:
    """Return all PENDING requests where the doctor is the recipient."""
    return (
        db.query(DoctorPatientLink)
        .filter(
            DoctorPatientLink.doctor_id == doctor_id,
            DoctorPatientLink.status == DoctorPatientLinkStatus.PENDING,
        )
        .order_by(DoctorPatientLink.initiated_at.desc())
        .all()
    )


def get_active_patients_for_doctor(
    db: Session, doctor_id: uuid.UUID
) -> list[DoctorPatientLink]:
    """Return all ACTIVE relationships for a doctor."""
    return (
        db.query(DoctorPatientLink)
        .filter(
            DoctorPatientLink.doctor_id == doctor_id,
            DoctorPatientLink.status == DoctorPatientLinkStatus.ACTIVE,
        )
        .order_by(DoctorPatientLink.accepted_at.desc())
        .all()
    )


def get_relationships_for_patient(
    db: Session, patient_id: uuid.UUID
) -> list[DoctorPatientLink]:
    """Return all relationships (any status) for a patient."""
    return (
        db.query(DoctorPatientLink)
        .filter(DoctorPatientLink.patient_id == patient_id)
        .order_by(DoctorPatientLink.created_at.desc())
        .all()
    )


def accept_relationship(
    db: Session,
    link_id: uuid.UUID,
    doctor_id: uuid.UUID,
) -> DoctorPatientLink:
    """
    Accept a pending relationship request.

    Only the doctor who received the request can accept it.
    Transitions PENDING -> ACTIVE.
    """
    link = get_relationship(db, link_id)
    if link is None:
        raise RelationshipNotFoundError("Relationship not found.")

    # Only the doctor can accept
    if link.doctor_id != doctor_id:
        raise UnauthorizedActionError(
            "Only the doctor who received this request can accept it."
        )

    # Must be PENDING
    if link.status != DoctorPatientLinkStatus.PENDING:
        raise InvalidTransitionError(
            f"Cannot accept a relationship with status '{link.status.value}'. "
            f"Only PENDING relationships can be accepted."
        )

    now = datetime.now(timezone.utc)
    link.status = DoctorPatientLinkStatus.ACTIVE
    link.accepted_at = now

    try:
        db.commit()
        db.refresh(link)
    except SQLAlchemyError:
        db.rollback()
        raise RelationshipError() from None

    return link


def decline_relationship(
    db: Session,
    link_id: uuid.UUID,
    doctor_id: uuid.UUID,
) -> DoctorPatientLink:
    """
    Decline a pending relationship request.

    Only the doctor who received the request can decline it.
    Transitions PENDING -> DECLINED.
    """
    link = get_relationship(db, link_id)
    if link is None:
        raise RelationshipNotFoundError("Relationship not found.")

    # Only the doctor can decline
    if link.doctor_id != doctor_id:
        raise UnauthorizedActionError(
            "Only the doctor who received this request can decline it."
        )

    # Must be PENDING
    if link.status != DoctorPatientLinkStatus.PENDING:
        raise InvalidTransitionError(
            f"Cannot decline a relationship with status '{link.status.value}'. "
            f"Only PENDING relationships can be declined."
        )

    link.status = DoctorPatientLinkStatus.DECLINED

    try:
        db.commit()
        db.refresh(link)
    except SQLAlchemyError:
        db.rollback()
        raise RelationshipError() from None

    return link


def revoke_relationship(
    db: Session,
    link_id: uuid.UUID,
    patient_id: uuid.UUID,
) -> DoctorPatientLink:
    """
    Revoke an active relationship.

    Only the patient who owns the relationship can revoke it.
    Transitions ACTIVE -> REVOKED.
    """
    link = get_relationship(db, link_id)
    if link is None:
        raise RelationshipNotFoundError("Relationship not found.")

    # Only the patient can revoke
    if link.patient_id != patient_id:
        raise UnauthorizedActionError(
            "Only the patient who owns this relationship can revoke it."
        )

    # Must be ACTIVE
    if link.status != DoctorPatientLinkStatus.ACTIVE:
        raise InvalidTransitionError(
            f"Cannot revoke a relationship with status '{link.status.value}'. "
            f"Only ACTIVE relationships can be revoked."
        )

    link.status = DoctorPatientLinkStatus.REVOKED

    try:
        db.commit()
        db.refresh(link)
    except SQLAlchemyError:
        db.rollback()
        raise RelationshipError() from None

    return link


def doctor_has_active_access(
    db: Session,
    doctor_id: uuid.UUID,
    patient_id: uuid.UUID,
) -> bool:
    """
    Check if a doctor has active access to a patient.

    This is the reusable authorization foundation. It returns True only
    when there exists an ACTIVE DoctorPatientLink between the doctor
    and patient. All future doctor access endpoints must call this.

    Returns False for any non-ACTIVE status, missing link, or invalid
    user IDs.
    """
    link = (
        db.query(DoctorPatientLink)
        .filter(
            DoctorPatientLink.doctor_id == doctor_id,
            DoctorPatientLink.patient_id == patient_id,
            DoctorPatientLink.status == DoctorPatientLinkStatus.ACTIVE,
        )
        .first()
    )
    return link is not None


def get_doctor_roster(
    db: Session,
    doctor_id: uuid.UUID,
) -> list[dict]:
    """
    Return the doctor's active patient roster.

    Queries DoctorPatientLink records WHERE:
        - doctor_id = authenticated doctor
        - status = ACTIVE

    Returns safe patient metadata for the roster view. Never exposes
    sensitive fields. Returns an empty list when the doctor has no
    active patients.
    """
    links = (
        db.query(DoctorPatientLink, User)
        .join(User, DoctorPatientLink.patient_id == User.id)
        .filter(
            DoctorPatientLink.doctor_id == doctor_id,
            DoctorPatientLink.status == DoctorPatientLinkStatus.ACTIVE,
        )
        .order_by(DoctorPatientLink.accepted_at.desc())
        .all()
    )

    return [
        {
            "patient_id": patient.id,
            "patient_name": patient.full_name,
            "relationship_id": link.id,
            "status": link.status,
            "initiated_by": link.initiated_by,
            "initiated_at": link.initiated_at,
            "accepted_at": link.accepted_at,
        }
        for link, patient in links
    ]
