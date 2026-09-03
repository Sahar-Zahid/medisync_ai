"""
Pydantic schemas for the doctor candidate correction endpoint.

The correction schema accepts structured, validated medical field
corrections. The server re-runs deterministic normalization where
appropriate rather than blindly trusting client-supplied normalized
values.
"""
import uuid
from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class CorrectionRequest(BaseModel):
    """Structured correction for a pending candidate result.

    Only fields that are legitimately correctable are accepted.
    The server re-validates and re-normalizes where deterministic
    services exist. Client-supplied normalized values are NOT
    directly trusted — the server recomputes them from the raw
    corrected values.

    Ownership fields (patient_id, report_id, doctor_id) are never
    accepted — they come from the server-side authorization chain.
    """

    # --- Correctable medical fields ---

    # Corrected test name (raw, as printed on the report).
    test_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Corrected raw test name from the report",
    )

    # Corrected canonical test code (if the doctor knows the correct mapping).
    canonical_test_code: str | None = Field(
        default=None,
        max_length=64,
        description="Corrected canonical test code (e.g. 'HEMOGLOBIN')",
    )

    # Corrected raw value (as should appear on the report).
    value: str | None = Field(
        default=None,
        min_length=1,
        max_length=255,
        description="Corrected raw value from the report",
    )

    # Corrected unit.
    unit: str | None = Field(
        default=None,
        max_length=64,
        description="Corrected unit from the report",
    )

    # Corrected reference range (raw string as printed on the report).
    reference_range: str | None = Field(
        default=None,
        max_length=255,
        description="Corrected reference range string from the report",
    )

    # Corrected result date (raw string as printed on the report).
    result_date: str | None = Field(
        default=None,
        max_length=64,
        description="Corrected result date string from the report",
    )

    # --- Required ---

    # Correction reason — mandatory for audit trail.
    reason: str = Field(
        ...,
        min_length=1,
        max_length=1000,
        description="Required reason for the correction",
    )


class CorrectCandidateResponse(BaseModel):
    """Response after successful candidate correction."""

    message: str
    candidate_id: uuid.UUID
    status: str
    test_result_id: uuid.UUID | None = None
