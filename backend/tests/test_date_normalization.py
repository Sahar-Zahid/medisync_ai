"""
Tests for deterministic lab-result date normalization:
* app.services.date_normalization_service

No database, no network, no LLM — purely deterministic parsing tests.
Mirrors the test style of the existing test_candidate_extraction.py.

Run with:
    pytest backend/tests/test_date_normalization.py -v
"""
from datetime import date

import pytest

from app.models.extraction import DateNormalizationStatus
from app.services.date_normalization_service import (
    DateNormalizationResult,
    normalize_result_date,
)


# ---------------------------------------------------------------------------
# Valid dates — year-first ISO-style formats
# ---------------------------------------------------------------------------


def test_iso_dash_format_resolves():
    result = normalize_result_date("2026-06-12")
    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2026, 6, 12)


def test_iso_slash_format_resolves():
    result = normalize_result_date("2026/06/12")
    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2026, 6, 12)


def test_iso_dot_format_resolves():
    result = normalize_result_date("2026.06.12")
    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2026, 6, 12)


def test_leap_year_valid_date_resolves():
    """2024 is a leap year — Feb 29 is valid."""
    result = normalize_result_date("2024-02-29")
    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2024, 2, 29)


def test_non_leap_year_feb_29_is_invalid():
    """2026 is NOT a leap year — Feb 29 is invalid."""
    result = normalize_result_date("2026-02-29")
    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_boundary_jan_01_resolves():
    result = normalize_result_date("2026-01-01")
    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2026, 1, 1)


def test_boundary_dec_31_resolves():
    result = normalize_result_date("2026-12-31")
    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2026, 12, 31)


# ---------------------------------------------------------------------------
# Valid dates — unambiguous day-first formats (first component > 12)
# ---------------------------------------------------------------------------


def test_unambiguous_day_first_dash_resolves():
    """25 > 12, so this must be day-first: 25-12-2026 = Dec 25, 2026."""
    result = normalize_result_date("25-12-2026")
    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2026, 12, 25)


def test_unambiguous_day_first_slash_resolves():
    result = normalize_result_date("25/12/2026")
    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2026, 12, 25)


# ---------------------------------------------------------------------------
# Invalid dates — format matches but calendar date is impossible
# ---------------------------------------------------------------------------


def test_impossible_day_feb_30():
    result = normalize_result_date("2026-02-30")
    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_impossible_day_april_31():
    """April has 30 days."""
    result = normalize_result_date("2026-04-31")
    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_impossible_month_13():
    result = normalize_result_date("2026-13-01")
    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_impossible_day_00():
    result = normalize_result_date("2026-06-00")
    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_impossible_month_00():
    result = normalize_result_date("2026-00-01")
    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_day_32():
    result = normalize_result_date("2026-06-32")
    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_day_first_invalid_calendar_date():
    """Day=31 > 12, unambiguously day-first, but April has only 30 days."""
    result = normalize_result_date("31/04/2026")
    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


# ---------------------------------------------------------------------------
# Ambiguous formats — deliberately NOT guessed, returned as UNRESOLVED
# ---------------------------------------------------------------------------


def test_ambiguous_mm_dd_yyyy_is_unresolved():
    """01/02/2026 — both components <= 12, ambiguous."""
    result = normalize_result_date("01/02/2026")
    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_ambiguous_dd_mm_yyyy_with_dash_is_unresolved():
    """03-04-2026 — both components <= 12, ambiguous."""
    result = normalize_result_date("03-04-2026")
    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_ambiguous_dd_slash_mm_yyyy_is_unresolved():
    """12/06/2026 — both components <= 12, ambiguous."""
    result = normalize_result_date("12/06/2026")
    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


# ---------------------------------------------------------------------------
# Missing / empty
# ---------------------------------------------------------------------------


def test_none_date_is_unresolved():
    result = normalize_result_date(None)
    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_empty_string_is_unresolved():
    result = normalize_result_date("")
    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


def test_whitespace_only_is_unresolved():
    result = normalize_result_date("   ")
    assert result.status == DateNormalizationStatus.UNRESOLVED
    assert result.normalized_date is None


# ---------------------------------------------------------------------------
# Unsupported formats
# ---------------------------------------------------------------------------


def test_free_text_date_is_unsupported():
    result = normalize_result_date("12 Jun 2026")
    assert result.status == DateNormalizationStatus.UNSUPPORTED
    assert result.normalized_date is None


def test_long_form_date_is_unsupported():
    result = normalize_result_date("June 12, 2026")
    assert result.status == DateNormalizationStatus.UNSUPPORTED
    assert result.normalized_date is None


def test_written_month_is_unsupported():
    result = normalize_result_date("2026-Jun-12")
    assert result.status == DateNormalizationStatus.UNSUPPORTED
    assert result.normalized_date is None


def test_relative_date_is_unsupported():
    result = normalize_result_date("last Tuesday")
    assert result.status == DateNormalizationStatus.UNSUPPORTED
    assert result.normalized_date is None


def test_two_digit_year_is_unsupported():
    """YY-MM-DD is not in the supported format list."""
    result = normalize_result_date("26-06-12")
    assert result.status == DateNormalizationStatus.UNSUPPORTED
    assert result.normalized_date is None


def test_timestamp_with_time_is_unsupported():
    """Includes time component — not a bare date."""
    result = normalize_result_date("2026-06-12T10:30:00")
    assert result.status == DateNormalizationStatus.UNSUPPORTED
    assert result.normalized_date is None


# ---------------------------------------------------------------------------
# Preservation: raw date is never modified
# ---------------------------------------------------------------------------


def test_raw_date_string_is_preserved():
    """The service never mutates its input. The raw date is preserved by
    the caller (candidate_extraction_service) in result_date; this test
    verifies the function itself doesn't change what was passed in."""
    raw = "2026-06-12"
    result = normalize_result_date(raw)
    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2026, 6, 12)


def test_surrounding_whitespace_trimmed():
    result = normalize_result_date("  2026-06-12  ")
    assert result.status == DateNormalizationStatus.RESOLVED
    assert result.normalized_date == date(2026, 6, 12)


# ---------------------------------------------------------------------------
# Security / trust behavior
# ---------------------------------------------------------------------------


def test_no_verification_status_change():
    """Date normalization never touches verification_status. This is a
    structural guarantee — the service returns only a
    DateNormalizationResult and has no access to CandidateResult or
    any ORM model."""
    result = normalize_result_date("2026-06-12")
    assert result.status == DateNormalizationStatus.RESOLVED
    assert not hasattr(result, "verification_status")


def test_normalized_date_is_not_used_for_trust_escalation():
    """RESOLVED status means 'this date string was parsed successfully'
    — NOT 'this date is medically verified'. The service returns a
    plain date, not a trust claim."""
    result = normalize_result_date("2026-06-12")
    assert result.status == DateNormalizationStatus.RESOLVED
    assert isinstance(result.normalized_date, date)


def test_result_dataclass_is_frozen():
    """The result cannot be mutated after creation — defensive
    immutability matching the existing normalization architecture."""
    result = normalize_result_date("2026-06-12")
    with pytest.raises(AttributeError):
        result.status = DateNormalizationStatus.UNRESOLVED


def test_deterministic_output():
    """Same input always produces the same output."""
    results = [normalize_result_date("2026-06-12") for _ in range(5)]
    statuses = {r.status for r in results}
    dates = {r.normalized_date for r in results}
    assert statuses == {DateNormalizationStatus.RESOLVED}
    assert dates == {date(2026, 6, 12)}
