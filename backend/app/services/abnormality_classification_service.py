"""
Deterministic lab-result abnormality classification — foundation layer.

Compares a CandidateResult's normalized numeric value against its
normalized reference range to produce a deterministic abnormality
classification: NORMAL, LOW, HIGH, UNRESOLVED, or NOT_APPLICABLE.

This module has NO external API dependency and makes no network calls —
classification is a pure, deterministic numeric comparison against
already-normalized bounds. It is independently testable with no
database at all; nothing here queries the database.

CRITICAL TRUST RULE: This service is ONLY a numeric comparison
outcome. NORMAL means 'the normalized value falls within the
normalized reference bounds' and NOTHING MORE. It is NOT a diagnosis,
NOT a disease classification, NOT medical advice, and NOT equivalent
to clinical verification. All candidates remain PENDING regardless
of their abnormality_status.

UNIT COMPATIBILITY: Abnormality comparison only happens when the
candidate's normalized value and the reference range's normalized
bounds are in compatible units. If the units are incompatible
(e.g. mg/dL vs mmol/L) or if either the value or range could not
be normalized, the classification is UNRESOLVED. This service
receives already-normalized data from the reference-range and unit
normalization services — it never performs its own unit conversion.
"""
from dataclasses import dataclass
from decimal import Decimal

from app.models.extraction import AbnormalityStatus


@dataclass(frozen=True)
class AbnormalityClassificationResult:
    """Outcome of classifying one candidate's abnormality.

    `status` is always populated. This is a pure comparison outcome
    — not a clinical judgment.
    """
    status: AbnormalityStatus


def classify_abnormality(
    normalized_value: Decimal | None,
    normalized_reference_lower: Decimal | None,
    normalized_reference_upper: Decimal | None,
    inclusive_lower: bool | None,
    inclusive_upper: bool | None,
    normalized_unit: str | None,
    reference_normalized_unit: str | None,
) -> AbnormalityClassificationResult:
    """
    Deterministically classify whether `normalized_value` falls within,
    below, or above the normalized reference range.

    All inputs are already-normalized data from prior normalization
    steps — this function performs NO normalization itself, NO unit
    conversion, and NO database queries.

    Unit compatibility check: if both the value's unit and the range's
    unit are present but differ, comparison is meaningless and
    UNRESOLVED is returned. If the range has no extracted unit (which
    is common — many lab reports don't embed units in the reference
    range string), we proceed with comparison only when the value has
    been unit-normalized (i.e. normalized_unit is not None), accepting
    that the range implicitly shares whatever unit the test is
    measured in.

    For a two-sided range (both lower and upper present):
        value < lower bound → LOW
        value > upper bound → HIGH
        value within bounds → NORMAL

    For a "<" or "<=" one-sided range (only upper present):
        value > upper bound → HIGH
        value <= upper bound → NORMAL

    For a ">" or ">=" one-sided range (only lower present):
        value < lower bound → LOW
        value >= lower bound → NORMAL

    Boundary behavior:
        Inclusive bound: value AT the bound → NORMAL
        Exclusive bound: value AT the bound → outside the bound
    """
    # --- Guard: value must be present and numeric ---
    if normalized_value is None:
        return AbnormalityClassificationResult(
            status=AbnormalityStatus.UNRESOLVED,
        )

    # --- Guard: at least one bound must be present ---
    if normalized_reference_lower is None and normalized_reference_upper is None:
        return AbnormalityClassificationResult(
            status=AbnormalityStatus.NOT_APPLICABLE,
        )

    # --- Unit compatibility check ---
    if (
        normalized_unit is not None
        and reference_normalized_unit is not None
        and normalized_unit != reference_normalized_unit
    ):
        return AbnormalityClassificationResult(
            status=AbnormalityStatus.UNRESOLVED,
        )

    # --- Two-sided range ---
    if normalized_reference_lower is not None and normalized_reference_upper is not None:
        if normalized_value < normalized_reference_lower:
            return AbnormalityClassificationResult(status=AbnormalityStatus.LOW)
        if normalized_value > normalized_reference_upper:
            return AbnormalityClassificationResult(status=AbnormalityStatus.HIGH)
        # Value is between the bounds — definitely NORMAL.
        if normalized_reference_lower < normalized_value < normalized_reference_upper:
            return AbnormalityClassificationResult(status=AbnormalityStatus.NORMAL)
        # Value equals one of the bounds — check inclusivity.
        if normalized_value == normalized_reference_lower:
            if inclusive_lower:
                return AbnormalityClassificationResult(status=AbnormalityStatus.NORMAL)
            else:
                return AbnormalityClassificationResult(status=AbnormalityStatus.LOW)
        if normalized_value == normalized_reference_upper:
            if inclusive_upper:
                return AbnormalityClassificationResult(status=AbnormalityStatus.NORMAL)
            else:
                return AbnormalityClassificationResult(status=AbnormalityStatus.HIGH)
        # Should be unreachable with Decimal comparisons, but be safe.
        return AbnormalityClassificationResult(
            status=AbnormalityStatus.UNRESOLVED,
        )

    # --- One-sided: upper bound only ("<" or "<=") ---
    if normalized_reference_upper is not None:
        if normalized_value > normalized_reference_upper:
            return AbnormalityClassificationResult(status=AbnormalityStatus.HIGH)
        if normalized_value == normalized_reference_upper:
            if inclusive_upper:
                return AbnormalityClassificationResult(status=AbnormalityStatus.NORMAL)
            else:
                return AbnormalityClassificationResult(status=AbnormalityStatus.HIGH)
        return AbnormalityClassificationResult(status=AbnormalityStatus.NORMAL)

    # --- One-sided: lower bound only (">" or ">=") ---
    if normalized_reference_lower is not None:
        if normalized_value < normalized_reference_lower:
            return AbnormalityClassificationResult(status=AbnormalityStatus.LOW)
        if normalized_value == normalized_reference_lower:
            if inclusive_lower:
                return AbnormalityClassificationResult(status=AbnormalityStatus.NORMAL)
            else:
                return AbnormalityClassificationResult(status=AbnormalityStatus.LOW)
        return AbnormalityClassificationResult(status=AbnormalityStatus.NORMAL)

    # Should be unreachable (we already checked for None/None above),
    # but be safe.
    return AbnormalityClassificationResult(
        status=AbnormalityStatus.UNRESOLVED,
    )
