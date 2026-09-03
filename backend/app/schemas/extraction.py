"""
Pydantic response schemas for candidate lab-result extraction.

Only response shapes exist here — the request has no body at all (see
app/routers/extraction.py): the client supplies which report to process
and nothing else, never report text, a filesystem path, or a prompt.
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
    TestResultStatus,
    UnitNormalizationStatus,
)


class ExtractionEvidenceResponse(BaseModel):
    """Structured provenance record for one CandidateResult's evidence.
    Shows where in the original PDF the evidence text came from, without
    exposing filesystem paths or other internal storage details."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_result_id: uuid.UUID
    extraction_run_id: uuid.UUID
    report_id: uuid.UUID
    source_column: ExtractionSourceField
    page_number: int | None
    # Verified source text from the actual report extraction. NULL when
    # the AI's evidence hint could not be reliably located in the actual
    # report text — evidence is unavailable rather than fabricated.
    source_text: str | None = None
    bounding_box_x: float | None
    bounding_box_y: float | None
    bounding_box_width: float | None
    bounding_box_height: float | None
    created_at: datetime


class CanonicalTestResponse(BaseModel):
    """The backend-curated canonical test identity a CandidateResult was
    deterministically matched to (only present when normalization_status
    is RESOLVED)."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    code: str
    display_name: str


class CandidateResultResponse(BaseModel):
    """A single AI-extracted, pending-verification candidate result.
    Never labeled or implied as confirmed/verified/final — see
    verification_status, which is always "pending" today.

    test_name is always the original, unaltered source name Gemini
    extracted (task rule: never overwritten). canonical_test /
    normalization_status describe a *separate*, deterministic,
    Gemini-free matching outcome — see NormalizationStatus's docstring
    in app.models.extraction for why this is independent of
    verification_status. normalized_value/normalized_unit/
    unit_normalization_status describe a further, equally independent
    deterministic unit-conversion outcome — see
    UnitNormalizationStatus's docstring."""

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
    canonical_test: CanonicalTestResponse | None
    # Additive unit-normalization outcome (see
    # app.services.unit_normalization_service). `value`/`unit` above
    # remain exactly what Gemini extracted — never overwritten, never
    # relabeled as "corrected" or verified. normalized_value/unit are
    # only populated when unit_normalization_status is RESOLVED.
    normalized_value: Decimal | None
    normalized_unit: str | None
    unit_normalization_status: UnitNormalizationStatus
    # Additive result-date normalization outcome (see
    # app.services.date_normalization_service). result_date above
    # remains exactly what Gemini extracted — never overwritten, never
    # inferred when missing. normalized_result_date is only populated
    # when date_normalization_status is RESOLVED.
    normalized_result_date: date | None
    date_normalization_status: DateNormalizationStatus
    # Additive reference-range normalization outcome (see
    # app.services.reference_range_normalization_service).
    # reference_range above remains exactly what Gemini extracted —
    # never overwritten. normalized_reference_lower/upper are only
    # populated when reference_range_normalization_status is RESOLVED.
    normalized_reference_lower: Decimal | None
    normalized_reference_upper: Decimal | None
    reference_range_inclusive_lower: bool | None
    reference_range_inclusive_upper: bool | None
    reference_range_normalization_status: ReferenceRangeNormalizationStatus
    # Deterministic abnormality classification outcome (see
    # app.services.abnormality_classification_service). This is a
    # purely numeric comparison — NOT a diagnosis, NOT medical advice.
    abnormality_status: AbnormalityStatus
    # Structured provenance — where in the original PDF this
    # candidate's evidence text was found. None only when the
    # extraction pipeline did not persist an evidence record
    # (should not occur for new extractions).
    evidence_record: ExtractionEvidenceResponse | None
    created_at: datetime


class CandidateExtractionResponse(BaseModel):
    """One extraction run for a report, and its candidate results
    (empty when status is FAILED, or when Gemini legitimately found
    no lab values). Includes version metadata for auditability."""

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
    results: list[CandidateResultResponse]


class TestResultResponse(BaseModel):
    """A trusted medical test result — the ONLY representation of
    verified clinical data. Created ONLY by a doctor's explicit
    verification action. Never automatically populated by the
    extraction pipeline."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_result_id: uuid.UUID
    extraction_run_id: uuid.UUID
    status: TestResultStatus
    canonical_test: CanonicalTestResponse | None
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
    doctor_id: uuid.UUID | None
    verified_at: datetime | None
    correction_note: str | None
    created_at: datetime
