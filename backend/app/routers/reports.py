"""
Patient report-upload routes.

Only upload exists so far — no listing, viewing, download, extraction, or
review endpoints yet. Those are separate, later routers.
"""
import hashlib
import os
import uuid

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from fastapi.encoders import jsonable_encoder
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import require_patient
from app.core.pdf_validation import is_valid_pdf
from app.core.storage import StorageError, delete_report_file, save_report_file
from app.models.report import Report
from app.models.user import User
from app.schemas.report import ReportResponse
from app.services.report_service import (
    DuplicateReportError,
    InvalidStatusTransitionError,
    ReportCreationError,
    ReportUpdateError,
    create_report,
    get_report_by_patient_and_hash,
    process_report_text_extraction,
)

router = APIRouter(prefix="/patient/reports", tags=["reports"])

_MAX_UPLOAD_BYTES = settings.max_report_upload_mb * 1024 * 1024

_DUPLICATE_MESSAGE = "This report has already been uploaded."


def _duplicate_response(existing_report) -> HTTPException:
    """Build the 409 response for an exact duplicate. Identifies the
    existing report using only the same safe, client-facing fields
    upload already returns — never a storage path or other internal
    identifier."""
    return HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail={
            "message": _DUPLICATE_MESSAGE,
            "report": jsonable_encoder(ReportResponse.model_validate(existing_report)),
        },
    )


@router.post("", response_model=ReportResponse, status_code=status.HTTP_201_CREATED)
async def upload_report(
    file: UploadFile = File(...),
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> ReportResponse:
    """
    Upload a medical report PDF.

    require_patient guarantees (via the database role, never a
    client-supplied value) that the caller is authenticated and a
    patient — doctors and unauthenticated requests are rejected before
    this body ever runs. Ownership of the resulting Report row always
    comes from current_user.id, never from anything in the request body.

    The uploaded content is validated server-side regardless of the
    browser-reported Content-Type (which is never trusted alone): it must
    look like a well-formed PDF and must not exceed the configured size
    limit. No text extraction or processing happens here — this endpoint
    only stores the original file and records that it was uploaded.

    Duplicate protection: if this same patient has already uploaded a
    file with identical bytes (same SHA-256 hash), no new file is stored
    and no new Report row is created — the existing report is returned
    via a 409 with safe metadata only. The identity used for "duplicate"
    is exactly the file's SHA-256, never filename/size/timestamp, and is
    scoped per patient, so the same PDF uploaded by a different patient
    is unaffected.
    """
    content = await file.read()

    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the {settings.max_report_upload_mb}MB upload limit.",
        )

    if not is_valid_pdf(content):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only valid PDF files are accepted.",
        )

    # Identity for duplicate detection: the actual file bytes, never
    # filename/size/MIME/timestamp.
    sha256_hash = hashlib.sha256(content).hexdigest()

    # Up-front check so an already-uploaded duplicate never gets a new
    # copy written to storage at all. This is a courtesy fast path, not
    # the real safety net — the (patient_id, sha256_hash) unique
    # constraint enforced in create_report() is what actually prevents
    # two duplicate rows under a concurrent-upload race.
    existing = get_report_by_patient_and_hash(db, current_user.id, sha256_hash)
    if existing is not None:
        raise _duplicate_response(existing)

    # Display-only value stored alongside the report — defanged with
    # basename() so a path-like client filename can't smuggle directory
    # components into the database, even though it is never used to build
    # a filesystem path. Falls back to a generic name if the browser sent
    # none, and is truncated to fit the column.
    original_filename = os.path.basename(file.filename or "report.pdf")[:255]

    try:
        storage_path = save_report_file(content)
    except StorageError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save the uploaded file. Please try again.",
        )

    try:
        report = create_report(
            db,
            patient_id=current_user.id,
            original_filename=original_filename,
            storage_path=storage_path,
            sha256_hash=sha256_hash,
        )
    except DuplicateReportError as exc:
        # Lost the race: another upload of the same bytes by the same
        # patient committed first. create_report() already deleted the
        # file we just stored — nothing further to clean up here.
        raise _duplicate_response(exc.existing_report)
    except ReportCreationError:
        # create_report already attempts cleanup internally, but guard
        # here too in case the failure happened before that call.
        delete_report_file(storage_path)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not save the uploaded report. Please try again.",
        )

    return report


@router.post("/{report_id}/process", response_model=ReportResponse)
def process_report(
    report_id: uuid.UUID,
    current_user: User = Depends(require_patient),
    db: Session = Depends(get_db),
) -> ReportResponse:
    """
    Trigger machine-readable PDF text extraction for one of the caller's
    own reports.

    require_patient guarantees the caller is authenticated and a patient
    before this body runs. The report is looked up by (report_id,
    patient_id=current_user.id) together — never by report_id alone — so
    a patient can neither trigger processing on, nor learn anything
    about the existence of, another patient's report; an unmatched ID
    looks identical to a nonexistent one. There is no request body: the
    client supplies nothing but which report to process — it cannot
    submit extracted text, a filesystem path, or a target status, and
    only the server (via process_report_text_extraction) decides the
    outcome.
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

    try:
        report = process_report_text_extraction(db, report)
    except InvalidStatusTransitionError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This report is not ready for processing.",
        )
    except ReportUpdateError:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not update report status. Please try again.",
        )

    return report
