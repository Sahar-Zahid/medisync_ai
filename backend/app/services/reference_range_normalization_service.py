"""
Deterministic lab-result reference-range normalization — foundation layer.

Parses a CandidateResult's raw, source-extracted `reference_range` string
(e.g. "3.5-5.5", "< 5.0", ">= 2.0") into structured normalized numeric
bounds using a small, explicit set of supported range formats — or
explicitly declines to when it can't do so safely.

This module has NO external API dependency and makes no network calls — parsing
is a pure, deterministic regex-and-decimal operation against an explicit
format allowlist. It is independently testable with no database at all;
nothing here queries the database.

Conservative by design: if the reference_range is missing, doesn't match
any supported format, contains non-numeric bounds, or has mathematically
inconsistent bounds (e.g. reversed lower > upper), normalization leaves
the result UNRESOLVED or UNSUPPORTED rather than guessing. Nothing here
ever overwrites CandidateResult.reference_range, touches
verification_status, or performs any clinical interpretation.

SUPPORTED FORMATS — all must be unambiguously interpretable:

Two-sided ranges (lower BOUND SEP BOUND upper):
  * "3.5 - 5.5"     (dash separator, spaces optional)
  * "3.5–5.5"       (en-dash separator)
  * "3.5 to 5.5"    (text "to" separator)
  Bounds are inclusive on both sides (standard lab convention).

One-sided ranges:
  * "< 5.0"         (upper bound only, exclusive)
  * "<= 5.0"        (upper bound only, inclusive)
  * "> 2.0"         (lower bound only, exclusive)
  * ">= 2.0"        (lower bound only, inclusive)

Deliberately NOT supported (mapped to UNSUPPORTED):
  * Free-form text like "normal", "standard range", "reference"
  * Ranges with embedded units like "3.5-5.5 g/dL" — the unit is
    stripped and only the numeric part parsed; if unit-stripping fails
    the format is unsupported
  * Any format not in the allowlist above

INVALID ranges (mapped to UNRESOLVED):
  * Missing or blank string
  * A string that matches a format's shape but has non-numeric bounds
  * A two-sided range where lower > upper (reversed/inconsistent)
"""
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from app.models.extraction import ReferenceRangeNormalizationStatus


# Only a plain, unsigned-or-signed decimal number: optional leading
# sign, digits, optional "." + digits. Same pattern as used in
# unit_normalization_service.
_NUMERIC_PATTERN = re.compile(r"[+-]?\d+(?:\.\d+)?")



# Two-sided range: BOUND SEP BOUND where BOUND is numeric and SEP
# is one of " - ", "–", or " to ". Captures the two numeric bounds.
_TWO_SIDED_PATTERNS: list[re.Pattern] = [
    # "3.5 - 5.5" or "3.5-5.5" (optional spaces around dash)
    re.compile(rf"({_NUMERIC_PATTERN.pattern})\s*[-–]\s*({_NUMERIC_PATTERN.pattern})"),
    # "3.5 to 5.5"
    re.compile(rf"({_NUMERIC_PATTERN.pattern})\s+to\s+({_NUMERIC_PATTERN.pattern})", re.IGNORECASE),
]

# One-sided patterns: OPERATOR BOUND
_ONE_SIDED_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("<=", re.compile(rf"<=\s*({_NUMERIC_PATTERN.pattern})")),
    ("<", re.compile(rf"<\s*({_NUMERIC_PATTERN.pattern})")),
    (">=", re.compile(rf">=\s*({_NUMERIC_PATTERN.pattern})")),
    (">", re.compile(rf">\s*({_NUMERIC_PATTERN.pattern})")),
]


def _parse_decimal(s: str) -> Decimal | None:
    """Parse a string to Decimal, returning None on failure."""
    try:
        return Decimal(s)
    except InvalidOperation:
        return None


def _strip_reference_range_unit(raw_range: str) -> str:
    """Strip a trailing unit suffix from the reference range string.

    Many lab reports embed the unit in the reference range, e.g.
    ``4.0-11.0 x10^9/L`` or ``3.5-5.5 g/dL``. We need to extract only
    the numeric part for parsing.

    Strategy: find the last numeric token that is followed by
    whitespace or end-of-string (not by a non-space character). This
    correctly identifies the last range number (e.g. "11.0" in
    "4.0-11.0 x10^9/L") as opposed to numbers embedded in the unit
    suffix (e.g. "10" or "9" in "x10^9/L").

    If stripping fails, return the original string -- the format
    patterns will match or not based on what remains.
    """
    candidate = raw_range.strip()

    # Find the last numeric token followed by whitespace or end-of-string
    last_range_number_end = 0
    for match in _NUMERIC_PATTERN.finditer(candidate):
        after = match.end()
        if after >= len(candidate) or candidate[after] in (" ", "\t"):
            last_range_number_end = match.end()

    if last_range_number_end == 0:
        # No suitable numeric token found — return as-is
        return candidate

    return candidate[:last_range_number_end].rstrip()


@dataclass(frozen=True)
class ReferenceRangeNormalizationResult:
    """Outcome of normalizing one candidate's reference_range.

    All fields are populated only when `status` is RESOLVED; always
    None otherwise — callers should never need to check status before
    trusting that.

    For a two-sided range: both lower and upper are populated.
    For a "<" or "<=" one-sided range: only upper is populated.
    For a ">" or ">=" one-sided range: only lower is populated.
    """
    status: ReferenceRangeNormalizationStatus
    normalized_reference_lower: Decimal | None = None
    normalized_reference_upper: Decimal | None = None
    inclusive_lower: bool | None = None
    inclusive_upper: bool | None = None


def normalize_reference_range(
    raw_reference_range: str | None,
) -> ReferenceRangeNormalizationResult:
    """
    Deterministically parse `raw_reference_range` using the explicit
    format allowlist above, or explicitly decline to (conservative by
    design).

    Never calls any external API or network service, never uses fuzzy
    matching, and never infers a range from context. A missing, blank,
    ambiguous, or unsupported string is returned as UNRESOLVED or
    UNSUPPORTED — never guessed at.

    The raw_reference_range string is never modified or stored by this
    function; the caller (candidate_extraction_service) preserves the
    original value in CandidateResult.reference_range unchanged.
    """
    if raw_reference_range is None:
        return ReferenceRangeNormalizationResult(
            status=ReferenceRangeNormalizationStatus.UNRESOLVED,
        )

    candidate = raw_reference_range.strip()
    if not candidate:
        return ReferenceRangeNormalizationResult(
            status=ReferenceRangeNormalizationStatus.UNRESOLVED,
        )

    # Strip trailing unit suffix before pattern matching.
    candidate = _strip_reference_range_unit(candidate)

    # --- One-sided patterns (must check BEFORE two-sided, since "<= 5.0"
    #     contains "-" which could match two-sided if not careful) ---
    for operator, pattern in _ONE_SIDED_PATTERNS:
        match = pattern.fullmatch(candidate)
        if match:
            bound = _parse_decimal(match.group(1))
            if bound is None:
                return ReferenceRangeNormalizationResult(
                    status=ReferenceRangeNormalizationStatus.UNRESOLVED,
                )
            if operator in ("<", "<="):
                # Upper-bound only
                return ReferenceRangeNormalizationResult(
                    status=ReferenceRangeNormalizationStatus.RESOLVED,
                    normalized_reference_upper=bound,
                    inclusive_upper=(operator == "<="),
                )
            else:
                # Lower-bound only
                return ReferenceRangeNormalizationResult(
                    status=ReferenceRangeNormalizationStatus.RESOLVED,
                    normalized_reference_lower=bound,
                    inclusive_lower=(operator == ">="),
                )

    # --- Two-sided patterns ---
    for pattern in _TWO_SIDED_PATTERNS:
        match = pattern.fullmatch(candidate)
        if match:
            lower = _parse_decimal(match.group(1))
            upper = _parse_decimal(match.group(2))
            if lower is None or upper is None:
                return ReferenceRangeNormalizationResult(
                    status=ReferenceRangeNormalizationStatus.UNRESOLVED,
                )
            # Validate: lower must not exceed upper for a two-sided range.
            if lower > upper:
                return ReferenceRangeNormalizationResult(
                    status=ReferenceRangeNormalizationStatus.UNRESOLVED,
                )
            # Standard lab convention: two-sided ranges are inclusive on
            # both sides.
            return ReferenceRangeNormalizationResult(
                status=ReferenceRangeNormalizationStatus.RESOLVED,
                normalized_reference_lower=lower,
                normalized_reference_upper=upper,
                inclusive_lower=True,
                inclusive_upper=True,
            )

    # --- No supported format matched ---
    return ReferenceRangeNormalizationResult(
        status=ReferenceRangeNormalizationStatus.UNSUPPORTED,
    )
