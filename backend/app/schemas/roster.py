"""
Pydantic schemas for doctor roster endpoints.

Safe patient metadata for the doctor's My Patients roster view.
"""
import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.relationship import DoctorPatientLinkStatus, LinkInitiatedBy


class RosterPatientEntry(BaseModel):
    """Safe patient metadata for the doctor's roster.

    Only includes information genuinely needed for the roster view:
    - Patient identity (id, name)
    - Relationship metadata (status, timestamps)

    Never exposes: password, hashed_password, email, JWT tokens,
    storage paths, medical data, or any other sensitive fields.
    """
    model_config = ConfigDict(from_attributes=True)

    patient_id: uuid.UUID
    patient_name: str
    relationship_id: uuid.UUID
    status: DoctorPatientLinkStatus
    initiated_by: LinkInitiatedBy
    initiated_at: datetime
    accepted_at: datetime | None


class RosterResponse(BaseModel):
    """Response containing the doctor's active patient roster."""
    patients: list[RosterPatientEntry]
