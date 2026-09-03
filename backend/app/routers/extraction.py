"""
Candidate lab-result extraction route.

Only one endpoint exists here: a patient requesting AI extraction be run
(or reused) for one of their own reports. No listing, no doctor review,
no verification endpoint — those are separate, later features (see task
rules 20/23).
"""
import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_patient
from app.models.extraction import ExtractionRunStatus
from app.models.report import Report
from app.models.user import User
from app.schemas.extraction import CandidateExtractionResponse
from app.services.candidate_extraction_service import (
    ExtractionPersistenceError,
    ReportNotReadyError,
    get_existing_extraction,
    request_candidate_extraction,
)

router = APIRouter(prefix="/patient/reports", tags=["candidate-extraction"])


@router.post(
    "/{report_id}/candidate-extraction",
    response_model=CandidateExtractionResponse,
)
def create_candidate_extraction(
    report_id: uuid.UUID,
    response: Response,
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> CandidateExtractionResponse:
    """
    Request AI candidate lab-result extraction for one of the caller's
    own reports.

    require_patient guarantees the caller is authenticated and a patient
    before this body runs. The report is looked up by (report_id,
    patient_id=current_user.id) together — never by report_id alone — so
    a patient can neither trigger extraction on, nor learn anything
    about the existence of, another patient's report.

    There is no request body: the client supplies nothing but which
    report to process. It cannot submit report text, a filesystem path,
    or a Gemini prompt — the server always reads the report's own
    already-extracted text from the database.

    The result is always a candidate, pending-verification dataset —
    never a verified medical result — regardless of whether this call
    created a new extraction or reused an existing one (200 vs 201 is
    the only observable difference for that).
    """
    report = (
        db.query(Report)
        .filter(Report.id == report_id, Report.patient_id == current_user.id)
        .first()
    )
    if report is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Report not found."
        )

    prior = get_existing_extraction(db, report.id)
    is_reuse = prior is not None and prior.status == ExtractionRunStatus.COMPLETED

    try:
        extraction = request_candidate_extraction(db, report)
    except ReportNotReadyError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This report is not ready for AI extraction yet.",
        )
    except ExtractionPersistenceError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save the extraction result. Please try again.",
        )

    # 200 when an already-completed extraction was reused (no new Gemini
    # call, no new row), 201 when a new attempt (successful or failed)
    # was just created.
    response.status_code = (
        status.HTTP_200_OK if is_reuse else status.HTTP_201_CREATED
    )

    return CandidateExtractionResponse.model_validate(extraction)
