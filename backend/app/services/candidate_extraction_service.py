"""Candidate lab-result extraction — orchestration and persistence.

This is the service the router calls. It owns:
* choosing which report text field to send to Gemini (native extraction
  preferred, OCR fallback used only when native text isn't available —
  the same priority the existing ingestion pipeline itself uses)
* idempotency (never silently duplicating a successful extraction)
* calling app.services.gemini_extraction_service (the only module that
  talks to Gemini)
* validating and persisting the result, including attaching each
  CandidateResult's deterministic normalization outcomes — test-name
  (see app.services.normalization_service), unit (see
  app.services.unit_normalization_service, run second, after test-name
  normalization), date (see app.services.date_normalization_service,
  run third), reference-range (see
  app.services.reference_range_normalization_service, run fourth),
  and abnormality (see
  app.services.abnormality_classification_service, run fifth/last) —
  all Gemini-free, called only after Gemini's own output has already
  been validated and is about to be persisted

Extraction run versioning: each extraction run records model_version,
prompt_version, and schema_version for auditability. Lifecycle
timestamps (started_at, completed_at) track when the run began and
when it reached a terminal state.

NO DIRECT MEDICAL TRUST ESCALATION: every CandidateResult this creates is
persisted with verification_status=PENDING (the only value
CandidateVerificationStatus currently defines) and nothing in this module
can mark one verified. Doctor verification is a separate future feature.
No TestResult is ever created by this module — trusted results are
created ONLY by the future doctor-review workflow.
"""
import uuid
from datetime import datetime, timezone

from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import Session

from app.models.extraction import (
    CandidateExtraction,
    CandidateResult,
    ExtractionEvidence,
    ExtractionRunStatus,
    ExtractionSourceField,
)
from app.models.report import Report, ReportStatus
from app.services.gemini_extraction_service import (
    GeminiExtractionError,
    extract_candidates_from_text,
)
from app.services.date_normalization_service import normalize_result_date
from app.services.normalization_service import normalize_test_name
from app.services.unit_normalization_service import normalize_unit
from app.services.reference_range_normalization_service import (
    normalize_reference_range,
)
from app.services.abnormality_classification_service import (
    classify_abnormality,
)
from app.services.evidence_matching_service import match_evidence_to_source


# --- Extraction run version tracking ---
# These versions record which extraction configuration produced
# the candidates in this run, enabling auditability and
# reproducibility. Updated when the extraction pipeline changes.
MODEL_VERSION = "1.0.0"
PROMPT_VERSION = "1.0.0"
SCHEMA_VERSION = "1.0.0"


class ReportNotReadyError(Exception):
    """Raised when the report hasn't completed the existing text-
    extraction pipeline yet (report.status != COMPLETED, or neither
    extracted_text nor ocr_text is actually populated) — there is no
    server-sourced text to send to Gemini yet."""
    pass


class ExtractionPersistenceError(Exception):
    """Raised when a database failure prevents the extraction result
    (success or failure) from being saved. Never carries raw DB
    internals — the router turns this into a generic client-safe
    error."""
    pass


def _select_source_text(report: Report) -> tuple[str, ExtractionSourceField]:
    """Native extracted_text is preferred when it contains usable text;
    ocr_text is used only when extracted_text doesn't. Never any other
    source. Raises ReportNotReadyError if neither is populated."""
    if report.extracted_text and report.extracted_text.strip():
        return report.extracted_text, ExtractionSourceField.EXTRACTED_TEXT
    if report.ocr_text and report.ocr_text.strip():
        return report.ocr_text, ExtractionSourceField.OCR_TEXT
    raise ReportNotReadyError(
        "This report has no extracted text available for AI processing."
    )


def get_existing_extraction(db: Session, report_id: uuid.UUID) -> CandidateExtraction | None:
    """Most recent extraction attempt for this report, if any (COMPLETED
    or FAILED)."""
    return (
        db.query(CandidateExtraction)
        .filter(CandidateExtraction.report_id == report_id)
        .order_by(CandidateExtraction.created_at.desc())
        .first()
    )


def request_candidate_extraction(db: Session, report: Report) -> CandidateExtraction:
    """
    Run (or reuse) candidate lab-result extraction for `report`.

    Idempotency (task rule 19): if this report already has a COMPLETED
    extraction, it is returned unchanged — Gemini is never called again
    and no duplicate candidate set is created. If the most recent attempt
    FAILED, a new attempt is made (a report that never successfully
    extracted has nothing to "duplicate"). If a new attempt is made, it
    is always persisted as a new row rather than mutating the failed one,
    so failed attempts remain in the history rather than disappearing.

    Ownership/authorization are the caller's responsibility (the router
    resolves `report` by (report_id, patient_id) together) — this
    function only requires a Report object, never a raw ID plus a
    separately-trusted owner.

    Raises ReportNotReadyError if the report has no extracted text yet,
    or ExtractionPersistenceError if a database failure prevents saving
    the outcome. Never raises for a Gemini failure — that outcome is
    represented as a returned CandidateExtraction with
    status=ExtractionRunStatus.FAILED instead, per task rule 11 ("do not
    create fake results... use a safe failure behavior").
    """
    existing = get_existing_extraction(db, report.id)
    if existing is not None and existing.status == ExtractionRunStatus.COMPLETED:
        return existing

    source_text, source_field = _select_source_text(report)

    try:
        candidates = extract_candidates_from_text(source_text)
    except GeminiExtractionError as exc:
        return _persist_failed_extraction(db, report.id, source_field, str(exc))

    result = _persist_completed_extraction(
        db, report.id, source_field, source_text, candidates
    )

    # Run identity checkpoint after successful extraction.
    # The identity check is a read-only deterministic operation on the
    # existing report text — it does not modify the report text or
    # candidates. It persists identity metadata on the report so that
    # VERIFY/CORRECT can enforce the identity guard.
    try:
        from app.services.identity_checkpoint_service import run_identity_check
        run_identity_check(db, report)
    except Exception:
        # Identity check failure must not block extraction.
        # The identity status will remain NOT_CHECKED, which means
        # VERIFY/CORRECT will require the identity check to be run first.
        pass

    return result


def _normalization_fields(
    db: Session,
    source_test_name: str,
    value: str,
    unit: str | None,
    result_date: str | None,
    reference_range: str | None,
) -> dict:
    """CandidateResult kwargs for the deterministic normalization
    columns, derived by calling the normalization services in order:

    1. Test-name normalization (app.services.normalization_service)
    2. Unit normalization (app.services.unit_normalization_service)
    3. Date normalization (app.services.date_normalization_service)
    4. Reference-range normalization
       (app.services.reference_range_normalization_service)
    5. Abnormality classification
       (app.services.abnormality_classification_service)

    Unit normalization deliberately runs after, and is given, the
    test-name normalization outcome (task rule 11 processing order): a
    test-specific conversion rule may only apply once the candidate's
    canonical test identity is itself RESOLVED (task rule 8), so an
    UNRESOLVED/AMBIGUOUS test-name result is passed through as no
    canonical test code at all rather than guessed at.

    Reference-range normalization is entirely independent of the first
    three — it takes only the raw reference_range string. Abnormality
    classification runs last and depends on the normalized value/unit
    (from step 2) and the normalized reference range (from step 4).
    """
    name_result = normalize_test_name(db, source_test_name)
    canonical_test_code = (
        name_result.canonical_test.code
        if name_result.canonical_test is not None
        else None
    )

    unit_result = normalize_unit(value, unit, canonical_test_code)
    date_result = normalize_result_date(result_date)
    range_result = normalize_reference_range(reference_range)

    # Abnormality classification: compare the normalized value against
    # the normalized reference range. This only proceeds when both
    # the value and range have been successfully normalized.
    abnormality_result = classify_abnormality(
        normalized_value=unit_result.normalized_value,
        normalized_reference_lower=range_result.normalized_reference_lower,
        normalized_reference_upper=range_result.normalized_reference_upper,
        inclusive_lower=range_result.inclusive_lower,
        inclusive_upper=range_result.inclusive_upper,
        normalized_unit=unit_result.normalized_unit,
        reference_normalized_unit=None,
    )

    return {
        "canonical_test_id": (
            name_result.canonical_test.id
            if name_result.canonical_test is not None
            else None
        ),
        "normalization_status": name_result.status,
        "normalized_value": unit_result.normalized_value,
        "normalized_unit": unit_result.normalized_unit,
        "unit_normalization_status": unit_result.status,
        "normalized_result_date": date_result.normalized_date,
        "date_normalization_status": date_result.status,
        "normalized_reference_lower": range_result.normalized_reference_lower,
        "normalized_reference_upper": range_result.normalized_reference_upper,
        "reference_range_inclusive_lower": range_result.inclusive_lower,
        "reference_range_inclusive_upper": range_result.inclusive_upper,
        "reference_range_normalization_status": range_result.status,
        "abnormality_status": abnormality_result.status,
    }


def _persist_completed_extraction(
    db: Session,
    report_id: uuid.UUID,
    source_field: ExtractionSourceField,
    report_source_text: str,
    candidates: list,
) -> CandidateExtraction:
    """Persist a successful extraction with provenance-verified evidence.

    For each candidate, the AI's evidence hint (item.evidence) is matched
    against the actual report_source_text (the native-extracted or OCR
    text from the report). Only text verified as present in the actual
    report is stored as ExtractionEvidence.source_text — AI-only text
    that cannot be located in the report is never stored as provenance.
    """
    now = datetime.now(timezone.utc)
    extraction = CandidateExtraction(
        report_id=report_id,
        status=ExtractionRunStatus.COMPLETED,
        source_field=source_field,
        model_version=MODEL_VERSION,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        started_at=now,
        completed_at=now,
    )
    # Build CandidateResult objects with their ExtractionEvidence
    # provenance records. The evidence record is created automatically
    # alongside every candidate — never fabricated, never accepted
    # from client input.
    #
    # Evidence provenance flow:
    #   AI evidence hint -> matched against actual report source text
    #   -> matched text stored (or None if not found)
    result_objects = []
    for item in candidates:
        candidate_result = CandidateResult(
            test_name=item.test_name,
            value=item.value,
            unit=item.unit,
            reference_range=item.reference_range,
            specimen=item.specimen,
            result_date=item.result_date,
            evidence=item.evidence,
            confidence=item.confidence,
            # Deterministic, Gemini-free normalization (task rule 9): the
            # source test_name/value/unit/reference_range above are never
            # touched by this — only the additive normalization columns
            # are set.
            **_normalization_fields(
                db, item.test_name, item.value, item.unit, item.result_date,
                item.reference_range,
            ),
        )
        # Structured provenance record: the AI's evidence hint is matched
        # against the actual report source text. Only text verified as
        # present in the authoritative report is stored — never AI-only
        # text. page_number and bounding_box_* are None until the
        # extraction pipeline provides page-level granularity.
        matched_source_text = match_evidence_to_source(
            item.evidence, report_source_text
        )
        evidence_record = ExtractionEvidence(
            candidate_result=candidate_result,
            extraction_run=extraction,
            report_id=report_id,
            source_column=source_field,
            page_number=None,
            source_text=matched_source_text,
            bounding_box_x=None,
            bounding_box_y=None,
            bounding_box_width=None,
            bounding_box_height=None,
        )
        result_objects.append(candidate_result)

    extraction.results = result_objects

    try:
        db.add(extraction)
        db.commit()
        db.refresh(extraction)
    except IntegrityError:
        # The database-enforced partial unique index (see
        # CandidateExtraction.__table_args__ / migration
        # a4d6e1f8c3b7) rejected this insert because another
        # concurrent request already committed a COMPLETED extraction
        # for this report first. That's not a failure from the
        # caller's point of view — the existing, now-committed
        # extraction is exactly what a sequential caller would have
        # gotten back, so fetch and return it instead of surfacing an
        # internal database conflict.
        db.rollback()
        winner = (
            db.query(CandidateExtraction)
            .filter(
                CandidateExtraction.report_id == report_id,
                CandidateExtraction.status == ExtractionRunStatus.COMPLETED,
            )
            .first()
        )
        if winner is not None:
            return winner
        # The conflict wasn't actually the expected one (e.g. some other
        # constraint, or the winning row isn't visible yet) — fail safe
        # rather than guessing.
        raise ExtractionPersistenceError() from None
    except SQLAlchemyError:
        db.rollback()
        raise ExtractionPersistenceError() from None

    return extraction


def _persist_failed_extraction(
    db: Session,
    report_id: uuid.UUID,
    source_field: ExtractionSourceField,
    error_message: str,
) -> CandidateExtraction:
    # No CandidateResult rows are ever attached to a FAILED extraction —
    # a failure never produces even partial candidate data (task rule
    # 12: "Do not persist invalid candidate data").
    now = datetime.now(timezone.utc)
    extraction = CandidateExtraction(
        report_id=report_id,
        status=ExtractionRunStatus.FAILED,
        source_field=source_field,
        error_message=error_message[:500],
        model_version=MODEL_VERSION,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        started_at=now,
        completed_at=now,
    )

    try:
        db.add(extraction)
        db.commit()
        db.refresh(extraction)
    except SQLAlchemyError:
        db.rollback()
        raise ExtractionPersistenceError() from None

    return extraction
