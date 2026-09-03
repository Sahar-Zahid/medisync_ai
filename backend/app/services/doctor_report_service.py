"""
Doctor patient-report view business logic.

This is the single authoritative place for fetching a patient's reports
for a doctor. It enforces:
    - Doctor authentication (via caller)
    - ACTIVE DoctorPatientLink verification
    - Report ownership verification (report belongs to patient)
    - Safe data exposure (no storage paths, no sensitive fields)

The router calls these functions; they never trust client-supplied
doctor IDs or skip authorization checks.
"""
import uuid
from pathlib import Path
from sqlalchemy.orm import Session, joinedload

from app.core.storage import resolve_report_path, StorageError
from app.models.extraction import (
    AbnormalityStatus,
    CandidateExtraction,
    CandidateResult,
    CandidateVerificationStatus,
)
from app.models.relationship import DoctorPatientLink, DoctorPatientLinkStatus
from app.models.report import Report
from app.models.user import User, UserRole
from app.services.relationship_service import doctor_has_active_access


class DoctorReportError(Exception):
    """Base error for doctor report operations. Never carries raw DB
    internals — the router turns this into a generic client-safe error."""
    pass


class PatientNotFoundError(DoctorReportError):
    """Raised when the requested patient doesn't exist."""
    pass


class PatientNotPatientRoleError(DoctorReportError):
    """Raised when the requested user is not a patient."""
    pass


class UnauthorizedAccessError(DoctorReportError):
    """Raised when the doctor doesn't have active access to the patient."""
    pass


class ReportNotFoundError(DoctorReportError):
    """Raised when the requested report doesn't exist or doesn't belong
    to the authorized patient."""
    pass


def verify_patient_exists(db: Session, patient_id: uuid.UUID) -> User:
    """Verify the patient exists and has PATIENT role.

    Raises PatientNotFoundError if user doesn't exist.
    Raises PatientNotPatientRoleError if user is not a patient.
    """
    patient = db.query(User).filter(User.id == patient_id).first()
    if patient is None:
        raise PatientNotFoundError("Patient not found.")
    if patient.role != UserRole.PATIENT:
        raise PatientNotPatientRoleError("User is not a patient.")
    return patient


def verify_doctor_access(
    db: Session,
    doctor_id: uuid.UUID,
    patient_id: uuid.UUID,
) -> None:
    """Verify the doctor has ACTIVE access to the patient.

    Raises UnauthorizedAccessError if no ACTIVE DoctorPatientLink exists.
    """
    if not doctor_has_active_access(db, doctor_id, patient_id):
        raise UnauthorizedAccessError(
            "You do not have access to this patient's records."
        )


def get_patient_reports(
    db: Session,
    doctor_id: uuid.UUID,
    patient_id: uuid.UUID,
) -> dict:
    """
    Return a patient's reports for the authorized doctor.

    Enforces:
        1. Patient exists and has PATIENT role
        2. Doctor has ACTIVE DoctorPatientLink with patient
        3. Reports belong to the patient

    Returns safe metadata only — no storage paths, no sensitive fields.
    """
    # Step 1: Verify patient exists
    patient = verify_patient_exists(db, patient_id)

    # Step 2: Verify doctor has ACTIVE access
    verify_doctor_access(db, doctor_id, patient_id)

    # Step 3: Get patient's reports with extraction data
    reports = (
        db.query(Report)
        .filter(Report.patient_id == patient_id)
        .order_by(Report.created_at.desc())
        .all()
    )

    # Step 4: Build response with extraction data
    report_responses = []
    for report in reports:
        # Get the latest extraction for this report
        extraction = (
            db.query(CandidateExtraction)
            .filter(CandidateExtraction.report_id == report.id)
            .order_by(CandidateExtraction.created_at.desc())
            .first()
        )

        report_responses.append({
            "id": report.id,
            "original_filename": report.original_filename,
            "status": report.status,
            "created_at": report.created_at,
            "extraction": extraction,
            "patient_name_extracted": report.patient_name_extracted,
            "patient_dob_extracted": report.patient_dob_extracted,
            "patient_mrn_extracted": report.patient_mrn_extracted,
            "identity_check_status": report.identity_check_status,
            "identity_confirmed_by_doctor": report.identity_confirmed_by_doctor,
            "identity_confirmed_at": report.identity_confirmed_at,
        })

    return {
        "patient_id": patient.id,
        "patient_name": patient.full_name,
        "reports": report_responses,
    }


def get_pending_triage_results(
    db: Session,
    doctor_id: uuid.UUID,
) -> list[dict]:
    """
    Return every PENDING CandidateResult across the doctor's ACTIVE
    patient roster, for the read-only triage view.

    Reuses the same authorization foundation as the rest of the doctor
    view (an ACTIVE DoctorPatientLink) rather than introducing a second
    authorization system: a patient is only included here if the doctor
    already has ACTIVE access to them (mirrors app.services.
    relationship_service.doctor_has_active_access / get_doctor_roster).

    Filters to verification_status == PENDING only — this view never
    shows VERIFIED/CORRECTED/REJECTED candidates, and it never creates
    or modifies any row (purely a SELECT).

    abnormality_status is read directly from the already-persisted
    CandidateResult column — no new classification logic is run here.

    Returns candidates most-recently-created first, with abnormal
    (HIGH/LOW) results surfaced ahead of NORMAL/UNRESOLVED/NOT_APPLICABLE
    ones so a doctor scanning the list sees the results most likely to
    need attention first. This is a display-ordering convenience only —
    it does not filter anything out.
    """
    active_patient_ids = (
        db.query(DoctorPatientLink.patient_id)
        .filter(
            DoctorPatientLink.doctor_id == doctor_id,
            DoctorPatientLink.status == DoctorPatientLinkStatus.ACTIVE,
        )
        .subquery()
    )

    rows = (
        db.query(CandidateResult, Report, User)
        .join(
            CandidateExtraction,
            CandidateResult.candidate_extraction_id == CandidateExtraction.id,
        )
        .join(Report, CandidateExtraction.report_id == Report.id)
        .join(User, Report.patient_id == User.id)
        .filter(
            Report.patient_id.in_(active_patient_ids),
            CandidateResult.verification_status == CandidateVerificationStatus.PENDING,
        )
        .options(
            joinedload(CandidateResult.canonical_test),
            joinedload(CandidateResult.evidence_record),
        )
        .order_by(CandidateResult.created_at.desc())
        .all()
    )

    abnormal_first = {
        AbnormalityStatus.HIGH: 0,
        AbnormalityStatus.LOW: 0,
        AbnormalityStatus.NORMAL: 1,
        AbnormalityStatus.UNRESOLVED: 2,
        AbnormalityStatus.NOT_APPLICABLE: 2,
    }
    rows.sort(key=lambda row: abnormal_first.get(row[0].abnormality_status, 2))

    return [
        {
            "patient_id": patient.id,
            "patient_name": patient.full_name,
            "report_id": report.id,
            "report_original_filename": report.original_filename,
            "candidate": candidate,
        }
        for candidate, report, patient in rows
    ]


def get_report_pdf_path(
    db: Session,
    doctor_id: uuid.UUID,
    patient_id: uuid.UUID,
    report_id: uuid.UUID,
) -> Path:
    """
    Return the secure filesystem path for a report's original PDF.

    Enforces the same authorization as get_patient_reports:
        1. Patient exists
        2. Doctor has ACTIVE access
        3. Report belongs to the patient

    Returns the resolved Path for streaming to the client.
    Never exposes the path to the client — only used server-side.

    Raises:
        PatientNotFoundError: Patient doesn't exist
        UnauthorizedAccessError: No ACTIVE relationship
        ReportNotFoundError: Report not found or doesn't belong to patient
        DoctorReportError: Storage error
    """
    # Step 1: Verify patient exists
    verify_patient_exists(db, patient_id)

    # Step 2: Verify doctor has ACTIVE access
    verify_doctor_access(db, doctor_id, patient_id)

    # Step 3: Get the report and verify ownership
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

    # Step 4: Resolve the filesystem path (never expose to client)
    try:
        file_path = resolve_report_path(report.storage_path)
    except StorageError as exc:
        raise DoctorReportError(f"Could not access report file: {exc}") from None

    return file_path
