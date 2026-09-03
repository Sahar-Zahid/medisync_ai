"""
Patient identity checkpoint orchestration.

This is the single authoritative place for:
1. Running the identity check on a report (extract -> match -> persist)
2. Providing the backend guard that blocks VERIFY/CORRECT when
   identity requirements are not satisfied
3. Handling doctor confirmation of identity checkpoint

The checkpoint ensures a medical report from one patient never
silently becomes trusted data under another patient account.

Flow:
  Report text -> Extract identity -> Match against account -> Persist
  VERIFY/CORRECT -> Check identity checkpoint -> Block or Allow
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.models.report import IdentityCheckStatus, Report
from app.models.user import User, UserRole
from app.services.doctor_report_service import (
    PatientNotFoundError,
    PatientNotPatientRoleError,
    ReportNotFoundError,
    UnauthorizedAccessError,
    verify_doctor_access,
    verify_patient_exists,
)
from app.services.identity_extraction_service import extract_patient_identity
from app.services.identity_matching_service import match_identity


class IdentityCheckpointError(Exception):
    """Base error for identity checkpoint operations."""
    pass


class IdentityNotConfirmedError(IdentityCheckpointError):
    """Raised when VERIFY/CORRECT is attempted but identity checkpoint
    has not been satisfied."""
    pass


class IdentityAlreadyConfirmedError(IdentityCheckpointError):
    """Raised when trying to confirm identity that is already confirmed."""
    pass


class IdentityCheckNotRunError(IdentityCheckpointError):
    """Raised when trying to confirm identity but the check hasn't been
    run yet."""
    pass


class IdentityMismatchCannotConfirmError(IdentityCheckpointError):
    """Raised when a doctor tries to confirm a MISMATCH identity result.

    A deterministic MISMATCH is a HARD BLOCK — an explicitly wrong-patient
    report must never become trusted data through the confirmation
    endpoint, so confirmation is rejected outright."""
    pass


def run_identity_check(db: Session, report: Report) -> Report:
    """
    Run the deterministic identity check for a report.

    This extracts patient identity from the report text and compares
    it against the authenticated patient account. The result is
    persisted on the Report model.

    Must be called with a fully authorized report (patient ownership
    already verified by caller).

    Steps:
    1. Get patient account name from the report's patient_id
    2. Extract identity from report text (extracted_text or ocr_text)
    3. Match extracted identity against account
    4. Persist result on the report
    5. Commit atomically

    Returns the updated report.
    """
    # Step 1: Get patient account
    patient = db.query(User).filter(User.id == report.patient_id).first()
    if patient is None:
        raise PatientNotFoundError("Patient not found.")

    # Step 2: Extract identity from report text
    source_text = report.extracted_text or report.ocr_text
    extracted = extract_patient_identity(source_text or "")

    # Step 3: Match against account
    match_result = match_identity(
        extracted_name=extracted.patient_name,
        extracted_dob=extracted.patient_dob,
        extracted_mrn=extracted.patient_mrn,
        account_name=patient.full_name,
    )

    # Step 4: Persist on report
    report.patient_name_extracted = extracted.patient_name
    report.patient_dob_extracted = extracted.patient_dob
    report.patient_mrn_extracted = extracted.patient_mrn
    report.identity_check_status = match_result.status

    # Step 5: Commit atomically
    try:
        db.commit()
        db.refresh(report)
    except Exception:
        db.rollback()
        raise IdentityCheckpointError("Could not save identity check result.")

    return report


def confirm_identity_checkpoint(
    db: Session,
    doctor_id: uuid.UUID,
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
) -> Report:
    """
    Doctor confirms the identity checkpoint for a report.

    This is an explicit acknowledgment by the doctor that they have
    reviewed the identity checkpoint and accept the result.

    Only allowed when:
    - Identity check has been run (NOT not_checked)
    - Identity result is NOT a deterministic MISMATCH (a MISMATCH is a
      hard block that confirmation can never override)
    - Doctor is authenticated and has ACTIVE relationship
    - Report belongs to the patient

    The doctor_id and timestamp come from the server, never from
    the client.
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

    # Step 4: Verify identity check has been run
    if report.identity_check_status == IdentityCheckStatus.NOT_CHECKED:
        raise IdentityCheckNotRunError(
            "Identity check has not been performed on this report."
        )

    # Step 4b: A deterministic MISMATCH can never be confirmed as a
    # trust override. An explicitly wrong-patient report must never
    # become trusted through the confirmation endpoint.
    if report.identity_check_status == IdentityCheckStatus.MISMATCH:
        raise IdentityMismatchCannotConfirmError(
            "Identity check result is 'mismatch'. A mismatched patient "
            "identity cannot be confirmed as a trust override."
        )

    # Step 5: Confirm (idempotent — already confirmed is fine)
    report.identity_confirmed_by_doctor = True
    report.identity_confirmed_by = doctor_id
    report.identity_confirmed_at = now

    try:
        db.commit()
        db.refresh(report)
    except Exception:
        db.rollback()
        raise IdentityCheckpointError("Could not confirm identity checkpoint.")

    return report


def verify_identity_checkpoint_for_trust(report: Report) -> None:
    """
    Backend guard: verify that a report satisfies identity requirements
    before trusted data can be created.

    Called by verify_candidate and correct_candidate BEFORE creating
    a TestResult.

    Rules:
    - MATCH: allowed (no doctor confirmation needed)
    - MISMATCH: HARD BLOCK — never allowed, even with doctor
      confirmation. An explicitly wrong-patient report must never
      become trusted data.
    - UNRESOLVED: allowed ONLY with explicit doctor confirmation
    - NOT_CHECKED: always blocked
    - None (e.g. ORM-constructed instance without column default applied):
      treated as NOT_CHECKED and blocked

    Raises IdentityNotConfirmedError if requirements are not met.
    """
    status = report.identity_check_status

    # Treat None as NOT_CHECKED — this happens when a Report is
    # constructed via the ORM constructor without the column default
    # being applied (e.g. in tests or raw object construction).
    if status is None or status == IdentityCheckStatus.NOT_CHECKED:
        raise IdentityNotConfirmedError(
            "Identity checkpoint has not been performed on this report. "
            "A trusted TestResult cannot be created until identity is verified."
        )

    if status == IdentityCheckStatus.MATCH:
        # Match — no doctor confirmation needed
        return

    if status == IdentityCheckStatus.MISMATCH:
        # HARD BLOCK — a deterministic mismatch can never become trusted,
        # regardless of any doctor confirmation fields.
        raise IdentityNotConfirmedError(
            "Identity check result is 'mismatch'. This report's patient "
            "identity does not match the account, so a trusted TestResult "
            "cannot be created."
        )

    # UNRESOLVED — requires explicit doctor confirmation
    if not report.identity_confirmed_by_doctor:
        raise IdentityNotConfirmedError(
            f"Identity check result is '{status.value}'. "
            "A doctor must explicitly confirm the identity checkpoint "
            "before a trusted TestResult can be created."
        )
