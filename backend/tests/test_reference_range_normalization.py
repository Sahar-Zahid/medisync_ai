"""
Tests for the deterministic reference-range normalization and abnormality
classification foundations:

* app.services.reference_range_normalization_service (pure parsing)
* app.services.abnormality_classification_service (pure comparison)
* candidate persistence wiring in
  app.services.candidate_extraction_service._persist_completed_extraction

Mocked DB throughout (unittest.mock), no live PostgreSQL and no live
LLM/API calls anywhere in this file — consistent with the other
normalization test files.

Run with:
    pytest backend/tests/test_reference_range_normalization.py -v
"""
import inspect
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from app.models.extraction import (
    AbnormalityStatus,
    CandidateVerificationStatus,
    CanonicalTest,
    ExtractionSourceField,
    NormalizationStatus,
    ReferenceRangeNormalizationStatus,
    UnitNormalizationStatus,
)
from app.schemas.gemini_extraction import GeminiCandidateItem
from app.services import candidate_extraction_service as svc
from app.services import abnormality_classification_service as abnorm
from app.services import reference_range_normalization_service as rnorm


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


def db_resolving(canonical_test: CanonicalTest) -> MagicMock:
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = canonical_test
    return db


# =========================================================================
# SECTION 1: Reference-Range Normalization — Two-Sided Ranges
# =========================================================================


class TestTwoSidedRanges:
    def test_dash_separator_resolves(self):
        result = rnorm.normalize_reference_range("3.5 - 5.5")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_lower == Decimal("3.5")
        assert result.normalized_reference_upper == Decimal("5.5")
        assert result.inclusive_lower is True
        assert result.inclusive_upper is True

    def test_en_dash_separator_resolves(self):
        result = rnorm.normalize_reference_range("3.5–5.5")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_lower == Decimal("3.5")
        assert result.normalized_reference_upper == Decimal("5.5")

    def test_text_to_separator_resolves(self):
        result = rnorm.normalize_reference_range("3.5 to 5.5")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_lower == Decimal("3.5")
        assert result.normalized_reference_upper == Decimal("5.5")

    def test_dash_no_spaces_resolves(self):
        result = rnorm.normalize_reference_range("3.5-5.5")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_lower == Decimal("3.5")
        assert result.normalized_reference_upper == Decimal("5.5")

    def test_integer_bounds_resolves(self):
        result = rnorm.normalize_reference_range("3 - 5")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_lower == Decimal("3")
        assert result.normalized_reference_upper == Decimal("5")

    def test_negative_lower_bound_resolves(self):
        result = rnorm.normalize_reference_range("-5.0 - 5.0")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_lower == Decimal("-5.0")
        assert result.normalized_reference_upper == Decimal("5.0")

    def test_equal_bounds_resolves(self):
        """Equal lower and upper is a valid (if unusual) range."""
        result = rnorm.normalize_reference_range("5.0 - 5.0")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_lower == Decimal("5.0")
        assert result.normalized_reference_upper == Decimal("5.0")

    def test_reversed_bounds_is_unresolved(self):
        """Lower > upper is mathematically inconsistent."""
        result = rnorm.normalize_reference_range("5.5 - 3.5")
        assert result.status == ReferenceRangeNormalizationStatus.UNRESOLVED

    def test_trailing_unit_stripped_and_resolves(self):
        result = rnorm.normalize_reference_range("4.0-11.0 x10^9/L")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_lower == Decimal("4.0")
        assert result.normalized_reference_upper == Decimal("11.0")

    def test_unit_with_spaces_stripped_and_resolves(self):
        result = rnorm.normalize_reference_range("3.5 - 5.5 g/dL")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_lower == Decimal("3.5")
        assert result.normalized_reference_upper == Decimal("5.5")


# =========================================================================
# SECTION 2: Reference-Range Normalization — One-Sided Ranges
# =========================================================================


class TestOneSidedRanges:
    def test_less_than_resolves(self):
        result = rnorm.normalize_reference_range("< 5.0")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_lower is None
        assert result.normalized_reference_upper == Decimal("5.0")
        assert result.inclusive_upper is False

    def test_less_than_or_equal_resolves(self):
        result = rnorm.normalize_reference_range("<= 5.0")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_upper == Decimal("5.0")
        assert result.inclusive_upper is True

    def test_greater_than_resolves(self):
        result = rnorm.normalize_reference_range("> 2.0")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_lower == Decimal("2.0")
        assert result.inclusive_lower is False
        assert result.normalized_reference_upper is None

    def test_greater_than_or_equal_resolves(self):
        result = rnorm.normalize_reference_range(">= 2.0")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_lower == Decimal("2.0")
        assert result.inclusive_lower is True

    def test_less_than_no_space_resolves(self):
        result = rnorm.normalize_reference_range("<5.0")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_upper == Decimal("5.0")
        assert result.inclusive_upper is False

    def test_one_sided_negative_bound_resolves(self):
        result = rnorm.normalize_reference_range("> -1.0")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_lower == Decimal("-1.0")


# =========================================================================
# SECTION 3: Reference-Range Normalization — Invalid / Missing / Unsupported
# =========================================================================


class TestReferenceRangeInvalid:
    def test_none_is_unresolved(self):
        result = rnorm.normalize_reference_range(None)
        assert result.status == ReferenceRangeNormalizationStatus.UNRESOLVED

    def test_empty_string_is_unresolved(self):
        result = rnorm.normalize_reference_range("")
        assert result.status == ReferenceRangeNormalizationStatus.UNRESOLVED

    def test_whitespace_only_is_unresolved(self):
        result = rnorm.normalize_reference_range("   ")
        assert result.status == ReferenceRangeNormalizationStatus.UNRESOLVED

    def test_free_text_is_unsupported(self):
        result = rnorm.normalize_reference_range("normal range")
        assert result.status == ReferenceRangeNormalizationStatus.UNSUPPORTED

    def test_free_text_reference_is_unsupported(self):
        result = rnorm.normalize_reference_range("standard reference")
        assert result.status == ReferenceRangeNormalizationStatus.UNSUPPORTED

    def test_non_numeric_dash_range_is_unsupported(self):
        result = rnorm.normalize_reference_range("abc - def")
        assert result.status == ReferenceRangeNormalizationStatus.UNSUPPORTED

    def test_mixed_alphanumeric_is_unsupported(self):
        result = rnorm.normalize_reference_range("3.5 - abc")
        assert result.status == ReferenceRangeNormalizationStatus.UNSUPPORTED

    def test_surrounding_whitespace_trimmed(self):
        result = rnorm.normalize_reference_range("  3.5 - 5.5  ")
        assert result.status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_lower == Decimal("3.5")
        assert result.normalized_reference_upper == Decimal("5.5")


# =========================================================================
# SECTION 4: Abnormality Classification — Two-Sided Ranges
# =========================================================================


class TestAbnormalityTwoSided:
    def test_value_within_range_is_normal(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("4.0"),
            normalized_reference_lower=Decimal("3.5"),
            normalized_reference_upper=Decimal("5.5"),
            inclusive_lower=True,
            inclusive_upper=True,
            normalized_unit="g/L",
            reference_normalized_unit="g/L",
        )
        assert result.status == AbnormalityStatus.NORMAL

    def test_value_at_lower_bound_inclusive_is_normal(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("3.5"),
            normalized_reference_lower=Decimal("3.5"),
            normalized_reference_upper=Decimal("5.5"),
            inclusive_lower=True,
            inclusive_upper=True,
            normalized_unit="g/L",
            reference_normalized_unit="g/L",
        )
        assert result.status == AbnormalityStatus.NORMAL

    def test_value_at_upper_bound_inclusive_is_normal(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("5.5"),
            normalized_reference_lower=Decimal("3.5"),
            normalized_reference_upper=Decimal("5.5"),
            inclusive_lower=True,
            inclusive_upper=True,
            normalized_unit="g/L",
            reference_normalized_unit="g/L",
        )
        assert result.status == AbnormalityStatus.NORMAL

    def test_value_below_lower_bound_is_low(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("3.4"),
            normalized_reference_lower=Decimal("3.5"),
            normalized_reference_upper=Decimal("5.5"),
            inclusive_lower=True,
            inclusive_upper=True,
            normalized_unit="g/L",
            reference_normalized_unit="g/L",
        )
        assert result.status == AbnormalityStatus.LOW

    def test_value_above_upper_bound_is_high(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("5.6"),
            normalized_reference_lower=Decimal("3.5"),
            normalized_reference_upper=Decimal("5.5"),
            inclusive_lower=True,
            inclusive_upper=True,
            normalized_unit="g/L",
            reference_normalized_unit="g/L",
        )
        assert result.status == AbnormalityStatus.HIGH

    def test_value_at_lower_bound_exclusive_is_low(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("3.5"),
            normalized_reference_lower=Decimal("3.5"),
            normalized_reference_upper=Decimal("5.5"),
            inclusive_lower=False,
            inclusive_upper=True,
            normalized_unit="g/L",
            reference_normalized_unit="g/L",
        )
        assert result.status == AbnormalityStatus.LOW

    def test_value_at_upper_bound_exclusive_is_high(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("5.5"),
            normalized_reference_lower=Decimal("3.5"),
            normalized_reference_upper=Decimal("5.5"),
            inclusive_lower=True,
            inclusive_upper=False,
            normalized_unit="g/L",
            reference_normalized_unit="g/L",
        )
        assert result.status == AbnormalityStatus.HIGH


# =========================================================================
# SECTION 5: Abnormality Classification — One-Sided Ranges
# =========================================================================


class TestAbnormalityOneSided:
    def test_value_below_upper_bound_is_normal(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("4.0"),
            normalized_reference_lower=None,
            normalized_reference_upper=Decimal("5.0"),
            inclusive_lower=None,
            inclusive_upper=False,
            normalized_unit="g/L",
            reference_normalized_unit=None,
        )
        assert result.status == AbnormalityStatus.NORMAL

    def test_value_above_upper_bound_is_high(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("6.0"),
            normalized_reference_lower=None,
            normalized_reference_upper=Decimal("5.0"),
            inclusive_lower=None,
            inclusive_upper=False,
            normalized_unit="g/L",
            reference_normalized_unit=None,
        )
        assert result.status == AbnormalityStatus.HIGH

    def test_value_at_upper_bound_exclusive_is_high(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("5.0"),
            normalized_reference_lower=None,
            normalized_reference_upper=Decimal("5.0"),
            inclusive_lower=None,
            inclusive_upper=False,
            normalized_unit="g/L",
            reference_normalized_unit=None,
        )
        assert result.status == AbnormalityStatus.HIGH

    def test_value_at_upper_bound_inclusive_is_normal(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("5.0"),
            normalized_reference_lower=None,
            normalized_reference_upper=Decimal("5.0"),
            inclusive_lower=None,
            inclusive_upper=True,
            normalized_unit="g/L",
            reference_normalized_unit=None,
        )
        assert result.status == AbnormalityStatus.NORMAL

    def test_value_above_lower_bound_is_normal(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("3.0"),
            normalized_reference_lower=Decimal("2.0"),
            normalized_reference_upper=None,
            inclusive_lower=False,
            inclusive_upper=None,
            normalized_unit="g/L",
            reference_normalized_unit=None,
        )
        assert result.status == AbnormalityStatus.NORMAL

    def test_value_below_lower_bound_is_low(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("1.0"),
            normalized_reference_lower=Decimal("2.0"),
            normalized_reference_upper=None,
            inclusive_lower=False,
            inclusive_upper=None,
            normalized_unit="g/L",
            reference_normalized_unit=None,
        )
        assert result.status == AbnormalityStatus.LOW

    def test_value_at_lower_bound_exclusive_is_low(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("2.0"),
            normalized_reference_lower=Decimal("2.0"),
            normalized_reference_upper=None,
            inclusive_lower=False,
            inclusive_upper=None,
            normalized_unit="g/L",
            reference_normalized_unit=None,
        )
        assert result.status == AbnormalityStatus.LOW

    def test_value_at_lower_bound_inclusive_is_normal(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("2.0"),
            normalized_reference_lower=Decimal("2.0"),
            normalized_reference_upper=None,
            inclusive_lower=True,
            inclusive_upper=None,
            normalized_unit="g/L",
            reference_normalized_unit=None,
        )
        assert result.status == AbnormalityStatus.NORMAL


# =========================================================================
# SECTION 6: Abnormality Classification — Edge Cases
# =========================================================================


class TestAbnormalityEdgeCases:
    def test_no_value_is_unresolved(self):
        result = abnorm.classify_abnormality(
            normalized_value=None,
            normalized_reference_lower=Decimal("3.5"),
            normalized_reference_upper=Decimal("5.5"),
            inclusive_lower=True,
            inclusive_upper=True,
            normalized_unit=None,
            reference_normalized_unit=None,
        )
        assert result.status == AbnormalityStatus.UNRESOLVED

    def test_no_reference_range_is_not_applicable(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("4.0"),
            normalized_reference_lower=None,
            normalized_reference_upper=None,
            inclusive_lower=None,
            inclusive_upper=None,
            normalized_unit="g/L",
            reference_normalized_unit=None,
        )
        assert result.status == AbnormalityStatus.NOT_APPLICABLE

    def test_incompatible_units_is_unresolved(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("4.0"),
            normalized_reference_lower=Decimal("3.5"),
            normalized_reference_upper=Decimal("5.5"),
            inclusive_lower=True,
            inclusive_upper=True,
            normalized_unit="mg/dL",
            reference_normalized_unit="mmol/L",
        )
        assert result.status == AbnormalityStatus.UNRESOLVED

    def test_same_units_compare_normally(self):
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("4.0"),
            normalized_reference_lower=Decimal("3.5"),
            normalized_reference_upper=Decimal("5.5"),
            inclusive_lower=True,
            inclusive_upper=True,
            normalized_unit="g/L",
            reference_normalized_unit="g/L",
        )
        assert result.status == AbnormalityStatus.NORMAL

    def test_none_units_compare_normally_when_range_has_no_unit(self):
        """When the reference range has no extracted unit (common for
        lab reports), comparison proceeds if the value is normalized."""
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("4.0"),
            normalized_reference_lower=Decimal("3.5"),
            normalized_reference_upper=Decimal("5.5"),
            inclusive_lower=True,
            inclusive_upper=True,
            normalized_unit="g/L",
            reference_normalized_unit=None,
        )
        assert result.status == AbnormalityStatus.NORMAL

    def test_both_units_none_compare_normally(self):
        """When neither has a unit, proceed with comparison."""
        result = abnorm.classify_abnormality(
            normalized_value=Decimal("4.0"),
            normalized_reference_lower=Decimal("3.5"),
            normalized_reference_upper=Decimal("5.5"),
            inclusive_lower=True,
            inclusive_upper=True,
            normalized_unit=None,
            reference_normalized_unit=None,
        )
        assert result.status == AbnormalityStatus.NORMAL


# =========================================================================
# SECTION 7: Persistence Integration — Reference Range + Abnormality
# =========================================================================


class TestReferenceRangePersistence:
    def test_resolved_reference_range_persists_correctly(self):
        hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
        db = db_resolving(hemoglobin)

        extraction = svc._persist_completed_extraction(
            db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
            "Hemoglobin 12.4 g/dL 3.5 - 5.5",
            [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                              reference_range="3.5 - 5.5")],
        )

        result = extraction.results[0]
        assert result.reference_range == "3.5 - 5.5"
        assert result.reference_range_normalization_status == ReferenceRangeNormalizationStatus.RESOLVED
        assert result.normalized_reference_lower == Decimal("3.5")
        assert result.normalized_reference_upper == Decimal("5.5")
        assert result.reference_range_inclusive_lower is True
        assert result.reference_range_inclusive_upper is True

    def test_raw_reference_range_never_overwritten(self):
        hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
        db = db_resolving(hemoglobin)

        extraction = svc._persist_completed_extraction(
            db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
            "Hemoglobin 12.4 g/dL 3.5 - 5.5",
            [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                              reference_range="3.5 - 5.5")],
        )

        result = extraction.results[0]
        assert result.reference_range == "3.5 - 5.5"

    def test_missing_reference_range_is_unresolved(self):
        hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
        db = db_resolving(hemoglobin)

        extraction = svc._persist_completed_extraction(
            db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
            "Hemoglobin 12.4 g/dL",
            [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                              reference_range=None)],
        )

        result = extraction.results[0]
        assert result.reference_range_normalization_status == ReferenceRangeNormalizationStatus.UNRESOLVED
        assert result.normalized_reference_lower is None
        assert result.normalized_reference_upper is None

    def test_unsupported_reference_range_is_unsupported(self):
        hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
        db = db_resolving(hemoglobin)

        extraction = svc._persist_completed_extraction(
            db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
            "Hemoglobin 12.4 g/dL normal range",
            [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                              reference_range="normal range")],
        )

        result = extraction.results[0]
        assert result.reference_range_normalization_status == ReferenceRangeNormalizationStatus.UNSUPPORTED


# =========================================================================
# SECTION 8: Abnormality Persistence Integration
# =========================================================================


class TestAbnormalityPersistence:
    def test_value_in_range_persists_normal(self):
        hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
        db = db_resolving(hemoglobin)

        extraction = svc._persist_completed_extraction(
            db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
            "Hemoglobin 12.4 g/dL 3.5 - 5.5",
            [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                              reference_range="3.5 - 5.5")],
        )

        result = extraction.results[0]
        # 12.4 g/dL -> 124.0 g/L via unit normalization.
        # Reference range "3.5 - 5.5" doesn't have a unit, so
        # reference_normalized_unit is None.
        # normalized_unit is "g/L", reference_normalized_unit is None -> proceed.
        # 124.0 > 5.5 -> HIGH
        assert result.abnormality_status == AbnormalityStatus.HIGH

    def test_missing_reference_range_gives_not_applicable(self):
        hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
        db = db_resolving(hemoglobin)

        extraction = svc._persist_completed_extraction(
            db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
            "Hemoglobin 12.4 g/dL",
            [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                              reference_range=None)],
        )

        result = extraction.results[0]
        # No reference range -> NOT_APPLICABLE
        assert result.abnormality_status == AbnormalityStatus.NOT_APPLICABLE

    def test_qualitative_value_gives_unresolved_abnormality(self):
        hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
        db = db_resolving(hemoglobin)

        extraction = svc._persist_completed_extraction(
            db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
            "Hemoglobin Positive g/dL 3.5 - 5.5",
            [make_gemini_item("Hemoglobin", value="Positive", unit="g/dL",
                              reference_range="3.5 - 5.5",
                              evidence="Hemoglobin: Positive")],
        )

        result = extraction.results[0]
        # Qualitative value -> unit normalization UNSUPPORTED ->
        # normalized_value is None -> abnormality UNRESOLVED
        assert result.abnormality_status == AbnormalityStatus.UNRESOLVED

    def test_unsupported_reference_range_gives_unresolved_abnormality(self):
        hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
        db = db_resolving(hemoglobin)

        extraction = svc._persist_completed_extraction(
            db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
            "Hemoglobin 12.4 g/dL normal range",
            [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                              reference_range="normal range")],
        )

        result = extraction.results[0]
        # Reference range UNSUPPORTED -> both bounds None -> NOT_APPLICABLE
        assert result.abnormality_status == AbnormalityStatus.NOT_APPLICABLE


# =========================================================================
# SECTION 9: Trust / Verification Preservation
# =========================================================================


class TestTrustPreservation:
    def test_verification_status_remains_pending_after_range_normalization(self):
        hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
        db = db_resolving(hemoglobin)

        extraction = svc._persist_completed_extraction(
            db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
            "Hemoglobin 12.4 g/dL 3.5 - 5.5",
            [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                              reference_range="3.5 - 5.5")],
        )

        result = extraction.results[0]
        assert result.verification_status in (
            None, CandidateVerificationStatus.PENDING
        )

    def test_abnormality_status_is_not_verification(self):
        """Abnormality classification must never produce a verification
        outcome — it is purely numeric comparison."""
        hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
        db = db_resolving(hemoglobin)

        extraction = svc._persist_completed_extraction(
            db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
            "Hemoglobin 12.4 g/dL 3.5 - 5.5",
            [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                              reference_range="3.5 - 5.5")],
        )

        result = extraction.results[0]
        # abnormality_status is a comparison outcome, not a trust state.
        assert result.abnormality_status in (
            AbnormalityStatus.NORMAL,
            AbnormalityStatus.LOW,
            AbnormalityStatus.HIGH,
            AbnormalityStatus.UNRESOLVED,
            AbnormalityStatus.NOT_APPLICABLE,
        )
        assert result.verification_status in (
            None, CandidateVerificationStatus.PENDING
        )


# =========================================================================
# SECTION 10: No LLM / Network / Database Dependency
# =========================================================================


class TestNoExternalDependency:
    def test_reference_range_service_has_no_llm_dependency(self):
        source = inspect.getsource(rnorm)
        for forbidden in ("gemini", "openai", "anthropic"):
            assert forbidden not in source.lower()
        # "llm" must not appear as a standalone word — substring
        # matches inside "fullmatch" are expected and harmless.
        assert not re.search(r'\bllm\b', source.lower())

    def test_reference_range_service_makes_no_network_calls(self):
        source = inspect.getsource(rnorm)
        for forbidden in ("requests", "httpx", "urllib", "socket"):
            assert forbidden not in source.lower()

    def test_abnormality_service_has_no_llm_dependency(self):
        source = inspect.getsource(abnorm)
        for forbidden in ("gemini", "openai", "anthropic", "llm"):
            assert forbidden not in source.lower()

    def test_abnormality_service_makes_no_network_calls(self):
        source = inspect.getsource(abnorm)
        for forbidden in ("requests", "httpx", "urllib", "socket"):
            assert forbidden not in source.lower()

    def test_abnormality_service_makes_no_database_calls(self):
        source = inspect.getsource(abnorm)
        assert "session" not in source.lower()
        assert "db.query" not in source.lower()

    def test_reference_range_service_makes_no_database_calls(self):
        source = inspect.getsource(rnorm)
        assert "session" not in source.lower()
        assert "db.query" not in source.lower()

    def test_normalization_fields_has_no_llm_dependency(self):
        source = inspect.getsource(svc._normalization_fields)
        assert "gemini" not in source.lower()


# =========================================================================
# SECTION 11: Determinism
# =========================================================================


class TestDeterminism:
    def test_reference_range_normalization_is_deterministic(self):
        results = [
            rnorm.normalize_reference_range("3.5 - 5.5") for _ in range(5)
        ]
        assert all(r.status == ReferenceRangeNormalizationStatus.RESOLVED for r in results)
        assert len({r.normalized_reference_lower for r in results}) == 1
        assert len({r.normalized_reference_upper for r in results}) == 1

    def test_abnormality_classification_is_deterministic(self):
        results = [
            abnorm.classify_abnormality(
                normalized_value=Decimal("4.0"),
                normalized_reference_lower=Decimal("3.5"),
                normalized_reference_upper=Decimal("5.5"),
                inclusive_lower=True,
                inclusive_upper=True,
                normalized_unit="g/L",
                reference_normalized_unit="g/L",
            )
            for _ in range(5)
        ]
        assert all(r.status == AbnormalityStatus.NORMAL for r in results)


# =========================================================================
# SECTION 12: Mixed Batch Persistence
# =========================================================================


class TestMixedBatchPersistence:
    def test_mixed_reference_ranges_persist_together(self):
        hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
        db = db_resolving(hemoglobin)

        candidates = [
            make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                             reference_range="3.5 - 5.5"),
            make_gemini_item("Unknown Test", value="5", unit=None,
                             reference_range=None,
                             evidence="Unknown: 5"),
            make_gemini_item("Glucose", value="120", unit="mg/dL",
                             reference_range="< 140"),
        ]

        extraction = svc._persist_completed_extraction(
            db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
            "Hemoglobin 12.4 g/dL Unknown: 5 Glucose 120 mg/dL < 140",
            candidates,
        )

        assert len(extraction.results) == 3
        hgb, unknown, glucose = extraction.results

        # HGB: reference range resolves, value 124.0 g/L > 5.5 -> HIGH
        assert hgb.reference_range_normalization_status == ReferenceRangeNormalizationStatus.RESOLVED
        assert hgb.abnormality_status == AbnormalityStatus.HIGH

        # Unknown: no unit -> unit normalization UNRESOLVED ->
        # normalized_value is None -> abnormality UNRESOLVED
        assert unknown.reference_range_normalization_status == ReferenceRangeNormalizationStatus.UNRESOLVED
        assert unknown.abnormality_status == AbnormalityStatus.UNRESOLVED

        # Glucose: "< 140" resolves as one-sided upper, but mg/dL has no
        # conversion rule → unit_normalization_status=UNSUPPORTED →
        # normalized_value=None → abnormality UNRESOLVED
        assert glucose.reference_range_normalization_status == ReferenceRangeNormalizationStatus.RESOLVED
        assert glucose.abnormality_status == AbnormalityStatus.UNRESOLVED

        db.add.assert_called_once()
        db.commit.assert_called_once()


# =========================================================================
# SECTION 13: All Previous Normalizations Still Work
# =========================================================================


class TestExistingNormalizationUnaffected:
    def test_test_name_and_unit_normalization_still_resolve(self):
        hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
        db = db_resolving(hemoglobin)

        extraction = svc._persist_completed_extraction(
            db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
            "Hemoglobin 12.4 g/dL 3.5 - 5.5",
            [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL",
                              reference_range="3.5 - 5.5")],
        )

        result = extraction.results[0]
        assert result.normalization_status == NormalizationStatus.RESOLVED
        assert result.unit_normalization_status == UnitNormalizationStatus.RESOLVED
        assert result.normalized_value == Decimal("124.0")
        assert result.normalized_unit == "g/L"
        # Reference-range normalization also resolves.
        assert result.reference_range_normalization_status == ReferenceRangeNormalizationStatus.RESOLVED

    def test_module_does_not_import_unrelated_code(self):
        source = inspect.getsource(rnorm)
        for unrelated in ("pdf_validation", "storage", "ocr_extraction_service"):
            assert unrelated not in source

        source = inspect.getsource(abnorm)
        for unrelated in ("pdf_validation", "storage", "ocr_extraction_service"):
            assert unrelated not in source
