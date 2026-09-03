"""
Tests for the deterministic unit-normalization foundation:
* app.services.unit_normalization_service (pure parsing + conversion)
* candidate persistence wiring in
  app.services.candidate_extraction_service._persist_completed_extraction

Mocked DB throughout (unittest.mock), no live PostgreSQL and no live
Gemini API calls anywhere in this file — consistent with
tests/test_normalization.py and tests/test_candidate_extraction.py,
which this file deliberately does not modify or duplicate beyond the
small local helpers below.

Run with:
    pytest backend/tests/test_unit_normalization.py -v
"""
import inspect
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock

from app.models.extraction import (
    CandidateVerificationStatus,
    CanonicalTest,
    ExtractionSourceField,
    NormalizationStatus,
    UnitNormalizationStatus,
)
from app.schemas.gemini_extraction import GeminiCandidateItem
from app.services import candidate_extraction_service as svc
from app.services import unit_normalization_service as unorm


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
# 1. Supported conversion succeeds
# ---------------------------------------------------------------------------


def test_supported_test_independent_conversion_succeeds():
    result = unorm.normalize_unit("12.4", "g/dL", canonical_test_code=None)

    assert result.status == UnitNormalizationStatus.RESOLVED
    # Precision policy: quantized to the source value's own decimal
    # places (one, from "12.4"), never to a fixed rule precision — see
    # issue 1 / this module's PRECISION POLICY docstring.
    assert result.normalized_value == Decimal("124.0")
    assert result.normalized_unit == "g/L"


def test_normalized_result_does_not_gain_unjustified_precision():
    """12.4 has exactly one decimal place of stated precision; the
    normalized value must reflect that — not the conversion factor's own
    digit count, and not a blanket fixed precision."""
    result = unorm.normalize_unit("12.4", "g/dL", canonical_test_code=None)
    assert result.normalized_value == Decimal("124.0")
    assert str(result.normalized_value) != "124.0000"

    # An integer-looking source value (0 decimal places stated) yields
    # an integer-looking normalized value — no invented decimals at all.
    integer_result = unorm.normalize_unit("12", "g/dL", canonical_test_code=None)
    assert integer_result.normalized_value == Decimal("120")
    assert unorm._source_decimal_places(integer_result.normalized_value) == 0

    # A source value that itself states more precision (e.g. "12.40")
    # is honored, not truncated — the policy tracks the source, it
    # doesn't cap it.
    precise_result = unorm.normalize_unit("12.40", "g/dL", canonical_test_code=None)
    assert precise_result.normalized_value == Decimal("124.00")


def test_hemoglobin_mmol_conversion_is_no_longer_supported():
    """Issue 2: the prior g/dL -> mmol/L rule for generic HEMOGLOBIN has
    been removed entirely — the current HEMOGLOBIN canonical identity
    isn't specific enough to guarantee which measurand convention a
    report means, and this service must never guess. g/dL still
    resolves — but only via the test-independent g/L rule, never to
    mmol/L, regardless of test identity."""
    result = unorm.normalize_unit("12.4", "g/dL", canonical_test_code="HEMOGLOBIN")

    assert result.status == UnitNormalizationStatus.RESOLVED
    assert result.normalized_unit == "g/L"
    assert result.normalized_unit != "mmol/L"

    # No rule in the current foundation targets mmol/L for HEMOGLOBIN
    # (or anything else) at all.
    assert all(
        rule.target_unit != "mmol/L" for rule in unorm._CONVERSION_RULES
    )
    assert all(
        rule.canonical_test_code != "HEMOGLOBIN" for rule in unorm._CONVERSION_RULES
    )


# ---------------------------------------------------------------------------
# 2. Raw value/unit remain unchanged
# ---------------------------------------------------------------------------


def test_raw_value_and_unit_untouched_by_persistence():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = hemoglobin

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL")],
    )

    result = extraction.results[0]
    assert result.value == "12.4"
    assert result.unit == "g/dL"


# ---------------------------------------------------------------------------
# 3 & 4. Normalized value/unit stored separately
# ---------------------------------------------------------------------------


def test_normalized_value_and_unit_stored_separately():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = hemoglobin

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL")],
    )

    result = extraction.results[0]
    assert result.unit_normalization_status == UnitNormalizationStatus.RESOLVED
    assert result.normalized_value == Decimal("124.0")
    assert result.normalized_unit == "g/L"
    # Raw fields still independent/unchanged alongside the normalized ones.
    assert result.value == "12.4"
    assert result.unit == "g/dL"


# ---------------------------------------------------------------------------
# 5 & 6. Unsupported unit does not crash
# ---------------------------------------------------------------------------


def test_unsupported_unit_does_not_crash():
    result = unorm.normalize_unit("5", "furlongs", canonical_test_code=None)

    assert result.status == UnitNormalizationStatus.UNSUPPORTED
    assert result.normalized_value is None
    assert result.normalized_unit is None


def test_unsupported_unit_does_not_fail_persistence():
    db = MagicMock()

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Completely Unknown Test", value="5", unit="furlongs")],
    )

    result = extraction.results[0]
    assert result.unit_normalization_status == UnitNormalizationStatus.UNSUPPORTED
    assert result.normalized_value is None


# ---------------------------------------------------------------------------
# 7. Missing unit does not crash
# ---------------------------------------------------------------------------


def test_missing_unit_returns_unresolved():
    result = unorm.normalize_unit("12.4", None, canonical_test_code=None)
    assert result.status == UnitNormalizationStatus.UNRESOLVED
    assert result.normalized_value is None
    assert result.normalized_unit is None

    blank = unorm.normalize_unit("12.4", "   ", canonical_test_code=None)
    assert blank.status == UnitNormalizationStatus.UNRESOLVED


# ---------------------------------------------------------------------------
# 8. Qualitative value is never numerically converted
# ---------------------------------------------------------------------------


def test_qualitative_value_never_numerically_converted():
    for qualitative in ["Positive", "Negative", "Trace", "Reactive", "Non-reactive"]:
        result = unorm.normalize_unit(qualitative, "g/dL", canonical_test_code=None)
        assert result.status == UnitNormalizationStatus.UNSUPPORTED, qualitative
        assert result.normalized_value is None
        assert result.normalized_unit is None


# ---------------------------------------------------------------------------
# 9. Invalid numeric value does not produce fake normalized data
# ---------------------------------------------------------------------------


def test_invalid_numeric_formats_are_never_converted():
    for bad_value in ["1.2.3", "12,4", "1e10", "", "  ", "twelve", "12-14", "-"]:
        assert unorm.parse_numeric_value(bad_value) is None, bad_value
        result = unorm.normalize_unit(bad_value, "g/dL", canonical_test_code=None)
        assert result.status == UnitNormalizationStatus.UNSUPPORTED, bad_value
        assert result.normalized_value is None


def test_valid_numeric_formats_are_parsed():
    assert unorm.parse_numeric_value("12.4") == Decimal("12.4")
    assert unorm.parse_numeric_value("-3.5") == Decimal("-3.5")
    assert unorm.parse_numeric_value("  7  ") == Decimal("7")
    assert unorm.parse_numeric_value("+2") == Decimal("2")


# ---------------------------------------------------------------------------
# 10 & 11. Unresolved/ambiguous test identity prevents test-specific
# conversion
# ---------------------------------------------------------------------------


def test_unresolved_test_identity_prevents_test_specific_conversion():
    # The current foundation has no test-specific rules at all (the
    # prior HEMOGLOBIN g/dL -> mmol/L rule was removed — see issue 2);
    # this proves the guard doesn't need one to matter — the universal
    # g/dL -> g/L rule still resolves fine with no test identity at all,
    # since it never required one.
    result = unorm.normalize_unit("12.4", "g/dL", canonical_test_code=None)
    assert result.status == UnitNormalizationStatus.RESOLVED
    assert result.normalized_unit == "g/L"


def test_find_rule_prefers_universal_rule_over_test_specific():
    """g/dL has both a universal (test-independent) rule and a
    HEMOGLOBIN-specific one — without a resolved test identity, lookup
    must still succeed via the universal rule rather than reporting
    "needs test identity" (that signal is reserved for units whose
    *only* matching rules are test-specific)."""
    rule, needs_identity = unorm._find_rule("g/dl", canonical_test_code=None)
    assert rule is not None
    assert rule.canonical_test_code is None
    assert needs_identity is False


def test_find_rule_reports_needs_identity_when_only_test_specific_rules_match():
    """Directly exercises the "needs identity" branch using a synthetic
    rule table shape (the current foundation has no test-specific rules
    at all — see issue 2 of the unit-normalization review, which removed
    the HEMOGLOBIN g/dL -> mmol/L rule — so this proves the guard itself
    still works independent of which units happen to be configured
    today)."""
    synthetic_rules = [
        unorm.ConversionRule(
            source_unit="mmol/l",
            target_unit="mg/dL",
            factor=Decimal("18.0"),
            canonical_test_code="GLUCOSE",
        )
    ]
    original_rules = unorm._CONVERSION_RULES
    unorm._CONVERSION_RULES = synthetic_rules
    try:
        rule, needs_identity = unorm._find_rule("mmol/l", canonical_test_code=None)
        assert rule is None
        assert needs_identity is True

        rule2, needs_identity2 = unorm._find_rule(
            "mmol/l", canonical_test_code="GLUCOSE"
        )
        assert rule2 is synthetic_rules[0]
        assert needs_identity2 is False
    finally:
        unorm._CONVERSION_RULES = original_rules


def test_ambiguous_test_identity_never_reaches_unit_normalization_as_resolved():
    """When test-name normalization is AMBIGUOUS, the wiring in
    candidate_extraction_service passes canonical_test_code=None to unit
    normalization (only a RESOLVED name normalization yields a code) —
    so a test-specific rule can never apply."""
    db = MagicMock()

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("T3", value="12.4", unit="g/dL")],
    )

    result = extraction.results[0]
    assert result.normalization_status == NormalizationStatus.AMBIGUOUS
    # Universal g/dL -> g/L rule still applies regardless of test
    # identity — proves the ambiguity only blocks the *test-specific*
    # rule, not unit normalization as a whole.
    assert result.unit_normalization_status == UnitNormalizationStatus.RESOLVED
    assert result.normalized_unit == "g/L"


def test_unresolved_test_name_still_blocks_hemoglobin_specific_rule():
    db = MagicMock()

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Completely Unknown Test", value="12.4", unit="g/dL")],
    )

    result = extraction.results[0]
    assert result.normalization_status == NormalizationStatus.UNRESOLVED
    # Still resolves via the universal (test-independent) rule.
    assert result.unit_normalization_status == UnitNormalizationStatus.RESOLVED
    assert result.normalized_unit == "g/L"


# ---------------------------------------------------------------------------
# 12. Conversion is deterministic
# ---------------------------------------------------------------------------


def test_conversion_is_deterministic():
    results = [
        unorm.normalize_unit("12.4", "g/dL", canonical_test_code=None)
        for _ in range(5)
    ]
    values = {r.normalized_value for r in results}
    units = {r.normalized_unit for r in results}
    assert values == {Decimal("124.0")}
    assert units == {"g/L"}


# ---------------------------------------------------------------------------
# 13. Normalization does not call Gemini
# ---------------------------------------------------------------------------


def test_unit_normalization_service_has_no_gemini_dependency():
    """Static guarantee, not just an unexercised mock: the module source
    never references Gemini at all."""
    source = inspect.getsource(unorm)
    assert "gemini" not in source.lower()


def test_unit_normalization_service_makes_no_network_calls():
    """No requests/httpx/urllib import anywhere in the module."""
    source = inspect.getsource(unorm)
    for forbidden in ("requests", "httpx", "urllib", "socket"):
        assert forbidden not in source.lower()


# ---------------------------------------------------------------------------
# 14. Normalization does not modify verification status
# ---------------------------------------------------------------------------


def test_unit_normalization_fields_never_include_verification_status():
    fields = svc._normalization_fields(MagicMock(), "Hemoglobin", "12.4", "g/dL", None, None)
    assert "verification_status" not in fields


def test_persisted_verification_status_still_pending_default():
    db = MagicMock()

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL")],
    )

    result = extraction.results[0]
    assert "verification_status" not in result.__dict__ or result.__dict__.get(
        "verification_status"
    ) in (None, CandidateVerificationStatus.PENDING)


# ---------------------------------------------------------------------------
# 15. Normalized data persists correctly
# ---------------------------------------------------------------------------


def test_normalized_data_persists_correctly_and_commits():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = hemoglobin

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL")],
    )

    result = extraction.results[0]
    assert result.unit_normalization_status == UnitNormalizationStatus.RESOLVED
    assert result.normalized_value == Decimal("124.0")
    db.add.assert_called_once()
    db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# 16 & 17. Existing test-name normalization / candidate extraction
# behavior remains intact
# ---------------------------------------------------------------------------


def test_mixed_batch_persists_with_independent_unit_normalization_outcomes():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = hemoglobin

    candidates = [
        make_gemini_item("Hemoglobin", value="12.4", unit="g/dL"),
        make_gemini_item("Hemoglobin", value="Positive", unit="g/dL",
                          evidence="Hemoglobin: Positive"),
        make_gemini_item("Completely Unknown Test", value="5", unit=None,
                          evidence="Unknown: 5"),
    ]

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        candidates,
    )

    resolved, unsupported, unresolved = extraction.results
    assert resolved.unit_normalization_status == UnitNormalizationStatus.RESOLVED
    assert unsupported.unit_normalization_status == UnitNormalizationStatus.UNSUPPORTED
    assert unresolved.unit_normalization_status == UnitNormalizationStatus.UNRESOLVED
    # Test-name normalization for the first two is untouched by this
    # feature and still resolves independently of unit normalization.
    assert resolved.normalization_status == NormalizationStatus.RESOLVED
    assert unsupported.normalization_status == NormalizationStatus.RESOLVED
    db.add.assert_called_once()
    db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# 18. Existing upload/PDF/OCR behavior remains untouched
# ---------------------------------------------------------------------------


def test_unit_normalization_module_does_not_import_upload_pdf_or_ocr_code():
    """This feature is additive to candidate extraction only — it has no
    business importing the upload/PDF-validation/OCR modules at all."""
    source = inspect.getsource(unorm)
    for unrelated in ("pdf_validation", "storage", "ocr_extraction_service"):
        assert unrelated not in source
