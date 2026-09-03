"""
Patient trusted-results routes.

Read-only: GET /patient/results, GET /patient/results/history, and
GET /patient/results/summary. All three read the trusted TestResult
table the doctor review endpoints (verify/correct/reject) already
populate, and none of them writes anything.

GET /patient/results/history is the same trusted VERIFIED/CORRECTED
data as GET /patient/results, reused as-is for a chronological
history/timeline view — see
app.services.patient_result_service.get_patient_trusted_results_history.

GET /patient/results/summary is a read-only, AI-generated plain-language
summary built ONLY from the patient's own VERIFIED/CORRECTED TestResult
rows — see app.services.patient_summary_service for the full trust-
boundary explanation (UPLOAD -> EXTRACTION -> CANDIDATES -> DOCTOR
VERIFICATION -> TestResult -> AI SUMMARY, never the reverse).
"""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import require_patient
from app.models.user import User
from app.schemas.result import PatientTestResultResponse
from app.schemas.summary import PatientResultSummaryResponse
from app.services.patient_result_service import (
    get_patient_trusted_results,
    get_patient_trusted_results_history,
)
from app.services.patient_summary_service import (
    SummaryGenerationError,
    get_patient_result_summary,
)

router = APIRouter(prefix="/patient/results", tags=["results"])


@router.get("", response_model=list[PatientTestResultResponse])
def get_my_trusted_results(
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> list[PatientTestResultResponse]:
    """
    Return the authenticated patient's own trusted (doctor-reviewed)
    test results.

    require_patient guarantees (via the database role, never a
    client-supplied value) that the caller is authenticated and a
    patient — doctors and unauthenticated requests are rejected before
    this body ever runs. There is no patient ID anywhere in this route:
    ownership is always resolved from current_user.id, so a patient can
    never retrieve another patient's results by guessing an ID, and
    there is no request body for a client to smuggle a patient_id into.

    Only TestResultStatus.VERIFIED/CORRECTED rows are ever returned —
    see get_patient_trusted_results. A patient with no trusted results
    yet simply gets an empty list, not an error.
    """
    return get_patient_trusted_results(db, current_user.id)


@router.get("/history", response_model=list[PatientTestResultResponse])
def get_my_results_history(
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> list[PatientTestResultResponse]:
    """
    Return the authenticated patient's own trusted (VERIFIED/CORRECTED)
    test results as a chronological, read-only history/timeline.

    Reuses the exact same PatientTestResultResponse schema as
    GET /patient/results above — the history view is the same trusted
    data, just consumed chronologically by the frontend, so it exposes
    no new fields and no new internal identifiers.

    require_patient guarantees (via the database role, never a
    client-supplied value) that the caller is authenticated and a
    patient. There is no patient ID anywhere in this route — ownership
    is always resolved from current_user.id, exactly like
    GET /patient/results.

    Ordering is deterministic (newest result_date first, NULLs last,
    then verified_at, then id as a final tiebreaker) — see
    get_patient_trusted_results_history / get_patient_trusted_results.
    A patient with no trusted results yet simply gets an empty list, not
    an error.
    """
    return get_patient_trusted_results_history(db, current_user.id)


@router.get("/summary", response_model=PatientResultSummaryResponse)
def get_my_result_summary(
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> PatientResultSummaryResponse:
    """
    Return a read-only, AI-generated plain-language summary of the
    authenticated patient's own trusted (VERIFIED/CORRECTED) test
    results.

    require_patient guarantees (via the database role, never a
    client-supplied value) that the caller is authenticated and a
    patient. There is no patient ID anywhere in this route — ownership
    is always resolved from current_user.id, exactly like
    GET /patient/results above.

    This endpoint performs no writes: it never creates or updates a
    TestResult, never touches CandidateResult.verification_status, and
    never creates a VerificationHistory row. If the patient has no
    trusted results yet, a deterministic empty-state response is
    returned and Gemini is never called (see
    app.services.patient_summary_service.get_patient_result_summary).

    A 503 is returned — with a generic, client-safe message only — if
    Gemini is unavailable or its response could not be validated; this
    never happens when the patient has no trusted results, since Gemini
    is only ever called once trusted data exists.
    """
    try:
        return get_patient_result_summary(db, current_user.id)
    except SummaryGenerationError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from None
