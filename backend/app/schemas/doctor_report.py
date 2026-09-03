"""
Pydantic schemas for doctor patient-report view endpoints.

Safe report and candidate metadata for the doctor's view of a patient's
medical reports. Never exposes storage paths, filesystem locations,
passwords, or other sensitive fields.
"""
import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

from app.models.extraction import (
    AbnormalityStatus,
    CandidateVerificationStatus,
    DateNormalizationStatus,
    ExtractionRunStatus,
    ExtractionSourceField,
    NormalizationStatus,
    ReferenceRangeNormalizationStatus,
    UnitNormalizationStatus,
)
from app.models.report import ReportStatus, IdentityCheckStatus
from app.models.verification_history import VerificationAction


class DoctorCanonicalTestResponse(BaseModel):
    """Safe canonical test identity for doctor review view."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    display_name: str


class DoctorEvidenceResponse(BaseModel):
    """Safe evidence/provenance metadata for doctor review view.

    Shows where in the original PDF the evidence text came from.
    Never exposes filesystem paths or internal storage details.
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    source_column: ExtractionSourceField
    page_number: int | None
    source_text: str | None
    created_at: datetime


class DoctorCandidateResultResponse(BaseModel):
    """Safe candidate result metadata for doctor review.

    Shows the extracted candidate data with all normalization outcomes,
    canonical test identity, and evidence provenance.
    verification_status is always PENDING — this is a view-only feature.
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    test_name: str
    value: str
    unit: str | None
    reference_range: str | None
    specimen: str | None
    result_date: str | None
    evidence: str
    confidence: float | None
    verification_status: CandidateVerificationStatus
    normalization_status: NormalizationStatus
    canonical_test: DoctorCanonicalTestResponse | None
    normalized_value: Decimal | None
    normalized_unit: str | None
    unit_normalization_status: UnitNormalizationStatus
    normalized_result_date: date | None
    date_normalization_status: DateNormalizationStatus
    normalized_reference_lower: Decimal | None
    normalized_reference_upper: Decimal | None
    reference_range_inclusive_lower: bool | None
    reference_range_inclusive_upper: bool | None
    reference_range_normalization_status: ReferenceRangeNormalizationStatus
    abnormality_status: AbnormalityStatus
    rejection_reason: str | None = None
    evidence_record: DoctorEvidenceResponse | None
    created_at: datetime


class DoctorExtractionResponse(BaseModel):
    """Safe extraction run metadata for doctor view."""
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    report_id: uuid.UUID
    status: ExtractionRunStatus
    source_field: ExtractionSourceField
    error_message: str | None
    model_version: str | None
    prompt_version: str | None
    schema_version: str | None
    started_at: datetime
    completed_at: datetime | None
    created_at: datetime
    results: list[DoctorCandidateResultResponse]


class DoctorReportResponse(BaseModel):
    """Safe report metadata for doctor's patient-report view.

    Includes report status, filename, timestamps, and any extraction
    data. Never exposes storage_path, sha256_hash, or patient_id.
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    original_filename: str
    status: ReportStatus
    created_at: datetime
    extraction: DoctorExtractionResponse | None
    # Identity checkpoint fields
    patient_name_extracted: str | None = None
    patient_dob_extracted: str | None = None
    patient_mrn_extracted: str | None = None
    identity_check_status: IdentityCheckStatus = IdentityCheckStatus.NOT_CHECKED
    identity_confirmed_by_doctor: bool = False
    identity_confirmed_at: datetime | None = None


class DoctorPatientReportsResponse(BaseModel):
    """Response containing a patient's reports for doctor view."""
    patient_id: uuid.UUID
    patient_name: str
    reports: list[DoctorReportResponse]


class DoctorTriageEntryResponse(BaseModel):
    """One PENDING candidate result surfaced in the doctor's triage
    view, with the minimum patient/report context needed to identify
    it, plus the same safe candidate fields as the review workspace
    (DoctorCandidateResultResponse). Read-only: verification_status is
    always PENDING here (see get_pending_triage_results)."""
    model_config = ConfigDict(from_attributes=True)

    patient_id: uuid.UUID
    patient_name: str
    report_id: uuid.UUID
    report_original_filename: str
    candidate: DoctorCandidateResultResponse


class DoctorTriageResponse(BaseModel):
    """Response containing every PENDING candidate result across the
    doctor's ACTIVE patient roster, for the read-only triage view."""
    results: list[DoctorTriageEntryResponse]


class VerificationHistoryItem(BaseModel):
    """One immutable verification-history record, safe for doctor view.

    Read-only audit data — there is no update or delete API. The action,
    ownership ids, and timestamps are all server-recorded; the frontend
    can display but never modify them. No AI-generated diagnoses or
    medical advice are ever stored or exposed here.
    """
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID
    doctor_id: uuid.UUID
    action: VerificationAction
    # Original candidate snapshot (before the action).
    old_test_name: str | None
    old_value: str | None
    old_unit: str | None
    old_normalized_value: Decimal | None
    old_normalized_unit: str | None
    old_reference_range: str | None
    old_result_date: str | None
    old_abnormality_status: AbnormalityStatus | None
    # Final backend-derived snapshot where applicable (NULL for REJECT).
    new_test_name: str | None
    new_value: str | None
    new_unit: str | None
    new_normalized_value: Decimal | None
    new_normalized_unit: str | None
    new_reference_range: str | None
    new_result_date: str | None
    new_abnormality_status: AbnormalityStatus | None
    reason: str | None
    created_at: datetime


class DoctorVerificationHistoryResponse(BaseModel):
    """A report's chronological verification history for an authorized
    doctor. Oldest first, using the server/database timestamps."""
    patient_id: uuid.UUID
    report_id: uuid.UUID
    history: list[VerificationHistoryItem]
