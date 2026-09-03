"""
Tests for the deterministic result-date normalization foundation:
* app.services.date_normalization_service (pure parsing + validation)
* candidate persistence wiring in
  app.services.candidate_extraction_service._persist_completed_extraction

Mocked DB throughout (unittest.mock), no live PostgreSQL and no live
Gemini API calls anywhere in this file -- consistent with
tests/test_normalization.py and tests/test_unit_normalization.py, which
this file deliberately does not modify or duplicate beyond the small
local helpers below.

Run with:
    pytest backend/tests/test_result_date_normalization.py -v
"""
import inspect
import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock

from app.models.extraction import (
    CandidateVerificationStatus,
    CanonicalTest,
    DateNormalizationStatus,
    ExtractionSourceField,
    NormalizationStatus,
    UnitNormalizationStatus,
)
from app.schemas.gemini_extraction import GeminiCandidateItem
from app.services import candidate_extraction_service as svc
from app.services import date_normalization_service as dnorm


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
# 1. Valid YYYY-MM-DD
# ---------------------------------------------------------------------------


def test_valid_iso_dash_date_resolves():
    result = dnorm.normalize_result_date("2026-06-12")

    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2026, 6, 12)


# ---------------------------------------------------------------------------
# 2. Valid YYYY/MM/DD
# ---------------------------------------------------------------------------


def test_valid_iso_slash_date_resolves():
    result = dnorm.normalize_result_date("2026/06/12")

    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2026, 6, 12)


# ---------------------------------------------------------------------------
# 3. Valid DD-MM-YYYY
# ---------------------------------------------------------------------------


def test_valid_day_first_dash_date_resolves():
    # Day = 25 cannot be a month, so this is unambiguously day-first.
    result = dnorm.normalize_result_date("25-12-2026")

    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2026, 12, 25)


# ---------------------------------------------------------------------------
# 4. Valid DD/MM/YYYY
# ---------------------------------------------------------------------------


def test_valid_day_first_slash_date_resolves():
    result = dnorm.normalize_result_date("25/12/2026")

    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2026, 12, 25)


# ---------------------------------------------------------------------------
# 5. Invalid calendar date
# ---------------------------------------------------------------------------


def test_invalid_calendar_date_unresolved():
    result = dnorm.normalize_result_date("2026-02-30")

    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_day_first_invalid_calendar_date_unresolved():
    # Day = 31 is unambiguously day-first (can't be a month), but April
    # only has 30 days.
    result = dnorm.normalize_result_date("31/04/2026")

    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_invalid_month_unresolved():
    result = dnorm.normalize_result_date("2026-13-01")

    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


# ---------------------------------------------------------------------------
# 6. Leap-year correctness
# ---------------------------------------------------------------------------


def test_leap_year_feb_29_resolves():
    result = dnorm.normalize_result_date("2024-02-29")

    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2024, 2, 29)


def test_non_leap_year_feb_29_unresolved():
    result = dnorm.normalize_result_date("2026-02-29")

    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


# ---------------------------------------------------------------------------
# 7. Ambiguous date rejected
# ---------------------------------------------------------------------------


def test_ambiguous_day_month_rejected():
    # Both 03 and 04 could plausibly be the day or the month depending
    # on locale -- must not be guessed at.
    result = dnorm.normalize_result_date("03/04/2026")

    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_ambiguous_day_month_dash_rejected():
    result = dnorm.normalize_result_date("03-04-2026")

    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


# ---------------------------------------------------------------------------
# 8. Unsupported text date rejected
# ---------------------------------------------------------------------------


def test_free_form_text_date_unsupported():
    result = dnorm.normalize_result_date("12 Jun 2026")

    assert result.status == DateNormalizationStatus.UNSUPPORTED
    assert result.normalized_date is None


def test_month_name_date_unsupported():
    result = dnorm.normalize_result_date("June 12, 2026")

    assert result.status == DateNormalizationStatus.UNSUPPORTED
    assert result.normalized_date is None


def test_two_digit_year_unsupported():
    result = dnorm.normalize_result_date("12-06-26")

    assert result.status == DateNormalizationStatus.UNSUPPORTED
    assert result.normalized_date is None


# ---------------------------------------------------------------------------
# 9. Missing date
# ---------------------------------------------------------------------------


def test_missing_date_none_unresolved():
    result = dnorm.normalize_result_date(None)

    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_missing_date_empty_string_unresolved():
    result = dnorm.normalize_result_date("")

    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


# ---------------------------------------------------------------------------
# 10. Whitespace handling
# ---------------------------------------------------------------------------


def test_whitespace_only_unresolved():
    result = dnorm.normalize_result_date("   ")

    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_surrounding_whitespace_trimmed_and_resolves():
    result = dnorm.normalize_result_date("  2026-06-12  ")

    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2026, 6, 12)


# ---------------------------------------------------------------------------
# 11. Raw result_date remains unchanged
# ---------------------------------------------------------------------------


def test_raw_result_date_untouched_by_persistence():
    db = MagicMock()

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                           result_date="2026-06-12")],
    )

    result = extraction.results[0]
    assert result.result_date == "2026-06-12"


def test_raw_result_date_untouched_even_when_unresolvable():
    db = MagicMock()

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                           result_date="03/04/2026")],
    )

    result = extraction.results[0]
    # Ambiguous and thus UNRESOLVED for the normalized field, but the
    # raw transcription is never altered or dropped.
    assert result.result_date == "03/04/2026"
    assert result.date_normalization_status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_result_date is None


# ---------------------------------------------------------------------------
# 12. Normalized date is additive
# ---------------------------------------------------------------------------


def test_normalized_date_stored_separately_from_raw():
    db = MagicMock()

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                           result_date="2026-06-12")],
    )

    result = extraction.results[0]
    assert result.date_normalization_status == DateNormalizationStatus.RESOLVED
    assert result.normalized_result_date == date(2026, 6, 12)
    # Raw field still independent/unchanged alongside the normalized one.
    assert result.result_date == "2026-06-12"


# ---------------------------------------------------------------------------
# 13. verification_status remains PENDING
# ---------------------------------------------------------------------------


def test_date_normalization_fields_never_include_verification_status():
    fields = svc._normalization_fields(
        MagicMock(), "Hemoglobin", "12.4", "g/dL", "2026-06-12", None
    )
    assert "verification_status" not in fields


def test_persisted_verification_status_still_pending_after_date_normalization():
    db = MagicMock()

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                           result_date="2026-02-30")],
    )

    result = extraction.results[0]
    assert result.__dict__.get("verification_status") in (
        None, CandidateVerificationStatus.PENDING
    )


# ---------------------------------------------------------------------------
# 14. Mixed candidate batch with valid + invalid + missing dates
# ---------------------------------------------------------------------------


def test_mixed_batch_persists_with_independent_date_normalization_outcomes():
    db = MagicMock()

    candidates = [
        make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                          result_date="2026-06-12",
                          evidence="Hemoglobin: 12.4 (2026-06-12)"),
        make_gemini_item("Glucose", value="90", unit="mg/dL",
                          result_date="2026-02-30",
                          evidence="Glucose: 90 (2026-02-30)"),
        make_gemini_item("Platelets", value="250", unit=None,
                          result_date=None,
                          evidence="Platelets: 250"),
        make_gemini_item("Sodium", value="140", unit=None,
                          result_date="12 Jun 2026",
                          evidence="Sodium: 140 (12 Jun 2026)"),
    ]

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL Glucose 90 mg/dL",
        candidates,
    )

    resolved, invalid_calendar, missing, unsupported_text = extraction.results

    assert resolved.date_normalization_status == DateNormalizationStatus.RESOLVED
    assert resolved.normalized_result_date == date(2026, 6, 12)

    assert invalid_calendar.date_normalization_status == DateNormalizationStatus.UNRESOLVED
    assert invalid_calendar.normalized_result_date is None
    assert invalid_calendar.result_date == "2026-02-30"

    assert missing.date_normalization_status == DateNormalizationStatus.UNRESOLVED
    assert missing.normalized_result_date is None
    assert missing.result_date is None

    assert unsupported_text.date_normalization_status == DateNormalizationStatus.UNSUPPORTED
    assert unsupported_text.normalized_result_date is None
    assert unsupported_text.result_date == "12 Jun 2026"

    db.add.assert_called_once()
    db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# 15. Deterministic repeated normalization
# ---------------------------------------------------------------------------


def test_date_normalization_is_deterministic():
    results = [dnorm.normalize_result_date("2026-06-12") for _ in range(5)]
    statuses = {r.status for r in results}
    dates = {r.normalized_date for r in results}
    assert statuses == {DateNormalizationStatus.RESOLVED}
    assert dates == {date(2026, 6, 12)}


def test_ambiguous_date_normalization_is_deterministic():
    results = [dnorm.normalize_result_date("03/04/2026") for _ in range(5)]
    statuses = {r.status for r in results}
    assert statuses == {DateNormalizationStatus.UNRESOLVED}


# ---------------------------------------------------------------------------
# 16. No Gemini dependency
# ---------------------------------------------------------------------------


def test_date_normalization_service_has_no_gemini_dependency():
    """Static guarantee, not just an unexercised mock: the module source
    never references Gemini at all."""
    source = inspect.getsource(dnorm)
    assert "gemini" not in source.lower()


# ---------------------------------------------------------------------------
# 17. No network dependency
# ---------------------------------------------------------------------------


def test_date_normalization_service_makes_no_network_calls():
    """No requests/httpx/urllib import anywhere in the module."""
    source = inspect.getsource(dnorm)
    for forbidden in ("requests", "httpx", "urllib", "socket"):
        assert forbidden not in source.lower()


def test_date_normalization_service_makes_no_database_calls():
    """No sqlalchemy Session usage anywhere in the module -- the
    service accepts only the raw date string (task rule 2)."""
    source = inspect.getsource(dnorm)
    assert "session" not in source.lower()
    assert "db.query" not in source.lower()


# ---------------------------------------------------------------------------
# 18. No metadata/current-date inference
# ---------------------------------------------------------------------------


def test_date_normalization_service_never_uses_current_date():
    """Static guarantee that nothing in the module reaches for
    datetime.now()/date.today()/utcnow() to invent a missing date."""
    source = inspect.getsource(dnorm)
    for forbidden in ("date.today", "datetime.now", "utcnow"):
        assert forbidden not in source


def test_normalize_result_date_accepts_only_the_raw_string():
    """The public function's signature takes nothing besides the raw
    date string -- no report/upload metadata can be passed in even by
    accident."""
    sig = inspect.signature(dnorm.normalize_result_date)
    assert list(sig.parameters.keys()) == ["raw_date"]


# ---------------------------------------------------------------------------
# 19. Existing name/unit normalization still works
# ---------------------------------------------------------------------------


def test_existing_unit_normalization_unaffected_by_date_normalization():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = hemoglobin

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                           result_date="2026-06-12")],
    )

    result = extraction.results[0]
    assert result.normalization_status == NormalizationStatus.RESOLVED
    assert result.unit_normalization_status == UnitNormalizationStatus.RESOLVED
    assert result.normalized_unit == "g/L"
    # Date normalization ran too, independently.
    assert result.date_normalization_status == DateNormalizationStatus.RESOLVED
    assert result.normalized_result_date == date(2026, 6, 12)


# ---------------------------------------------------------------------------
# 20. Existing extraction behavior remains intact
# ---------------------------------------------------------------------------


def test_extraction_persistence_still_commits_and_refreshes():
    db = MagicMock()

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                           result_date="2026-06-12")],
    )

    assert len(extraction.results) == 1
    db.add.assert_called_once()
    db.commit.assert_called_once()
    db.refresh.assert_called_once()
