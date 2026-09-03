"""
Deterministic lab-result date normalization — foundation layer.

Parses a CandidateResult's raw, source-extracted `result_date` string
(e.g. "2026-06-12") into a validated `datetime.date` representation
using a small, explicit set of date formats — or explicitly declines
to when it can't do so safely.

This module has NO LLM dependency and makes no network calls —
parsing is a pure, deterministic lookup against an explicit format
allowlist (task rule 3: "do NOT attempt to build a giant date parser").
It is independently testable with no database at all — nothing here
queries the database; a resolved date is returned as a plain
`datetime.date` and the caller (candidate_extraction_service) persists
it.

Conservative by design (task rule 4): if the date is missing, doesn't
match any supported format, is an invalid calendar date (e.g.
"2026-02-30"), or is ambiguous between locales (e.g. "03/04/2026"
which could be March 4 or April 3), normalization leaves the result
UNRESOLVED or UNSUPPORTED rather than guessing. Nothing here ever
overwrites CandidateResult.result_date, touches verification_status,
infers a date from unrelated report text, or performs any clinical
interpretation.

SUPPORTED FORMATS (task rule 5): Formats where year, month, and day
positions can be determined unambiguously:

Year-first (ISO-style, always unambiguous):
  * YYYY-MM-DD   (e.g. "2026-06-12")
  * YYYY/MM/DD   (e.g. "2026/06/12")
  * YYYY.MM.DD   (e.g. "2026.06.12")

Day-first (supported only when the first numeric component is > 12,
making it structurally impossible for it to be a month):
  * DD-MM-YYYY   (e.g. "25-12-2026")
  * DD/MM/YYYY   (e.g. "25/12/2026")

Deliberately NOT supported (mapped to UNSUPPORTED):
  * "12 Jun 2026", "June 12, 2026" — free-form text
  * Two-digit year formats (e.g. "12-06-26")
  * Timestamps with time components
  * Any other format not in the allowlist above

AMBIGUOUS dates (mapped to UNRESOLVED):
  * Missing or blank string
  * A string where the first two numeric components are both <= 12,
    making it impossible to determine day-first vs month-first without
    guessing (e.g. "03/04/2026")
  * A string that matches a supported format's shape but represents an
    impossible calendar date (e.g. "2026-02-30", "31/04/2026")
"""
import re
from dataclasses import dataclass
from datetime import date as PyDate
from datetime import datetime

from app.models.extraction import DateNormalizationStatus


@dataclass(frozen=True)
class DateNormalizationResult:
    """Outcome of normalizing one candidate's result_date.
    `normalized_date` is populated only when `status` is RESOLVED;
    always None otherwise — callers should never need to check status
    before trusting that."""
    status: DateNormalizationStatus
    normalized_date: PyDate | None = None


# Year-first format patterns (always unambiguous). Each captures
# year (4 digits), month (2 digits), day (2 digits).
_YEAR_FIRST_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(\d{4})-(\d{2})-(\d{2})$"),
    re.compile(r"^(\d{4})/(\d{2})/(\d{2})$"),
    re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})$"),
]

# Day-first format patterns (ambiguous unless first component > 12).
# Each captures component_a (day or month), component_b (month or day),
# year (4 digits).
_DAY_FIRST_PATTERNS: list[re.Pattern] = [
    re.compile(r"^(\d{2})-(\d{2})-(\d{4})$"),
    re.compile(r"^(\d{2})/(\d{2})/(\d{4})$"),
]


def _try_parse_date(
    year: int, month: int, day: int
) -> PyDate | None:
    """Attempt to construct a valid date. Returns the date if valid,
    None if the calendar date is impossible (e.g. Feb 30, month 13)."""
    try:
        return datetime(year, month, day).date()
    except ValueError:
        return None


def normalize_result_date(raw_date: str | None) -> DateNormalizationResult:
    """
    Deterministically parse `raw_date` using the explicit format
    allowlist above, or explicitly decline to (task rule 5).

    Never calls any LLM/network service, never uses fuzzy date
    matching, and never infers a date from context. A missing, blank,
    ambiguous, or unsupported string is returned as UNRESOLVED or
    UNSUPPORTED — never guessed at.

    The raw_date string is never modified or stored by this function;
    the caller (candidate_extraction_service) preserves the original
    value in CandidateResult.result_date unchanged.
    """
    if raw_date is None:
        return DateNormalizationResult(status=DateNormalizationStatus.UNRESOLVED)

    candidate = raw_date.strip()
    if not candidate:
        return DateNormalizationResult(status=DateNormalizationStatus.UNRESOLVED)

    # --- Year-first patterns (always unambiguous) ---
    for pattern in _YEAR_FIRST_PATTERNS:
        match = pattern.match(candidate)
        if match:
            year_str, month_str, day_str = match.groups()
            try:
                year = int(year_str)
                month = int(month_str)
                day = int(day_str)
            except ValueError:
                return DateNormalizationResult(
                    status=DateNormalizationStatus.UNRESOLVED
                )
            normalized = _try_parse_date(year, month, day)
            if normalized is None:
                return DateNormalizationResult(
                    status=DateNormalizationStatus.UNRESOLVED
                )
            return DateNormalizationResult(
                status=DateNormalizationStatus.RESOLVED,
                normalized_date=normalized,
            )

    # --- Day-first patterns (ambiguous unless first component > 12) ---
    for pattern in _DAY_FIRST_PATTERNS:
        match = pattern.match(candidate)
        if match:
            comp_a_str, comp_b_str, year_str = match.groups()
            try:
                comp_a = int(comp_a_str)
                comp_b = int(comp_b_str)
                year = int(year_str)
            except ValueError:
                return DateNormalizationResult(
                    status=DateNormalizationStatus.UNRESOLVED
                )

            # If both components are <= 12, we cannot determine which
            # is day and which is month — this is genuinely ambiguous
            # between locales and must not be guessed at.
            if comp_a <= 12 and comp_b <= 12:
                return DateNormalizationResult(
                    status=DateNormalizationStatus.UNRESOLVED
                )

            # comp_a > 12 means it must be the day (months only go to
            # 12), so this is unambiguously day-first.
            normalized = _try_parse_date(year, comp_b, comp_a)
            if normalized is None:
                return DateNormalizationResult(
                    status=DateNormalizationStatus.UNRESOLVED
                )
            return DateNormalizationResult(
                status=DateNormalizationStatus.RESOLVED,
                normalized_date=normalized,
            )

    # --- No supported format matched ---
    return DateNormalizationResult(status=DateNormalizationStatus.UNSUPPORTED)
