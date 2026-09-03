"""
Tests for deterministic lab test-name normalization:
* app.services.normalization_service (pure alias matching + DB lookup)
* candidate persistence wiring in
  app.services.candidate_extraction_service._persist_completed_extraction

Mocked DB throughout (unittest.mock), no live PostgreSQL and no live
Gemini API calls anywhere in this file — consistent with
tests/test_candidate_extraction.py, which this file deliberately does
not modify or duplicate beyond the small local helpers below.

Run with:
    pytest backend/tests/test_normalization.py -v
"""
import inspect
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.models.extraction import (
    CandidateVerificationStatus,
    CanonicalTest,
    ExtractionSourceField,
    NormalizationStatus,
)
from app.schemas.gemini_extraction import GeminiCandidateItem
from app.services import candidate_extraction_service as svc
from app.services import normalization_service as norm


def make_canonical_test(code: str, display_name: str) -> CanonicalTest:
    canonical = CanonicalTest(code=code, display_name=display_name)
    canonical.id = uuid.uuid4()
    canonical.created_at = datetime.now(timezone.utc)
    return canonical


def make_gemini_item(test_name: str, **overrides) -> GeminiCandidateItem:
    defaults = dict(
        test_name=test_name,
        value="12.4",
        evidence=f"{test_name}: 12.4",
    )
    defaults.update(overrides)
    return GeminiCandidateItem(**defaults)


# ---------------------------------------------------------------------------
# 1. Known exact canonical name resolves
# ---------------------------------------------------------------------------


def test_known_exact_canonical_name_resolves():
    assert norm.resolve_alias("Hemoglobin") == ["HEMOGLOBIN"]

    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = hemoglobin

    result = norm.normalize_test_name(db, "Hemoglobin")

    assert result.status == NormalizationStatus.RESOLVED
    assert result.canonical_test is hemoglobin


# ---------------------------------------------------------------------------
# 2. Known alias resolves
# ---------------------------------------------------------------------------


def test_known_alias_resolves():
    # Different casing/whitespace, same underlying alias.
    for alias in ["HGB", "hgb", "  Hb  ", "Haemoglobin"]:
        assert norm.resolve_alias(alias) == ["HEMOGLOBIN"], alias

    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = hemoglobin

    result = norm.normalize_test_name(db, "HGB")

    assert result.status == NormalizationStatus.RESOLVED
    assert result.canonical_test is hemoglobin


# ---------------------------------------------------------------------------
# 3. Original source name is preserved
# ---------------------------------------------------------------------------


def test_original_source_name_is_preserved_even_when_resolved():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = hemoglobin

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("HGB")],
    )

    result = extraction.results[0]
    # The raw, Gemini-extracted source name is untouched — normalization
    # is purely additive, never a rewrite.
    assert result.test_name == "HGB"
    assert result.normalization_status == NormalizationStatus.RESOLVED
    assert result.canonical_test_id == hemoglobin.id


# ---------------------------------------------------------------------------
# 4. Unknown test remains unresolved
# ---------------------------------------------------------------------------


def test_unknown_test_remains_unresolved():
    assert norm.resolve_alias("Completely Unknown Test") == []

    db = MagicMock()
    result = norm.normalize_test_name(db, "Completely Unknown Test")

    assert result.status == NormalizationStatus.UNRESOLVED
    assert result.canonical_test is None
    # An unknown name never even reaches the database — nothing to look
    # up for a code that doesn't exist.
    db.query.assert_not_called()


def test_unknown_test_does_not_fail_persistence():
    db = MagicMock()

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Completely Unknown Test")],
    )

    result = extraction.results[0]
    assert result.test_name == "Completely Unknown Test"
    assert result.normalization_status == NormalizationStatus.UNRESOLVED
    assert result.canonical_test_id is None


# ---------------------------------------------------------------------------
# 5. Ambiguous mapping remains ambiguous
# ---------------------------------------------------------------------------


def test_ambiguous_mapping_remains_ambiguous():
    # Deliberately configured ambiguous alias (task rule 12): "T3" alone
    # doesn't say Total vs Free T3.
    codes = norm.resolve_alias("T3")
    assert sorted(codes) == ["T3_FREE", "T3_TOTAL"]

    db = MagicMock()
    result = norm.normalize_test_name(db, "T3")

    assert result.status == NormalizationStatus.AMBIGUOUS
    assert result.canonical_test is None
    # No canonical test is guessed/selected — the ambiguous case never
    # even queries the database, since there is nothing safe to look up.
    db.query.assert_not_called()


def test_ambiguous_test_does_not_fail_persistence():
    db = MagicMock()

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("T3")],
    )

    result = extraction.results[0]
    assert result.test_name == "T3"
    assert result.normalization_status == NormalizationStatus.AMBIGUOUS
    assert result.canonical_test_id is None


# ---------------------------------------------------------------------------
# 6. Normalization does not call Gemini
# ---------------------------------------------------------------------------


def test_normalization_service_has_no_gemini_dependency():
    """Static guarantee, not just an unexercised mock: the module source
    never references Gemini at all."""
    source = inspect.getsource(norm)
    assert "gemini" not in source.lower()


# ---------------------------------------------------------------------------
# 7. Normalization does not change verification status
# ---------------------------------------------------------------------------


def test_normalization_fields_never_include_verification_status():
    db = MagicMock()
    # _normalization_fields now also derives the unit-normalization
    # columns (see app.services.unit_normalization_service /
    # test_unit_normalization.py) — value/unit are required parameters
    # so it can run unit normalization after test-name normalization,
    # per task rule 11 of that feature. Test-name normalization's own
    # behavior/keys are unaffected.
    fields = svc._normalization_fields(db, "Hemoglobin", "12.4", "g/dL", None, None)
    assert "verification_status" not in fields
    # Set updated to include the date-normalization, reference-range
    # normalization, and abnormality-classification columns that
    # _normalization_fields now also derives (see
    # test_result_date_normalization.py, test_reference_range_normalization.py).
    # Test-name normalization's own behavior/keys are unaffected.
    assert set(fields.keys()) == {
        "canonical_test_id",
        "normalization_status",
        "normalized_value",
        "normalized_unit",
        "unit_normalization_status",
        "normalized_result_date",
        "date_normalization_status",
        "normalized_reference_lower",
        "normalized_reference_upper",
        "reference_range_inclusive_lower",
        "reference_range_inclusive_upper",
        "reference_range_normalization_status",
        "abnormality_status",
    }


def test_persisted_result_verification_status_is_still_pending_default():
    """_persist_completed_extraction never passes verification_status
    explicitly — it's untouched by normalization and relies solely on
    the CandidateResult column default (CandidateVerificationStatus.
    PENDING), exactly as before this feature."""
    db = MagicMock()

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin")],
    )

    result = extraction.results[0]
    assert "verification_status" not in result.__dict__ or result.__dict__.get(
        "verification_status"
    ) in (None, CandidateVerificationStatus.PENDING)


# ---------------------------------------------------------------------------
# 8. Normalized information persists correctly
# ---------------------------------------------------------------------------


def test_normalized_information_persists_correctly():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = hemoglobin

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin")],
    )

    result = extraction.results[0]
    assert result.normalization_status == NormalizationStatus.RESOLVED
    assert result.canonical_test_id == hemoglobin.id
    db.add.assert_called_once()
    db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# 9. Existing candidate extraction behavior remains intact
# ---------------------------------------------------------------------------


def test_mixed_batch_persists_with_independent_normalization_outcomes():
    """A realistic extraction run with resolved, unresolved, and
    ambiguous results side by side — the extraction as a whole still
    persists in one call exactly as before this feature; only the two
    new fields differ per-result."""
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = hemoglobin

    candidates = [
        make_gemini_item("Hemoglobin"),
        make_gemini_item("Completely Unknown Test"),
        make_gemini_item("T3"),
    ]

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL Unknown: 5 T3: 1 g/dL",
        candidates
    )

    assert len(extraction.results) == 3
    statuses = {r.test_name: r.normalization_status for r in extraction.results}
    assert statuses["Hemoglobin"] == NormalizationStatus.RESOLVED
    assert statuses["Completely Unknown Test"] == NormalizationStatus.UNRESOLVED
    assert statuses["T3"] == NormalizationStatus.AMBIGUOUS
    # Existing persistence behavior (single add/commit for the whole
    # extraction, not per-result) is unaffected.
    db.add.assert_called_once()
    db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# 10. Existing report/upload/OCR behavior remains untouched
#
# No file under app/services/{pdf_extraction_service,
# ocr_extraction_service,report_service}.py, app/routers/reports.py, or
# app/models/report.py was created or modified by this feature (see the
# accompanying "Files modified" list) — tests/test_report_extraction.py
# and tests/test_report_status.py, unmodified, remain the coverage for
# that behavior; duplicating it here would be a speculative test
# unrelated to this feature's actual changes.
# ---------------------------------------------------------------------------
