"""
Doctor patient-report view routes.

GET  /doctor/patients/{patient_id}/reports - list patient's reports
GET  /doctor/patients/{patient_id}/reports/{report_id}/pdf - download original PDF
POST /doctor/patients/{patient_id}/reports/{report_id}/candidates/{candidate_id}/verify - verify a candidate
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_doctor
from app.models.user import User
from app.schemas.doctor_report import (
    DoctorCandidateResultResponse,
    DoctorPatientReportsResponse,
    DoctorReportResponse,
    DoctorTriageResponse,
    DoctorVerificationHistoryResponse,
)
from app.services.doctor_report_service import (
    DoctorReportError,
    PatientNotFoundError,
    PatientNotPatientRoleError,
    ReportNotFoundError,
    UnauthorizedAccessError,
    get_patient_reports,
    get_pending_triage_results,
    get_report_pdf_path,
)
from app.schemas.correction import CorrectionRequest, CorrectCandidateResponse
from app.services.correct_candidate_service import (
    CandidateAlreadyFinalizedError,
    CandidateNotFoundError as CorrectCandidateNotFoundError,
    CorrectionReasonRequiredError,
    CorrectError,
    InvalidCorrectionError,
    correct_candidate,
)
from app.schemas.rejection import RejectionRequest, RejectCandidateResponse
from app.services.reject_candidate_service import (
    CandidateAlreadyFinalizedError as RejectAlreadyFinalizedError,
    CandidateNotFoundError as RejectCandidateNotFoundError,
    RejectionReasonRequiredError,
    RejectError,
    reject_candidate,
)
from app.services.identity_checkpoint_service import (
    IdentityCheckpointError,
    IdentityCheckNotRunError,
    IdentityMismatchCannotConfirmError,
    confirm_identity_checkpoint,
    run_identity_check,
)
from app.services.verify_candidate_service import (
    CandidateAlreadyVerifiedError,
    CandidateNotFoundError,
    IdentityCheckpointBlockedError,
    VerifyError,
    verify_candidate,
)
from app.services.verification_history_service import (
    get_report_verification_history,
)

router = APIRouter(prefix="/doctor", tags=["doctor-reports"])


class VerifyCandidateResponse(BaseModel):
    """Response after successful candidate verification."""
    message: str
    candidate: DoctorCandidateResultResponse


class CorrectCandidateResponseView(BaseModel):
    """Response after successful candidate correction."""
    message: str
    candidate: DoctorCandidateResultResponse


@router.get(
    "/patients/{patient_id}/reports",
    response_model=DoctorPatientReportsResponse,
)
def list_patient_reports(
    patient_id: str,
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> DoctorPatientReportsResponse:
    """
    Return a patient's reports for the authenticated doctor.

    Authorization flow:
        1. Authenticate (require_doctor)
        2. Verify patient exists
        3. Verify ACTIVE DoctorPatientLink
        4. Return reports belonging to that patient

    The doctor's identity comes from the authenticated session, never
    from client input. The patient_id from the URL is authorization-
    checked server-side against the ACTIVE relationship.
    """
    try:
        parsed_patient_id = uuid.UUID(patient_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )

    try:
        result = get_patient_reports(db, current_user.id, parsed_patient_id)
    except PatientNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )
    except PatientNotPatientRoleError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )
    except UnauthorizedAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except DoctorReportError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load patient reports. Please try again.",
        )

    return DoctorPatientReportsResponse(**result)


@router.get(
    "/triage",
    response_model=DoctorTriageResponse,
)
def get_triage_results(
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> DoctorTriageResponse:
    """
    Read-only PENDING / abnormal-results triage view.

    Returns every CandidateResult with verification_status == PENDING
    across every patient the authenticated doctor currently has ACTIVE
    access to (the same authorization used by GET /doctor/patients and
    GET /doctor/patients/{patient_id}/reports — no separate
    authorization system).

    Purely a SELECT: never changes verification_status, never creates
    a TestResult. abnormality_status is read directly from the
    already-persisted candidate field.
    """
    try:
        entries = get_pending_triage_results(db, current_user.id)
    except DoctorReportError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load triage results. Please try again.",
        )

    return DoctorTriageResponse(results=entries)


@router.get(
    "/patients/{patient_id}/reports/{report_id}/pdf",
)
def download_patient_report_pdf(
    patient_id: str,
    report_id: str,
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> FileResponse:
    """
    Download the original PDF for a patient's report.

    Authorization flow (same as list_patient_reports):
        1. Authenticate (require_doctor)
        2. Verify patient exists
        3. Verify ACTIVE DoctorPatientLink
        4. Verify report belongs to the patient
        5. Stream the PDF file

    The PDF path is resolved server-side and never exposed to the client.
    A doctor cannot access another patient's PDF by guessing a report ID.
    """
    try:
        parsed_patient_id = uuid.UUID(patient_id)
        parsed_report_id = uuid.UUID(report_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found.",
        )

    try:
        file_path = get_report_pdf_path(
            db, current_user.id, parsed_patient_id, parsed_report_id
        )
    except PatientNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )
    except PatientNotPatientRoleError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )
    except UnauthorizedAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except ReportNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found.",
        )
    except DoctorReportError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not access report file. Please try again.",
        )

    return FileResponse(
        path=str(file_path),
        media_type="application/pdf",
        filename="report.pdf",
    )


@router.post(
    "/patients/{patient_id}/reports/{report_id}/candidates/{candidate_id}/correct",
    response_model=CorrectCandidateResponse,
)
def correct_candidate_result(
    patient_id: str,
    report_id: str,
    candidate_id: str,
    body: CorrectionRequest,
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> CorrectCandidateResponse:
    """
    Correct a pending candidate result with structured correction data.

    The doctor supplies corrected medical values and a required reason.
    The server validates the correction and creates a trusted TestResult
    with the corrected values.

    The original candidate data is NEVER overwritten — the candidate
    remains fully auditable with its original extracted values.

    Authorization flow (same as verify):
        1. Authenticate (require_doctor)
        2. Verify patient exists
        3. Verify ACTIVE DoctorPatientLink
        4. Verify report belongs to patient
        5. Verify candidate belongs to report
        6. Verify candidate is PENDING
        7. Validate correction data
        8. Mark candidate as CORRECTED
        9. Create trusted TestResult with corrected values
    """
    try:
        parsed_patient_id = uuid.UUID(patient_id)
        parsed_report_id = uuid.UUID(report_id)
        parsed_candidate_id = uuid.UUID(candidate_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate not found.",
        )

    try:
        result = correct_candidate(
            db=db,
            doctor_id=current_user.id,
            patient_id=parsed_patient_id,
            report_id=parsed_report_id,
            candidate_id=parsed_candidate_id,
            correction_data=body.model_dump(),
        )
    except CorrectionReasonRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except PatientNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )
    except PatientNotPatientRoleError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )
    except UnauthorizedAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except ReportNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found.",
        )
    except (CandidateNotFoundError, CorrectCandidateNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate not found.",
        )
    except CandidateAlreadyFinalizedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except InvalidCorrectionError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except IdentityCheckpointBlockedError as exc:
        # The patient-identity checkpoint was not satisfied (NOT_CHECKED,
        # MISMATCH hard block, or UNRESOLVED without confirmation) — this
        # is a report-state conflict, not a server error.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except CorrectError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not complete correction. Please try again.",
        )

    # Refresh the candidate to get the updated data
    from app.models.extraction import CandidateResult, CandidateExtraction
    from sqlalchemy.orm import joinedload
    updated_candidate = (
        db.query(CandidateResult)
        .join(CandidateExtraction, CandidateResult.candidate_extraction_id == CandidateExtraction.id)
        .filter(
            CandidateResult.id == parsed_candidate_id,
            CandidateExtraction.report_id == parsed_report_id,
        )
        .options(joinedload(CandidateResult.canonical_test))
        .options(joinedload(CandidateResult.evidence_record))
        .first()
    )

    return CorrectCandidateResponse(
        message="Candidate corrected successfully.",
        candidate=updated_candidate,
    )


@router.post(
    "/patients/{patient_id}/reports/{report_id}/candidates/{candidate_id}/reject",
    response_model=RejectCandidateResponse,
)
def reject_candidate_result(
    patient_id: str,
    report_id: str,
    candidate_id: str,
    body: RejectionRequest,
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> RejectCandidateResponse:
    """
    Reject a pending candidate result.

    The doctor provides a required rejection reason. The candidate is
    marked REJECTED and no trusted TestResult is created — rejected
    candidates never become trusted medical data.

    Authorization flow (same as verify/correct):
        1. Authenticate (require_doctor)
        2. Verify patient exists
        3. Verify ACTIVE DoctorPatientLink
        4. Verify report belongs to patient
        5. Verify candidate belongs to report
        6. Verify candidate is PENDING
        7. Mark candidate as REJECTED
    """
    try:
        parsed_patient_id = uuid.UUID(patient_id)
        parsed_report_id = uuid.UUID(report_id)
        parsed_candidate_id = uuid.UUID(candidate_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate not found.",
        )

    try:
        result = reject_candidate(
            db=db,
            doctor_id=current_user.id,
            patient_id=parsed_patient_id,
            report_id=parsed_report_id,
            candidate_id=parsed_candidate_id,
            reason=body.reason,
        )
    except RejectionReasonRequiredError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )
    except PatientNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )
    except PatientNotPatientRoleError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )
    except UnauthorizedAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except ReportNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found.",
        )
    except (CandidateNotFoundError, RejectCandidateNotFoundError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate not found.",
        )
    except RejectAlreadyFinalizedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except RejectError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not complete rejection. Please try again.",
        )

    return RejectCandidateResponse(
        message="Candidate rejected successfully.",
        candidate_id=str(parsed_candidate_id),
        status="rejected",
        rejection_reason=result.get("reason"),
    )


@router.post(
    "/patients/{patient_id}/reports/{report_id}/candidates/{candidate_id}/verify",
    response_model=VerifyCandidateResponse,
)
def verify_candidate_result(
    patient_id: str,
    report_id: str,
    candidate_id: str,
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> VerifyCandidateResponse:
    """
    Verify a pending candidate result.

    This is a read-then-write operation that:
        1. Authenticates the doctor
        2. Verifies ACTIVE DoctorPatientLink with the patient
        3. Verifies the report belongs to the patient
        4. Verifies the candidate belongs to the report
        5. Verifies the candidate is PENDING
        6. Marks the candidate as VERIFIED
        7. Creates a trusted TestResult from the candidate data

    The client does NOT submit medical values — the server copies
    the existing candidate data into the trusted TestResult.

    Atomicity is enforced by:
        - The candidate status update and TestResult creation
          are in the same transaction
        - The unique constraint on TestResult.candidate_result_id
          prevents duplicate trusted results

    Race safety:
        - If two concurrent requests try to verify the same candidate,
          exactly one succeeds and the other receives a conflict error
    """
    try:
        parsed_patient_id = uuid.UUID(patient_id)
        parsed_report_id = uuid.UUID(report_id)
        parsed_candidate_id = uuid.UUID(candidate_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate not found.",
        )

    try:
        result = verify_candidate(
            db=db,
            doctor_id=current_user.id,
            patient_id=parsed_patient_id,
            report_id=parsed_report_id,
            candidate_id=parsed_candidate_id,
        )
    except PatientNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )
    except PatientNotPatientRoleError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )
    except UnauthorizedAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except ReportNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found.",
        )
    except CandidateNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Candidate not found.",
        )
    except CandidateAlreadyVerifiedError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except IdentityCheckpointBlockedError as exc:
        # The patient-identity checkpoint was not satisfied (NOT_CHECKED,
        # MISMATCH hard block, or UNRESOLVED without confirmation) — this
        # is a report-state conflict, not a server error.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except VerifyError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not complete verification. Please try again.",
        )

    return VerifyCandidateResponse(
        message="Candidate verified successfully.",
        candidate=result["candidate"],
    )


@router.get(
    "/patients/{patient_id}/reports/{report_id}/verification-history",
    response_model=DoctorVerificationHistoryResponse,
)
def get_report_verification_history_view(
    patient_id: str,
    report_id: str,
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> DoctorVerificationHistoryResponse:
    """
    Return a report's immutable verification history for the
    authenticated doctor, ordered chronologically.

    Authorization flow (same as list_patient_reports):
        1. Authenticate (require_doctor)
        2. Verify patient exists
        3. Verify ACTIVE DoctorPatientLink
        4. Verify report belongs to the patient
        5. Return only that report's history rows

    The doctor's identity comes from the authenticated session, never
    from client input. patient_id/report_id from the URL are
    authorization-checked server-side — a doctor can never read another
    patient's history. This is a READ-ONLY endpoint: there is no update
    or delete API for verification history.
    """
    try:
        parsed_patient_id = uuid.UUID(patient_id)
        parsed_report_id = uuid.UUID(report_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found.",
        )

    try:
        history = get_report_verification_history(
            db=db,
            doctor_id=current_user.id,
            patient_id=parsed_patient_id,
            report_id=parsed_report_id,
        )
    except PatientNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )
    except PatientNotPatientRoleError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )
    except UnauthorizedAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except ReportNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found.",
        )
    except DoctorReportError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not load verification history. Please try again.",
        )

    return DoctorVerificationHistoryResponse(
        patient_id=parsed_patient_id,
        report_id=parsed_report_id,
        history=history,
    )


class ConfirmIdentityResponse(BaseModel):
    """Response after doctor confirms identity checkpoint."""
    message: str
    identity_check_status: str
    identity_confirmed_by_doctor: bool


@router.post(
    "/patients/{patient_id}/reports/{report_id}/confirm-identity",
    response_model=ConfirmIdentityResponse,
)
def confirm_report_identity(
    patient_id: str,
    report_id: str,
    current_user: User = Depends(require_doctor),
    db: Session = Depends(get_db),
) -> ConfirmIdentityResponse:
    """
    Doctor confirms the identity checkpoint for a report.

    This is an explicit acknowledgment that the doctor has reviewed
    the identity check result and accepts it. Only UNRESOLVED results
    can be confirmed — a deterministic MISMATCH is a hard block that
    confirmation can never override.

    Authorization flow:
        1. Authenticate (require_doctor)
        2. Verify patient exists
        3. Verify ACTIVE DoctorPatientLink
        4. Verify report belongs to patient
        5. Verify identity check has been run
        6. Record doctor confirmation
    """
    try:
        parsed_patient_id = uuid.UUID(patient_id)
        parsed_report_id = uuid.UUID(report_id)
    except (ValueError, AttributeError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found.",
        )

    try:
        report = confirm_identity_checkpoint(
            db=db,
            doctor_id=current_user.id,
            patient_id=parsed_patient_id,
            report_id=parsed_report_id,
        )
    except PatientNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )
    except PatientNotPatientRoleError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Patient not found.",
        )
    except UnauthorizedAccessError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except ReportNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Report not found.",
        )
    except IdentityCheckNotRunError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except IdentityMismatchCannotConfirmError as exc:
        # A deterministic MISMATCH is a hard block — confirmation must
        # never override it, so this is a client conflict, not a server
        # error.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except IdentityCheckpointError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not confirm identity checkpoint. Please try again.",
        )

    return ConfirmIdentityResponse(
        message="Identity checkpoint confirmed.",
        identity_check_status=report.identity_check_status.value,
        identity_confirmed_by_doctor=report.identity_confirmed_by_doctor,
    )
