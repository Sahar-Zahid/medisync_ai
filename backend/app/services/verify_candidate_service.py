"""
Doctor candidate verification service.

This is the single authoritative place for verifying a candidate result.
It enforces:
    - Doctor authentication (via caller)
    - ACTIVE DoctorPatientLink verification
    - Report ownership verification
    - Candidate ownership verification
    - Candidate must be PENDING (not already verified)
    - Atomic verification: check PENDING -> mark VERIFIED -> create TestResult -> commit
    - Race safety: unique constraint on TestResult.candidate_result_id prevents duplicates

The router calls these functions; they never trust client-supplied
doctor IDs or skip authorization checks.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session, joinedload

from app.models.extraction import (
    CandidateResult,
    CandidateVerificationStatus,
    CandidateExtraction,
    TestResult,
    TestResultStatus,
)
from app.models.report import Report
from app.models.user import User, UserRole
from app.models.verification_history import VerificationAction
from app.services.doctor_report_service import (
    PatientNotFoundError,
    PatientNotPatientRoleError,
    ReportNotFoundError,
    UnauthorizedAccessError,
    verify_doctor_access,
    verify_patient_exists,
)
from app.services.verification_history_service import (
    capture_candidate_snapshot,
    create_verification_history,
)


class VerifyError(Exception):
    """Base error for verification operations. Never carries raw DB
    internals — the router turns this into a generic client-safe error."""
    pass


class CandidateNotFoundError(VerifyError):
    """Raised when the requested candidate doesn't exist."""
    pass


class CandidateAlreadyVerifiedError(VerifyError):
    """Raised when the candidate is already in a terminal state."""
    pass


class IdentityCheckpointBlockedError(VerifyError):
    """Raised when the report's patient-identity checkpoint blocks
    verification (NOT_CHECKED, MISMATCH hard block, or UNRESOLVED
    without doctor confirmation). Distinct from generic VerifyError so
    the router can map it to a client conflict (409) instead of 500."""
    pass


def verify_candidate(
    db: Session,
    doctor_id: uuid.UUID,
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
    candidate_id: uuid.UUID,
) -> dict:
    """
    Verify a pending candidate result, creating a trusted TestResult.

    This is an atomic operation:
        1. Verify patient exists and has PATIENT role
        2. Verify doctor has ACTIVE access to patient
        3. Verify report belongs to patient
        4. Verify candidate belongs to report
        5. Verify candidate is PENDING
        6. Mark candidate as VERIFIED
        7. Create trusted TestResult from candidate data
        8. Commit atomically

    Race safety:
        - The unique constraint on TestResult.candidate_result_id
          prevents duplicate trusted results
        - If a concurrent request tries to verify the same candidate,
          the second commit will raise IntegrityError and be rolled back
        - The candidate's verification_status update is part of the same
          transaction, so either both succeed or neither does

    Returns:
        dict with candidate and test_result data

    Raises:
        PatientNotFoundError: Patient doesn't exist
        PatientNotPatientRoleError: User is not a patient
        UnauthorizedAccessError: No ACTIVE relationship
        ReportNotFoundError: Report not found or doesn't belong to patient
        CandidateNotFoundError: Candidate not found or doesn't belong to report
        CandidateAlreadyVerifiedError: Candidate is not PENDING
        VerifyError: Database or verification error
    """
    now = datetime.now(timezone.utc)

    # Step 1: Verify patient exists
    verify_patient_exists(db, patient_id)

    # Step 2: Verify doctor has ACTIVE access
    verify_doctor_access(db, doctor_id, patient_id)

    # Step 3: Verify report belongs to patient
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

    # Step 4: Verify candidate belongs to report
    # Join through CandidateExtraction to verify the report ownership chain
    candidate = (
        db.query(CandidateResult)
        .join(CandidateExtraction, CandidateResult.candidate_extraction_id == CandidateExtraction.id)
        .filter(
            CandidateResult.id == candidate_id,
            CandidateExtraction.report_id == report_id,
        )
        .options(joinedload(CandidateResult.canonical_test))
        .first()
    )
    if candidate is None:
        raise CandidateNotFoundError("Candidate not found.")

    # Step 5: Verify candidate is PENDING
    if candidate.verification_status != CandidateVerificationStatus.PENDING:
        raise CandidateAlreadyVerifiedError(
            f"Candidate is already {candidate.verification_status.value}. "
            f"Only PENDING candidates can be verified."
        )

    # Step 5b: Identity checkpoint guard — block if identity requirements
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

    # Step 5c: Capture the original candidate snapshot BEFORE any mutation
    # for the immutable verification history.
    old_snapshot = capture_candidate_snapshot(candidate)

    # Step 6: Mark candidate as VERIFIED
    candidate.verification_status = CandidateVerificationStatus.VERIFIED

    # Step 7: Create trusted TestResult from candidate data
    # Copy all relevant fields from the candidate to the trusted result
    test_result = TestResult(
        candidate_result_id=candidate.id,
        extraction_run_id=candidate.candidate_extraction_id,
        status=TestResultStatus.VERIFIED,
        canonical_test_id=candidate.canonical_test_id,
        test_name=candidate.test_name,
        raw_value=candidate.value,
        normalized_value=candidate.normalized_value,
        normalized_unit=candidate.normalized_unit,
        result_date=candidate.normalized_result_date,
        reference_range_lower=candidate.normalized_reference_lower,
        reference_range_upper=candidate.normalized_reference_upper,
        reference_range_inclusive_lower=candidate.reference_range_inclusive_lower,
        reference_range_inclusive_upper=candidate.reference_range_inclusive_upper,
        abnormality_status=candidate.abnormality_status,
        doctor_id=doctor_id,
        verified_at=now,
    )

    # Step 7b: Append an immutable verification-history record in the
    # SAME transaction as the status change + TestResult creation. The
    # final snapshot is identical to the original because VERIFY accepts
    # the candidate as-is. create_verification_history never commits —
    # if this commit fails, the history row rolls back with everything
    # else.
    create_verification_history(
        db,
        candidate=candidate,
        report=report,
        patient_id=patient_id,
        doctor_id=doctor_id,
        action=VerificationAction.VERIFY,
        old_snapshot=old_snapshot,
        new_snapshot=old_snapshot,
    )

    # Step 8: Commit atomically
    try:
        db.add(test_result)
        db.commit()
        db.refresh(candidate)
        db.refresh(test_result)
    except IntegrityError:
        # The unique constraint on TestResult.candidate_result_id was violated,
        # meaning another request already verified this candidate
        db.rollback()
        raise CandidateAlreadyVerifiedError(
            "This candidate has already been verified by another request."
        )
    except SQLAlchemyError:
        db.rollback()
        raise VerifyError("Could not complete verification. Please try again.")

    return {
        "candidate": candidate,
        "test_result": test_result,
    }
