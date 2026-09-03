"""
Pydantic schemas for doctor-patient relationship endpoints.

Request/response shapes for the relationship lifecycle. These schemas
never trust client-supplied status values for authorization — the
service layer enforces valid transitions.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.relationship import (
    DoctorPatientLinkStatus,
    LinkInitiatedBy,
)


class RelationshipCreate(BaseModel):
    """Input for creating a new doctor-patient relationship request.

    The target_id is the ID of the other party (doctor if patient is
    requesting, patient if doctor is requesting). The initiator's
    identity comes from the authenticated session, never from the body.
    """
    target_id: uuid.UUID


class RelationshipResponse(BaseModel):
    """Safe representation of a doctor-patient relationship.

    Never exposes sensitive medical data — just the relationship
    metadata needed for the UI.
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    patient_id: uuid.UUID
    doctor_id: uuid.UUID
    status: DoctorPatientLinkStatus
    initiated_by: LinkInitiatedBy
    initiated_at: datetime
    accepted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class RelationshipListResponse(BaseModel):
    """List of relationships."""
    relationships: list[RelationshipResponse]


class RelationshipActionResponse(BaseModel):
    """Response after accepting, declining, or revoking a relationship."""
    message: str
    relationship: RelationshipResponse
