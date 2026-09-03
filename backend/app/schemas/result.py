"""
Pydantic schemas for the patient trusted-results read path
(GET /patient/results).

Exposes only fields already stored on the trusted `TestResult` model
that are useful for the patient result experience. Deliberately
excludes: candidate_result_id, extraction_run_id (internal linkage),
doctor_id (doctor private information), correction_note (internal
verification detail), raw Gemini output, extraction internals, storage
paths, and API keys — none of those are needed to show a patient their
trusted results and none are returned here.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.extraction import AbnormalityStatus, TestResultStatus


class PatientCanonicalTestResponse(BaseModel):
    """Safe canonical test identity for the patient trusted-results view."""

    model_config = ConfigDict(from_attributes=True)

    code: str
    display_name: str


class PatientTestResultResponse(BaseModel):
    """One trusted, doctor-reviewed test result for the patient view.

    Only ever built from TestResult rows with status VERIFIED or
    CORRECTED (see patient_result_service.get_patient_trusted_results) —
    a PENDING candidate is never represented by this schema, and a
    REJECTED candidate never has a corresponding TestResult row to
    represent in the first place.
    """

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    status: TestResultStatus
    canonical_test: PatientCanonicalTestResponse | None
    test_name: str
    raw_value: str
    normalized_value: Decimal | None
    normalized_unit: str | None
    result_date: date | None
    reference_range_lower: Decimal | None
    reference_range_upper: Decimal | None
    reference_range_inclusive_lower: bool | None
    reference_range_inclusive_upper: bool | None
    abnormality_status: AbnormalityStatus
    verified_at: datetime | None
