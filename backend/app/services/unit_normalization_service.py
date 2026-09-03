"""
Deterministic lab-result unit normalization — foundation layer.

Maps a CandidateResult's raw, Gemini-extracted (value, unit) pair to an
additive normalized (normalized_value, normalized_unit) representation
using a small, explicit, backend-curated set of conversion rules — or
explicitly declines to when it can't do so safely.

This module has NO Gemini/LLM dependency and makes NO network calls —
conversion is a pure, deterministic lookup + arithmetic operation against
an in-code rule table (task rule 3: "do NOT build a giant medical
conversion dictionary"). It is independently testable with no database
at all — nothing here queries the database; a resolved canonical test
*code* (a plain string) is passed in by the caller, which already has it
from app.services.normalization_service.

Conservative by design (task rule 5): if the unit is missing, unknown,
unsupported, or the value can't be safely parsed as a supported numeric
form, normalization leaves the result UNRESOLVED/UNSUPPORTED rather than
guessing. Nothing here ever overwrites CandidateResult.value/unit,
touches CandidateVerificationStatus, or performs any clinical
interpretation (reference ranges, normal/abnormal, diagnosis).

Qualitative values (task rule 6) such as "Positive", "Negative", "Trace"
are never numerically interpreted — they simply fail numeric parsing and
come back UNSUPPORTED, exactly like any other value this foundation
can't safely handle.

PRECISION POLICY (task rule 9): a normalized_value is quantized to the
SAME number of decimal places the raw source value string itself had —
never to a fixed precision baked into the conversion rule. "12.4" (one
decimal place) converting via a x10 factor yields "124.0", not
"124.0000": the extra zeros a naive `factor`-driven rounding would add
do not correspond to any precision the report actually stated, and
would misrepresent the measurement as more precise than it is. A source
value given with more decimal places (e.g. "12.40") is honored as-is
(two decimal places out), since that precision was actually stated by
the report, not invented by this service. This policy applies uniformly
to every conversion rule below — no rule may override it with its own
fixed decimal_places.
"""
import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation

from app.models.extraction import UnitNormalizationStatus

# Only a plain, unsigned-or-signed decimal number (task rule 7): optional
# leading sign, digits, optional "." + digits. No scientific notation, no
# thousands separators, no unicode minus — deliberately minimal for this
# foundation ("do NOT attempt complicated lab-specific parsing yet").
_NUMERIC_VALUE_PATTERN = re.compile(r"^[+-]?\d+(\.\d+)?$")


@dataclass(frozen=True)
class ConversionRule:
    """One explicit, deterministic unit conversion.

    `canonical_test_code` is None for a conversion that is valid
    regardless of what the test is (dimensionally clear, unit-only —
    e.g. g/dL -> g/L is just a decimal shift). It is set to a specific
    CanonicalTest.code for a conversion that is only mathematically
    correct for one exactly-identified analyte/measurand (e.g.
    mass-to-molar conversions depend on that substance's molar mass and,
    for some analytes, on which measurand convention is meant) — task
    rule 8 requires the candidate's test-name normalization to already
    be RESOLVED to that exact test before this rule may be applied.

    There is deliberately no `decimal_places` field: output precision is
    never configured per-rule — see the PRECISION POLICY in this
    module's docstring. A rule only supplies the dimensional conversion
    itself (source_unit, target_unit, factor).
    """
    source_unit: str  # normalized (stripped + casefolded) source unit
    target_unit: str  # display form of the resulting unit
    factor: Decimal  # normalized_value = raw_value * factor
    canonical_test_code: str | None = None


# Small, explicit, deterministic rule set (task rule 4: "sufficient to
# prove the architecture", not a comprehensive dictionary).
#
# Only one rule exists today: a dimensionally trivial, test-independent
# mass-concentration decimal shift that requires no analyte identity at
# all to be mathematically correct. A prior revision of this foundation
# also included a HEMOGLOBIN-specific g/dL -> mmol/L rule; it has been
# removed (not merely disabled) because the project's current
# `HEMOGLOBIN` canonical test identity is not specific enough to
# guarantee which hemoglobin-concentration measurand convention a given
# report means, and this service must never silently pick one (task
# rule 5 — "do not guess"). Re-adding a Hb g/dL -> mmol/L (or any other
# analyte-specific) rule requires the project's canonical-test identity
# model to actually distinguish the exact measurand first — a comment
# alone is not sufficient justification. Until then, g/dL -> mmol/L for
# Hemoglobin is UNSUPPORTED, exactly like any other unit this small
# foundation doesn't yet cover.
_CONVERSION_RULES: list[ConversionRule] = [
    ConversionRule(
        source_unit="g/dl",
        target_unit="g/L",
        factor=Decimal("10"),
        canonical_test_code=None,
    ),
]


def _source_decimal_places(value: Decimal) -> int:
    """Number of decimal places explicitly present in a parsed source
    value (e.g. 1 for Decimal("12.4"), 2 for Decimal("12.40"), 0 for
    Decimal("12")) — derived from the value's own exponent, never from a
    conversion rule. This is the sole input to the precision policy: see
    this module's docstring."""
    exponent = value.as_tuple().exponent
    if isinstance(exponent, int) and exponent < 0:
        return -exponent
    return 0


@dataclass(frozen=True)
class UnitNormalizationResult:
    """Outcome of normalizing one candidate's (value, unit). Both
    `normalized_value` and `normalized_unit` are populated together only
    when `status` is RESOLVED; always None otherwise — callers should
    never need to check status before trusting that."""
    status: UnitNormalizationStatus
    normalized_value: Decimal | None = None
    normalized_unit: str | None = None


def parse_numeric_value(raw_value: str) -> Decimal | None:
    """Pure parsing: returns a Decimal if `raw_value` is a supported
    numeric form (task rule 7), or None otherwise — including for
    qualitative values like "Positive" (task rule 6), empty strings, and
    unsupported formats (scientific notation, ranges, etc). Never raises;
    a malformed or unusual value is simply unparseable, not an error."""
    candidate = raw_value.strip()
    if not _NUMERIC_VALUE_PATTERN.match(candidate):
        return None
    try:
        return Decimal(candidate)
    except InvalidOperation:
        # Defensive only — the regex above should already guarantee a
        # valid Decimal literal; never let a parsing edge case crash
        # extraction persistence (task rule 7: "do not crash the entire
        # extraction").
        return None


def _find_rule(
    unit_key: str, canonical_test_code: str | None
) -> tuple[ConversionRule | None, bool]:
    """Look up a matching rule for `unit_key`.

    Returns (rule, requires_unresolved_test_identity):
    * (rule, False) — a usable rule was found and may be applied now.
    * (None, True) — the only rule(s) for this unit are test-specific
      and no resolved canonical test code was supplied yet, so applying
      one now would violate task rule 8. Caller should return UNRESOLVED
      rather than UNSUPPORTED, since a conversion may still become
      possible once test-name normalization resolves.
    * (None, False) — no rule at all is configured for this unit, or a
      test-specific rule exists but doesn't match the resolved test
      identity. Caller should return UNSUPPORTED.
    """
    matching = [r for r in _CONVERSION_RULES if r.source_unit == unit_key]
    if not matching:
        return None, False

    universal = [r for r in matching if r.canonical_test_code is None]
    if universal:
        # By construction of _CONVERSION_RULES there is at most one
        # universal rule per source unit in this small foundation.
        return universal[0], False

    # Every matching rule is test-specific from here on.
    if canonical_test_code is None:
        return None, True

    for rule in matching:
        if rule.canonical_test_code == canonical_test_code:
            return rule, False

    return None, False


def normalize_unit(
    raw_value: str,
    raw_unit: str | None,
    canonical_test_code: str | None,
) -> UnitNormalizationResult:
    """
    Deterministically convert (raw_value, raw_unit) using the explicit
    rule set above, or explicitly decline to (task rule 5).

    `canonical_test_code` should be the CanonicalTest.code this
    candidate's test-name normalization already RESOLVED to (or None if
    it did not) — this function performs no database lookup itself and
    never calls app.services.normalization_service; the caller (task
    rule 11: unit normalization runs after test-name normalization) is
    responsible for supplying it.

    Never calls Gemini or any other LLM/network service, never uses
    fuzzy unit matching, and never invents a value/unit/analyte identity
    that wasn't explicitly configured.
    """
    parsed_value = parse_numeric_value(raw_value)
    if parsed_value is None:
        # Includes qualitative values (task rule 6) and any unsupported
        # numeric format (task rule 7) — the value itself, not the unit,
        # is why no conversion can happen.
        return UnitNormalizationResult(status=UnitNormalizationStatus.UNSUPPORTED)

    if raw_unit is None or not raw_unit.strip():
        return UnitNormalizationResult(status=UnitNormalizationStatus.UNRESOLVED)

    unit_key = raw_unit.strip().casefold()
    rule, needs_test_identity = _find_rule(unit_key, canonical_test_code)

    if rule is None:
        if needs_test_identity:
            return UnitNormalizationResult(status=UnitNormalizationStatus.UNRESOLVED)
        return UnitNormalizationResult(status=UnitNormalizationStatus.UNSUPPORTED)

    quantum = Decimal(1).scaleb(-_source_decimal_places(parsed_value))
    normalized_value = (parsed_value * rule.factor).quantize(
        quantum, rounding=ROUND_HALF_EVEN
    )

    return UnitNormalizationResult(
        status=UnitNormalizationStatus.RESOLVED,
        normalized_value=normalized_value,
        normalized_unit=rule.target_unit,
    )
