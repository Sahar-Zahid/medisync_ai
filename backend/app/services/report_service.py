"""
Report-related business logic (upload + exact-duplicate detection).

Kept separate from the router so the logic is testable without spinning up
FastAPI, and reusable later — same pattern as user_service.py.
"""
import uuid

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.core.storage import delete_report_file
from app.models.report import Report, ReportStatus
from app.services.ocr_extraction_service import (
    OcrExtractionError,
    extract_text_via_ocr,
)
from app.services.pdf_extraction_service import (
    PdfExtractionError,
    extract_text_from_report,
)


class ReportCreationError(Exception):
    """Raised when the report record could not be created for any other
    (non-duplicate) database reason. Never carries raw DB internals — the
    router turns this into a generic client-safe error message."""
    pass


class ReportUpdateError(Exception):
    """Raised when an otherwise-valid status transition could not be
    persisted for a database reason. Never carries raw DB internals."""
    pass


class InvalidStatusTransitionError(Exception):
    """Raised when a requested status transition isn't allowed from the
    report's current status (see ALLOWED_STATUS_TRANSITIONS below)."""

    def __init__(self, current_status: ReportStatus, requested_status: ReportStatus):
        self.current_status = current_status
        self.requested_status = requested_status
        super().__init__(
            f"Cannot transition report from {current_status.value} to "
            f"{requested_status.value}."
        )


# The report lifecycle this system currently supports:
#   UPLOADED -> PROCESSING -> COMPLETED
#                          \-> FAILED
# COMPLETED and FAILED are terminal. Nothing in this codebase drives a
# report out of UPLOADED yet — that's for the future processing-pipeline
# feature — this only defines what's *allowed* once something does.
ALLOWED_STATUS_TRANSITIONS: dict[ReportStatus, frozenset[ReportStatus]] = {
    ReportStatus.UPLOADED: frozenset({ReportStatus.PROCESSING}),
    ReportStatus.PROCESSING: frozenset({ReportStatus.COMPLETED, ReportStatus.FAILED}),
    ReportStatus.COMPLETED: frozenset(),
    ReportStatus.FAILED: frozenset(),
}


def transition_report_status(
    db: Session, report: Report, new_status: ReportStatus
) -> Report:
    """
    Move `report` to `new_status` if, and only if, that's a valid
    transition from its current status per ALLOWED_STATUS_TRANSITIONS.

    This is the single place status changes happen — nothing else should
    assign report.status directly. There is deliberately no endpoint
    exposing this to patients (or anyone) yet; it exists so a future
    processing feature has a safe entry point instead of writing
    report.status directly wherever it needs to.

    Raises InvalidStatusTransitionError (without touching the DB) if the
    transition isn't allowed, or ReportUpdateError if the transition was
    valid but couldn't be persisted.
    """
    allowed = ALLOWED_STATUS_TRANSITIONS.get(report.status, frozenset())
    if new_status not in allowed:
        raise InvalidStatusTransitionError(report.status, new_status)

    report.status = new_status
    try:
        db.commit()
        db.refresh(report)
    except SQLAlchemyError:
        db.rollback()
        raise ReportUpdateError() from None

    return report


def process_report_text_extraction(db: Session, report: Report) -> Report:
    """
    Run text extraction for `report`: native machine-readable extraction
    first, falling back to local OCR only if that finds no usable text.

    Drives the report through the existing status machine using
    transition_report_status() as the single source of truth for what
    transitions are allowed — this never assigns report.status directly.
    Only a report currently UPLOADED can be processed: if it isn't,
    transition_report_status() raises InvalidStatusTransitionError before
    anything else happens, so a report can't be processed twice or have
    processing re-triggered once it has already completed or failed.

    Decision logic:
    * Native extraction succeeds (usable machine-readable text found) ->
      `extracted_text` is set, report goes straight to COMPLETED. OCR is
      never invoked in this case — this is the fast, deterministic path.
    * Native extraction fails (unparseable PDF, or parsed but no usable
      text — e.g. a scanned/image-only PDF) -> the OCR fallback runs
      against the same server-stored PDF. If OCR succeeds, `ocr_text` is
      set (and `extracted_text` is left untouched, i.e. still None) and
      the report goes to COMPLETED. If OCR also fails, the report goes
      to FAILED.

    Either extracted_text or ocr_text is set before the COMPLETED
    transition is persisted, so the text and the status are committed
    together.
    """
    transition_report_status(db, report, ReportStatus.PROCESSING)

    try:
        report.extracted_text = extract_text_from_report(report.storage_path)
        return transition_report_status(db, report, ReportStatus.COMPLETED)
    except PdfExtractionError:
        pass

    try:
        report.ocr_text = extract_text_via_ocr(report.storage_path)
    except OcrExtractionError:
        return transition_report_status(db, report, ReportStatus.FAILED)

    return transition_report_status(db, report, ReportStatus.COMPLETED)


class DuplicateReportError(Exception):
    """Raised when the authenticated patient already has a report with
    the same SHA-256 hash — either caught by the application-level check
    or by the database's uniqueness constraint on a race. Carries the
    existing (unchanged) Report so the router can identify it in the
    response using only safe metadata."""

    def __init__(self, existing_report: Report):
        self.existing_report = existing_report
        super().__init__("Duplicate report for this patient.")


def get_report_by_patient_and_hash(
    db: Session, patient_id: uuid.UUID, sha256_hash: str
) -> Report | None:
    """Look up an existing report for this exact patient + file-hash pair.
    Used both as the up-front duplicate check and, on a race, to identify
    the report that won the database's unique constraint."""
    return (
        db.query(Report)
        .filter(Report.patient_id == patient_id, Report.sha256_hash == sha256_hash)
        .first()
    )


def create_report(
    db: Session,
    patient_id: uuid.UUID,
    original_filename: str,
    storage_path: str,
    sha256_hash: str,
) -> Report:
    """
    Persist a Report row for a file that has already been written to
    private storage.

    `patient_id` must come from the authenticated session (see
    app/routers/reports.py) — this function never trusts a caller-supplied
    owner. `storage_path` must be a server-generated identifier from
    app.core.storage.save_report_file, never derived from client input.

    Callers are expected to have already checked
    get_report_by_patient_and_hash() before storing the file at all, so
    this is normally the happy path — but the (patient_id, sha256_hash)
    unique constraint is still the real source of truth: if a concurrent
    upload wins the race between that check and this insert, the
    resulting IntegrityError is caught here, the newly stored duplicate
    file is deleted, and DuplicateReportError is raised carrying the
    report that actually won, rather than leaving an orphaned file or a
    generic 500.

    On any other database failure, the already-stored file is deleted
    (best-effort) and ReportCreationError is raised, so a failed upload
    never leaves an orphaned file with no corresponding record and the
    router never returns a misleading success response.
    """
    report = Report(
        patient_id=patient_id,
        original_filename=original_filename,
        storage_path=storage_path,
        sha256_hash=sha256_hash,
    )

    try:
        db.add(report)
        db.commit()
        db.refresh(report)
    except IntegrityError:
        db.rollback()
        delete_report_file(storage_path)
        existing = get_report_by_patient_and_hash(db, patient_id, sha256_hash)
        if existing is None:
            # Extremely unlikely (e.g. the conflicting row was deleted
            # again immediately after) — fall back to a generic error
            # rather than raising DuplicateReportError with nothing to
            # point at.
            raise ReportCreationError() from None
        raise DuplicateReportError(existing) from None
    except SQLAlchemyError:
        db.rollback()
        delete_report_file(storage_path)
        raise ReportCreationError() from None

    return report
