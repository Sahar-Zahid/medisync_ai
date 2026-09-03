"""
Doctor candidate rejection service.

This is the single authoritative place for rejecting a candidate result.
It enforces:
    - Doctor authentication (via caller)
    - ACTIVE DoctorPatientLink verification
    - Report ownership verification
    - Candidate ownership verification
    - Candidate must be PENDING (not already finalized)
    - Rejection reason validation (non-empty)
    - Atomic rejection: validate -> mark REJECTED -> commit
    - Race safety: atomic transaction prevents inconsistent state

Unlike VERIFY and CORRECT, REJECT does NOT create a trusted TestResult.
Rejected candidates never become trusted medical data.

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


class RejectError(Exception):
    """Base error for rejection operations. Never carries raw DB
    internals — the router turns this into a generic client-safe error."""
    pass


class CandidateNotFoundError(RejectError):
    """Raised when the requested candidate doesn't exist."""
    pass


class CandidateAlreadyFinalizedError(RejectError):
    """Raised when the candidate is already in a terminal state."""
    pass


class RejectionReasonRequiredError(RejectError):
    """Raised when the rejection reason is missing or empty."""
    pass


def _validate_rejection_reason(reason: str) -> str:
    """Validate and normalize the rejection reason.

    Returns the stripped reason string.
    Raises RejectionReasonRequiredError if missing, empty, or whitespace-only.
    """
    if not reason or not isinstance(reason, str):
        raise RejectionReasonRequiredError(
            "A rejection reason is required."
        )
    stripped = reason.strip()
    if not stripped:
        raise RejectionReasonRequiredError(
            "A rejection reason is required."
        )
    return stripped


def reject_candidate(
    db: Session,
    doctor_id: uuid.UUID,
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
    candidate_id: uuid.UUID,
    reason: str,
) -> dict:
    """
    Reject a pending candidate result.

    This is an atomic operation:
        1. Validate rejection reason
        2. Verify patient exists and has PATIENT role
        3. Verify doctor has ACTIVE access to patient
        4. Verify report belongs to patient
        5. Verify candidate belongs to report
        6. Verify candidate is PENDING
        7. Mark candidate as REJECTED
        8. Commit atomically

    Unlike VERIFY and CORRECT, no TestResult is created.
    Rejected candidates never become trusted medical data.

    Race safety:
        - The candidate's verification_status update is atomic
        - Two concurrent reject requests both succeed with the same
          outcome (idempotent state change) — no duplicate rows created

    The original candidate data (values, evidence, normalization) is
    NEVER overwritten. Only verification_status changes.

    Returns:
        dict with candidate and rejection metadata

    Raises:
        RejectionReasonRequiredError: Reason missing or empty
        PatientNotFoundError: Patient doesn't exist
        PatientNotPatientRoleError: User is not a patient
        UnauthorizedAccessError: No ACTIVE relationship
        ReportNotFoundError: Report not found or doesn't belong to patient
        CandidateNotFoundError: Candidate not found or doesn't belong to report
        CandidateAlreadyFinalizedError: Candidate is not PENDING
        RejectError: Database or rejection error
    """
    now = datetime.now(timezone.utc)

    # Step 1: Validate rejection reason
    validated_reason = _validate_rejection_reason(reason)

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
        .options(joinedload(CandidateResult.evidence_record))
        .first()
    )
    if candidate is None:
        raise CandidateNotFoundError("Candidate not found.")

    # Step 6: Verify candidate is PENDING
    if candidate.verification_status != CandidateVerificationStatus.PENDING:
        raise CandidateAlreadyFinalizedError(
            f"Candidate is already {candidate.verification_status.value}. "
            f"Only PENDING candidates can be rejected."
        )

    # Step 6b: Capture the original candidate snapshot BEFORE any mutation
    # for the immutable verification history. Rejection never alters the
    # candidate's extracted values — the history preserves them as-is.
    old_snapshot = capture_candidate_snapshot(candidate)

    # Step 7: Mark candidate as REJECTED and persist the reason.
    # Both the status change and the reason are set in the same
    # transaction — either both persist or neither does.
    candidate.verification_status = CandidateVerificationStatus.REJECTED
    candidate.rejection_reason = validated_reason

    # Step 7b: Append an immutable verification-history record in the
    # SAME transaction as the status change + reason persistence. The
    # reason is the validated rejection reason. There is NO new snapshot
    # — a rejected candidate has no trusted/final state, and no TestResult
    # is created. create_verification_history never commits — if this
    # commit fails, the history row rolls back with everything else.
    create_verification_history(
        db,
        candidate=candidate,
        report=report,
        patient_id=patient_id,
        doctor_id=doctor_id,
        action=VerificationAction.REJECT,
        old_snapshot=old_snapshot,
        reason=validated_reason,
    )

    # Step 8: Commit atomically
    # No TestResult is created — rejected candidates never become trusted.
    try:
        db.commit()
        db.refresh(candidate)
    except SQLAlchemyError:
        db.rollback()
        raise RejectError("Could not complete rejection. Please try again.")

    return {
        "candidate": candidate,
        "doctor_id": doctor_id,
        "reason": validated_reason,
        "rejected_at": now,
    }
