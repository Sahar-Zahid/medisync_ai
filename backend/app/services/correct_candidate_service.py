"""
Doctor candidate correction service.

This is the single authoritative place for correcting a candidate result.
It enforces:
    - Doctor authentication (via caller)
    - ACTIVE DoctorPatientLink verification
    - Report ownership verification
    - Candidate ownership verification
    - Candidate must be PENDING (not already finalized)
    - Structured correction validation
    - Reason validation (non-empty)
    - Deterministic recomputation of all normalized/derived fields
    - Atomic correction: validate -> recompute -> mark CORRECTED -> create TestResult -> commit
    - Race safety: unique constraint on TestResult.candidate_result_id prevents duplicates

The router calls these functions; they never trust client-supplied
doctor IDs or skip authorization checks.
"""
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.models.extraction import (
    AbnormalityStatus,
    CanonicalTest,
    CandidateResult,
    CandidateVerificationStatus,
    CandidateExtraction,
    TestResult,
    TestResultStatus,
)
from app.models.report import Report
from app.models.user import User, UserRole
from app.models.verification_history import VerificationAction
from app.services.abnormality_classification_service import classify_abnormality
from app.services.date_normalization_service import normalize_result_date
from app.services.doctor_report_service import (
    PatientNotFoundError,
    PatientNotPatientRoleError,
    ReportNotFoundError,
    UnauthorizedAccessError,
    verify_doctor_access,
    verify_patient_exists,
)
from app.services.normalization_service import normalize_test_name
from app.services.reference_range_normalization_service import normalize_reference_range
from app.services.unit_normalization_service import normalize_unit
from app.services.verification_history_service import (
    capture_candidate_snapshot,
    create_verification_history,
)


class CorrectError(Exception):
    """Base error for correction operations. Never carries raw DB
    internals — the router turns this into a generic client-safe error."""
    pass


class CandidateNotFoundError(CorrectError):
    """Raised when the requested candidate doesn't exist."""
    pass


class CandidateAlreadyFinalizedError(CorrectError):
    """Raised when the candidate is already in a terminal state."""
    pass


class InvalidCorrectionError(CorrectError):
    """Raised when the correction data is invalid."""
    pass


class CorrectionReasonRequiredError(CorrectError):
    """Raised when the correction reason is missing or empty."""
    pass


class IdentityCheckpointBlockedError(CorrectError):
    """Raised when the report's patient-identity checkpoint blocks
    correction (NOT_CHECKED, MISMATCH hard block, or UNRESOLVED
    without doctor confirmation). Distinct from generic CorrectError so
    the router can map it to a client conflict (409) instead of 500."""
    pass


def _validate_correction_reason(reason: str) -> str:
    """Validate and normalize the correction reason.

    Returns the stripped reason string.
    Raises CorrectionReasonRequiredError if missing, empty, or whitespace-only.
    """
    if not reason or not isinstance(reason, str):
        raise CorrectionReasonRequiredError(
            "A correction reason is required."
        )
    stripped = reason.strip()
    if not stripped:
        raise CorrectionReasonRequiredError(
            "A correction reason is required."
        )
    return stripped


def _validate_numeric_value(value_str: str, field_name: str) -> None:
    """Validate that a string value can be parsed as a number.

    Raises InvalidCorrectionError if the value is not a valid number.
    """
    try:
        Decimal(value_str)
    except (InvalidOperation, ValueError, TypeError):
        raise InvalidCorrectionError(
            f"{field_name} must be a valid numeric value."
        )


def _validate_date_string(date_str: str) -> None:
    """Validate that a date string is a reasonable date.

    Accepts common formats: YYYY-MM-DD, DD/MM/YYYY, MM/DD/YYYY, etc.
    Raises InvalidCorrectionError if obviously invalid.
    """
    if not date_str or not date_str.strip():
        return  # empty is fine, it means "no date"

    import re
    # Basic format check: must contain only digits and separators
    cleaned = date_str.strip()
    if not re.match(r'^[\d/\-\.]+$', cleaned):
        raise InvalidCorrectionError(
            "Result date must be a valid date format (e.g. 2026-06-12)."
        )


def _recompute_normalized_fields(
    db: Session,
    corrected_test_name: str,
    corrected_value: str,
    corrected_unit: str | None,
    corrected_reference_range: str | None,
    corrected_result_date: str | None,
) -> dict:
    """Recompute all normalized/derived fields from corrected raw values.

    Uses the existing deterministic normalization pipeline in the same
    order as the extraction pipeline:

    1. normalize_test_name(db, test_name) → canonical test
    2. normalize_unit(value, unit, canonical_code) → normalized value/unit
    3. normalize_reference_range(raw_range) → reference bounds
    4. normalize_result_date(raw_date) → normalized date
    5. classify_abnormality(norm_value, ref_lower, ref_upper, ...) → abnormality

    Returns a dict with all recomputed fields.
    """
    # Step 1: Normalize test name
    test_result = normalize_test_name(db, corrected_test_name)
    canonical_test_id = None
    canonical_test_code = None
    normalization_status = test_result.status
    if test_result.canonical_test is not None:
        canonical_test_id = test_result.canonical_test.id
        canonical_test_code = test_result.canonical_test.code

    # Step 2: Normalize unit
    unit_result = normalize_unit(corrected_value, corrected_unit, canonical_test_code)
    normalized_value = unit_result.normalized_value
    normalized_unit = unit_result.normalized_unit
    unit_normalization_status = unit_result.status

    # Step 3: Normalize reference range
    range_result = normalize_reference_range(corrected_reference_range)
    reference_range_lower = range_result.normalized_reference_lower
    reference_range_upper = range_result.normalized_reference_upper
    reference_range_inclusive_lower = range_result.inclusive_lower
    reference_range_inclusive_upper = range_result.inclusive_upper
    reference_range_normalization_status = range_result.status

    # Step 4: Normalize result date
    date_result = normalize_result_date(corrected_result_date)
    normalized_result_date = date_result.normalized_date
    date_normalization_status = date_result.status

    # Step 5: Classify abnormality using recomputed normalized values
    abnormality_result = classify_abnormality(
        normalized_value=normalized_value,
        normalized_reference_lower=reference_range_lower,
        normalized_reference_upper=reference_range_upper,
        inclusive_lower=reference_range_inclusive_lower,
        inclusive_upper=reference_range_inclusive_upper,
        normalized_unit=normalized_unit,
        reference_normalized_unit=None,  # ranges don't carry extracted units
    )
    abnormality_status = abnormality_result.status

    return {
        "canonical_test_id": canonical_test_id,
        "normalization_status": normalization_status,
        "normalized_value": normalized_value,
        "normalized_unit": normalized_unit,
        "unit_normalization_status": unit_normalization_status,
        "reference_range_lower": reference_range_lower,
        "reference_range_upper": reference_range_upper,
        "reference_range_inclusive_lower": reference_range_inclusive_lower,
        "reference_range_inclusive_upper": reference_range_inclusive_upper,
        "reference_range_normalization_status": reference_range_normalization_status,
        "normalized_result_date": normalized_result_date,
        "date_normalization_status": date_normalization_status,
        "abnormality_status": abnormality_status,
    }


def correct_candidate(
    db: Session,
    doctor_id: uuid.UUID,
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
    candidate_id: uuid.UUID,
    correction_data: dict,
) -> dict:
    """
    Correct a pending candidate result, creating a trusted TestResult
    with the corrected values.

    This is an atomic operation:
        1. Validate correction reason
        2. Verify patient exists and has PATIENT role
        3. Verify doctor has ACTIVE access to patient
        4. Verify report belongs to patient
        5. Verify candidate belongs to report
        6. Verify candidate is PENDING
        7. Validate correction data
        8. Determine corrected raw values (fall back to originals if omitted)
        9. Recompute all normalized/derived fields deterministically
        10. Mark candidate as CORRECTED
        11. Create trusted TestResult with corrected + recomputed data
        12. Commit atomically

    Race safety:
        - The unique constraint on TestResult.candidate_result_id
          prevents duplicate trusted results
        - If a concurrent request tries to correct the same candidate,
          the second commit will raise IntegrityError and be rolled back
        - The candidate's verification_status update is part of the same
          transaction, so either both succeed or neither does

    The original candidate data is NEVER overwritten. The candidate
    remains fully auditable with its original extracted values.

    All normalized/derived fields in the TestResult are recomputed from
    the corrected raw values using the existing deterministic normalization
    services — never copied from stale candidate data.

    Returns:
        dict with candidate, test_result, and corrected field info

    Raises:
        CorrectionReasonRequiredError: Reason missing or empty
        PatientNotFoundError: Patient doesn't exist
        PatientNotPatientRoleError: User is not a patient
        UnauthorizedAccessError: No ACTIVE relationship
        ReportNotFoundError: Report not found or doesn't belong to patient
        CandidateNotFoundError: Candidate not found or doesn't belong to report
        CandidateAlreadyFinalizedError: Candidate is not PENDING
        InvalidCorrectionError: Correction data is invalid
        CorrectError: Database or correction error
    """
    now = datetime.now(timezone.utc)

    # Step 1: Validate correction reason
    reason = _validate_correction_reason(correction_data.get("reason"))

    # Step 2: Verify patient exists
    verify_patient_exists(db, patient_id)

    # Step 3: Verify doctor has ACTIVE access
    verify_doctor_access(db, doctor_id, patient_id)

    # Step 4: Verify report belongs to patient
    report = (
        db.query(Report)
        .filter(
            Report.id == report_id,
            Report.patient_id == patient_id,
        )
        .first()
    )
    if report is None:
        raise ReportNotFoundError("Report not found.")

    # Step 5: Verify candidate belongs to report
    candidate = (
        db.query(CandidateResult)
        .join(
            CandidateExtraction,
            CandidateResult.candidate_extraction_id == CandidateExtraction.id,
        )
        .filter(
            CandidateResult.id == candidate_id,
            CandidateExtraction.report_id == report_id,
        )
        .options(joinedload(CandidateResult.canonical_test))
        .first()
    )
    if candidate is None:
        raise CandidateNotFoundError("Candidate not found.")

    # Step 6: Verify candidate is PENDING
    if candidate.verification_status != CandidateVerificationStatus.PENDING:
        raise CandidateAlreadyFinalizedError(
            f"Candidate is already {candidate.verification_status.value}. "
            f"Only PENDING candidates can be corrected."
        )

    # Step 6b: Identity checkpoint guard — block if identity requirements
    # are not satisfied. This prevents a report from one patient from
    # silently becoming trusted data under another account.
    from app.services.identity_checkpoint_service import (
        IdentityNotConfirmedError,
        verify_identity_checkpoint_for_trust,
    )
    try:
        verify_identity_checkpoint_for_trust(report)
    except IdentityNotConfirmedError as exc:
        raise IdentityCheckpointBlockedError(str(exc)) from None

    # Step 6c: Capture the original candidate snapshot BEFORE any mutation
    # for the immutable verification history.
    old_snapshot = capture_candidate_snapshot(candidate)

    # Step 7: Validate correction data
    if correction_data.get("value") is not None:
        _validate_numeric_value(correction_data["value"], "Value")
    if correction_data.get("result_date") is not None:
        _validate_date_string(correction_data["result_date"])

    # Step 8: Determine corrected raw values (fall back to originals)
    corrected_test_name = correction_data.get("test_name") or candidate.test_name
    corrected_value = correction_data.get("value") or candidate.value
    corrected_unit = (
        correction_data.get("unit")
        if correction_data.get("unit") is not None
        else candidate.unit
    )
    corrected_reference_range = (
        correction_data.get("reference_range")
        if correction_data.get("reference_range") is not None
        else candidate.reference_range
    )
    corrected_result_date = (
        correction_data.get("result_date")
        if correction_data.get("result_date") is not None
        else candidate.result_date
    )

    # Step 9: Recompute all normalized/derived fields deterministically
    normalized = _recompute_normalized_fields(
        db=db,
        corrected_test_name=corrected_test_name,
        corrected_value=corrected_value,
        corrected_unit=corrected_unit,
        corrected_reference_range=corrected_reference_range,
        corrected_result_date=corrected_result_date,
    )

    # Step 10: Mark candidate as CORRECTED
    candidate.verification_status = CandidateVerificationStatus.CORRECTED

    # Step 11: Create trusted TestResult with corrected raw values
    # and RECOMPUTED normalized/derived values (never stale originals)
    test_result = TestResult(
        candidate_result_id=candidate.id,
        extraction_run_id=candidate.candidate_extraction_id,
        status=TestResultStatus.CORRECTED,
        canonical_test_id=normalized["canonical_test_id"],
        test_name=corrected_test_name,
        raw_value=corrected_value,
        normalized_value=normalized["normalized_value"],
        normalized_unit=normalized["normalized_unit"],
        result_date=normalized["normalized_result_date"],
        reference_range_lower=normalized["reference_range_lower"],
        reference_range_upper=normalized["reference_range_upper"],
        reference_range_inclusive_lower=normalized["reference_range_inclusive_lower"],
        reference_range_inclusive_upper=normalized["reference_range_inclusive_upper"],
        abnormality_status=normalized["abnormality_status"],
        doctor_id=doctor_id,
        verified_at=now,
        correction_note=reason,
    )

    # Step 11b: Append an immutable verification-history record in the
    # SAME transaction as the status change + TestResult creation. The
    # final snapshot reflects the corrected raw values and the RECOMPUTED
    # deterministic normalized/derived values — never stale candidate
    # normalization, never client-supplied derived data. The reason is
    # the validated correction reason. create_verification_history never
    # commits — if this commit fails, the history row rolls back with
    # everything else.
    create_verification_history(
        db,
        candidate=candidate,
        report=report,
        patient_id=patient_id,
        doctor_id=doctor_id,
        action=VerificationAction.CORRECT,
        old_snapshot=old_snapshot,
        new_snapshot={
            "test_name": corrected_test_name,
            "value": corrected_value,
            "unit": corrected_unit,
            "normalized_value": normalized["normalized_value"],
            "normalized_unit": normalized["normalized_unit"],
            "canonical_test_id": normalized["canonical_test_id"],
            "reference_range": corrected_reference_range,
            "result_date": corrected_result_date,
            "abnormality_status": normalized["abnormality_status"],
        },
        reason=reason,
    )

    # Step 12: Commit atomically
    try:
        db.add(test_result)
        db.commit()
        db.refresh(candidate)
        db.refresh(test_result)
    except IntegrityError:
        db.rollback()
        raise CandidateAlreadyFinalizedError(
            "This candidate has already been corrected by another request."
        )
    except SQLAlchemyError:
        db.rollback()
        raise CorrectError("Could not complete correction. Please try again.")

    return {
        "candidate": candidate,
        "test_result": test_result,
        "corrected_test_name": corrected_test_name,
        "corrected_value": corrected_value,
        "corrected_unit": corrected_unit,
    }
