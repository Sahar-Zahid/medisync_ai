"""
Doctor-patient relationship routes.

Patient endpoints:
    POST /patient/relationships - request a doctor relationship
    GET /patient/relationships - list own relationships
    DELETE /patient/relationships/{link_id} - revoke an active relationship

Doctor endpoints:
    GET /doctor/relationships/pending - list incoming pending requests
    GET /doctor/relationships/active - list active patient relationships
    POST /doctor/relationships/{link_id}/accept - accept a pending request
    POST /doctor/relationships/{link_id}/decline - decline a pending request
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_doctor, require_patient
from app.models.user import User
from app.schemas.relationship import (
    RelationshipActionResponse,
    RelationshipCreate,
    RelationshipListResponse,
    RelationshipResponse,
)
from app.schemas.roster import RosterPatientEntry, RosterResponse
from app.services.relationship_service import (
    InvalidRelationshipError,
    InvalidTransitionError,
    RelationshipError,
    RelationshipNotFoundError,
    UnauthorizedActionError,
    accept_relationship,
    create_relationship_request,
    decline_relationship,
    get_active_patients_for_doctor,
    get_doctor_roster,
    get_pending_requests_for_doctor,
    get_relationships_for_patient,
    revoke_relationship,
)

# --- Patient router ---
patient_router = APIRouter(prefix="/patient", tags=["patient-relationships"])


@patient_router.post(
    "/relationships",
    response_model=RelationshipResponse,
    status_code=status.HTTP_201_CREATED,
)
def request_doctor_relationship(
    body: RelationshipCreate,
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> RelationshipResponse:
    """
    Request a relationship with a doctor.

    The authenticated patient initiates the request. The target_id must
    be a valid doctor. The service layer enforces:
        - Both users exist and have correct roles
        - No self-linking
        - No duplicate active/pending relationships
    """
    try:
        link = create_relationship_request(
            db=db,
            initiator_id=current_user.id,
            initiator_role=current_user.role,
            target_id=body.target_id,
        )
    except InvalidRelationshipError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except RelationshipError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create relationship. Please try again.",
        )

    return link


@patient_router.get(
    "/relationships",
    response_model=RelationshipListResponse,
)
def list_patient_relationships(
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> RelationshipListResponse:
    """
    List all relationships for the authenticated patient.

    Shows relationships in any status (PENDING, ACTIVE, DECLINED, REVOKED)
    so the patient can see their full relationship history.
    """
    try:
        relationships = get_relationships_for_patient(db, current_user.id)
    except RelationshipError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load relationships. Please try again.",
        )

    return RelationshipListResponse(relationships=relationships)


@patient_router.delete(
    "/relationships/{link_id}",
    response_model=RelationshipActionResponse,
)
def revoke_doctor_relationship(
    link_id: str,
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> RelationshipActionResponse:
    """
    Revoke an active relationship with a doctor.

    Only the patient who owns the relationship can revoke it.
    Transitions ACTIVE -> REVOKED.
    """
    try:
        parsed_id = __import__("uuid").UUID(link_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Relationship not found.",
        )

    try:
        link = revoke_relationship(db, parsed_id, current_user.id)
    except RelationshipNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Relationship not found.",
        )
    except UnauthorizedActionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except RelationshipError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not revoke relationship. Please try again.",
        )

    return RelationshipActionResponse(
        message="Relationship revoked.",
        relationship=link,
    )


# --- Doctor router ---
doctor_router = APIRouter(prefix="/doctor", tags=["doctor-relationships"])


@doctor_router.get(
    "/relationships/pending",
    response_model=RelationshipListResponse,
)
def list_pending_requests(
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> RelationshipListResponse:
    """
    List incoming pending relationship requests for the doctor.

    Shows requests where the doctor is the recipient and the status
    is PENDING.
    """
    try:
        relationships = get_pending_requests_for_doctor(db, current_user.id)
    except RelationshipError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load pending requests. Please try again.",
        )

    return RelationshipListResponse(relationships=relationships)


@doctor_router.get(
    "/relationships/active",
    response_model=RelationshipListResponse,
)
def list_active_patients(
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> RelationshipListResponse:
    """
    List active patient relationships for the doctor.

    Shows only ACTIVE relationships — the patients this doctor is
    currently authorized to access.
    """
    try:
        relationships = get_active_patients_for_doctor(db, current_user.id)
    except RelationshipError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load active patients. Please try again.",
        )

    return RelationshipListResponse(relationships=relationships)


@doctor_router.post(
    "/relationships/{link_id}/accept",
    response_model=RelationshipActionResponse,
)
def accept_relationship_request(
    link_id: str,
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> RelationshipActionResponse:
    """
    Accept a pending relationship request.

    Only the doctor who received the request can accept it.
    Transitions PENDING -> ACTIVE.
    """
    try:
        parsed_id = __import__("uuid").UUID(link_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Relationship not found.",
        )

    try:
        link = accept_relationship(db, parsed_id, current_user.id)
    except RelationshipNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Relationship not found.",
        )
    except UnauthorizedActionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except RelationshipError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not accept relationship. Please try again.",
        )

    return RelationshipActionResponse(
        message="Relationship accepted.",
        relationship=link,
    )


@doctor_router.post(
    "/relationships/{link_id}/decline",
    response_model=RelationshipActionResponse,
)
def decline_relationship_request(
    link_id: str,
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> RelationshipActionResponse:
    """
    Decline a pending relationship request.

    Only the doctor who received the request can decline it.
    Transitions PENDING -> DECLINED.
    """
    try:
        parsed_id = __import__("uuid").UUID(link_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Relationship not found.",
        )

    try:
        link = decline_relationship(db, parsed_id, current_user.id)
    except RelationshipNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Relationship not found.",
        )
    except UnauthorizedActionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except InvalidTransitionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except RelationshipError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not decline relationship. Please try again.",
        )

    return RelationshipActionResponse(
        message="Relationship declined.",
        relationship=link,
    )


@doctor_router.get(
    "/patients",
    response_model=RosterResponse,
)
def get_my_patients(
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> RosterResponse:
    """
    Return the doctor's My Patients roster.

    Returns ONLY patients connected through ACTIVE DoctorPatientLinks.
    The doctor's identity comes from the authenticated session, never
    from client input. Returns an empty list when no active patients.
    """
    try:
        roster_entries = get_doctor_roster(db, current_user.id)
    except RelationshipError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load patient roster. Please try again.",
        )

    return RosterResponse(
        patients=[RosterPatientEntry(**entry) for entry in roster_entries]
    )
