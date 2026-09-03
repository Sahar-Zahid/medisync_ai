"""
Verification history service.

Two responsibilities:

1. `capture_candidate_snapshot` / `create_verification_history` — append
   one immutable history row for a successful VERIFY / CORRECT / REJECT
   action. The helper NEVER commits: the caller's existing transaction
   owns atomicity, so a history row can only persist if the action it
   records also commits.

2. `get_report_verification_history` — authorized doctor-facing read of
   a report's chronological history. Enforces the same authorization
   chain as the doctor report endpoints (patient exists, ACTIVE
   doctor-patient relationship, report belongs to the patient).

Trust boundary:
- doctor_id comes from the authenticated doctor (caller).
- patient_id comes from the authorized patient path (caller).
- report_id / candidate_id come from the actual server-resolved objects.
- action comes from the backend operation, never the client.
- old values are snapshotted from the candidate BEFORE the action mutates
  anything (captured by the caller and passed in).
- new values are the actual final backend-derived values, never
  client-supplied normalized/derived data.
- reason comes from the validated correction/rejection request.
- created_at is a server/database timestamp — never client-supplied.

Immutability: this module exposes no update or delete operation.
"""
import uuid

from sqlalchemy.orm import Session

from app.models.extraction import CandidateResult
from app.models.report import Report
from app.models.verification_history import (
    VerificationAction,
    VerificationHistory,
)
from app.services.doctor_report_service import (
    ReportNotFoundError,
    verify_doctor_access,
    verify_patient_exists,
)

# Snapshot field names, shared by capture_candidate_snapshot and
# create_verification_history.
_SNAPSHOT_FIELDS = [
    "test_name",
    "value",
    "unit",
    "normalized_value",
    "normalized_unit",
    "canonical_test_id",
    "reference_range",
    "result_date",
    "abnormality_status",
]


def capture_candidate_snapshot(candidate: CandidateResult) -> dict:
    """Snapshot the candidate's stored values for audit history.

    Reads only the candidate's source + deterministic normalization
    fields — the same fields a history row records. Returns a plain
    dict so the snapshot is fixed at capture time, before any mutation
    by the action.
    """
    return {
        field: getattr(candidate, field, None) for field in _SNAPSHOT_FIELDS
    }


def create_verification_history(
    db: Session,
    *,
    candidate: CandidateResult,
    report: Report,
    patient_id: uuid.UUID,
    doctor_id: uuid.UUID,
    action: VerificationAction,
    old_snapshot: dict,
    new_snapshot: dict | None = None,
    reason: str | None = None,
) -> VerificationHistory:
    """Build and stage one immutable verification-history row.

    The row is added to the caller's session but NEVER committed here —
    the caller's existing transaction (which also performs the action's
    status change / TestResult creation / reason persistence) controls
    atomicity. If that transaction fails and rolls back, this history
    row is rolled back with it.

    `old_snapshot` must be captured from the candidate BEFORE the action
    mutates anything (see capture_candidate_snapshot). `new_snapshot`,
    when provided, must contain the actual final backend-derived values
    (e.g. corrected raw values + recomputed normalized values for
    CORRECT, or the identical snapshot for VERIFY). For REJECT there is
    no meaningful new state, so new_snapshot stays None and all new_*
    columns are NULL.
    """
    old = old_snapshot or {}
    new = new_snapshot or {}

    history = VerificationHistory(
        candidate_id=candidate.id,
        report_id=report.id,
        patient_id=patient_id,
        doctor_id=doctor_id,
        action=action,
        old_test_name=old.get("test_name"),
        old_value=old.get("value"),
        old_unit=old.get("unit"),
        old_normalized_value=old.get("normalized_value"),
        old_normalized_unit=old.get("normalized_unit"),
        old_canonical_test_id=old.get("canonical_test_id"),
        old_reference_range=old.get("reference_range"),
        old_result_date=old.get("result_date"),
        old_abnormality_status=old.get("abnormality_status"),
        new_test_name=new.get("test_name"),
        new_value=new.get("value"),
        new_unit=new.get("unit"),
        new_normalized_value=new.get("normalized_value"),
        new_normalized_unit=new.get("normalized_unit"),
        new_canonical_test_id=new.get("canonical_test_id"),
        new_reference_range=new.get("reference_range"),
        new_result_date=new.get("result_date"),
        new_abnormality_status=new.get("abnormality_status"),
        reason=reason,
    )
    db.add(history)
    return history


def get_report_verification_history(
    db: Session,
    doctor_id: uuid.UUID,
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
) -> list[VerificationHistory]:
    """Return a patient's report history for an authorized doctor.

    Authorization (same as the other doctor report endpoints):
        1. Patient exists and has PATIENT role
        2. Doctor has an ACTIVE relationship with the patient
        3. Report belongs to the patient

    Raises PatientNotFoundError / PatientNotPatientRoleError /
    UnauthorizedAccessError / ReportNotFoundError, which the router maps
    to client-safe HTTP errors.

    Returns history rows ordered chronologically (oldest first) using the
    server/database created_at timestamp.
    """
    verify_patient_exists(db, patient_id)
    verify_doctor_access(db, doctor_id, patient_id)

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

    return (
        db.query(VerificationHistory)
        .filter(VerificationHistory.report_id == report_id)
        .order_by(VerificationHistory.created_at.asc())
        .all()
    )