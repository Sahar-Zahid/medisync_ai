"""
Pydantic schemas for the doctor candidate rejection endpoint.
"""
from pydantic import BaseModel, Field


class RejectionRequest(BaseModel):
    """Structured rejection for a pending candidate result.

    The reason is mandatory for auditability. Ownership fields
    (patient_id, report_id, doctor_id) are never accepted — they
    come from the server-side authorization chain.
    """
    reason: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Required reason for the rejection",
    )


class RejectCandidateResponse(BaseModel):
    """Response after successful candidate rejection."""
    message: str
    candidate_id: str
    status: str
    rejection_reason: str | None = None
