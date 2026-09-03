"""
Tests for the combined candidate-result normalization pipeline:

    Gemini raw candidate -> test-name normalization -> unit normalization
    -> persisted CandidateResult (verification_status always PENDING)

This does not introduce new normalization logic — it exercises the
existing wiring in
app.services.candidate_extraction_service._persist_completed_extraction /
_normalization_fields, which already calls
app.services.normalization_service and
app.services.unit_normalization_service in that order (see
tests/test_normalization.py and tests/test_unit_normalization.py for
each service's own focused tests; this file is specifically about their
combination on realistic mixed batches).

Mocked DB throughout (unittest.mock), no live PostgreSQL and no live
Gemini API calls anywhere in this file.

Run with:
    pytest backend/tests/test_candidate_normalization_pipeline.py -v
"""
import inspect
import uuid
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


def make_canonical_test(code: str, display_name: str) -> CanonicalTest:
    canonical = CanonicalTest(code=code, display_name=display_name)
    canonical.id = uuid.uuid4()
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
    """A mocked Session whose CanonicalTest lookup always returns
    `canonical_test` — mirrors how test_normalization.py stubs the DB
    for a single-alias-family scenario."""
    db = MagicMock()
    db.query.return_value.filter.return_value.first.return_value = canonical_test
    return db


# ---------------------------------------------------------------------------
# Known test + supported unit -> both resolved
# ---------------------------------------------------------------------------


def test_known_test_and_supported_unit_both_resolve():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = db_resolving(hemoglobin)

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL")],
    )

    result = extraction.results[0]
    assert result.normalization_status == NormalizationStatus.RESOLVED
    assert result.canonical_test_id == hemoglobin.id
    assert result.unit_normalization_status == UnitNormalizationStatus.RESOLVED
    assert result.normalized_value == Decimal("124.0")
    assert result.normalized_unit == "g/L"


def test_known_alias_and_supported_unit_both_resolve():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = db_resolving(hemoglobin)

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("HGB", value="12.4", unit="g/dL")],
    )

    result = extraction.results[0]
    assert result.normalization_status == NormalizationStatus.RESOLVED
    assert result.canonical_test_id == hemoglobin.id
    assert result.unit_normalization_status == UnitNormalizationStatus.RESOLVED
    assert result.normalized_value == Decimal("124.0")


# ---------------------------------------------------------------------------
# Unknown/ambiguous test name -> test-specific conversion never applied
# ---------------------------------------------------------------------------


def test_unknown_test_name_unresolved_and_no_test_specific_conversion():
    db = MagicMock()

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Completely Unknown Test", value="12.4", unit="g/dL")],
    )

    result = extraction.results[0]
    assert result.normalization_status == NormalizationStatus.UNRESOLVED
    assert result.canonical_test_id is None
    # The universal (test-independent) g/dL -> g/L rule still applies —
    # it never required a resolved test identity in the first place.
    # There is no test-specific rule in the current foundation to
    # withhold here, but the outcome must never depend on a guessed
    # test identity.
    assert result.unit_normalization_status == UnitNormalizationStatus.RESOLVED
    assert result.normalized_unit == "g/L"


def test_ambiguous_test_name_and_no_test_specific_conversion():
    db = MagicMock()

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("T3", value="12.4", unit="g/dL")],
    )

    result = extraction.results[0]
    assert result.normalization_status == NormalizationStatus.AMBIGUOUS
    assert result.canonical_test_id is None
    assert result.unit_normalization_status == UnitNormalizationStatus.RESOLVED
    assert result.normalized_unit == "g/L"


# ---------------------------------------------------------------------------
# Known test + unsupported unit -> name resolved, unit unsupported
# ---------------------------------------------------------------------------


def test_known_test_and_unsupported_unit():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = db_resolving(hemoglobin)

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin", value="12.4", unit="furlongs")],
    )

    result = extraction.results[0]
    assert result.normalization_status == NormalizationStatus.RESOLVED
    assert result.unit_normalization_status == UnitNormalizationStatus.UNSUPPORTED
    assert result.normalized_value is None
    assert result.normalized_unit is None


def test_known_test_and_qualitative_value_is_unit_unsupported():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = db_resolving(hemoglobin)

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin", value="Positive", unit="g/dL",
                           evidence="Hemoglobin: Positive")],
    )

    result = extraction.results[0]
    assert result.normalization_status == NormalizationStatus.RESOLVED
    assert result.unit_normalization_status == UnitNormalizationStatus.UNSUPPORTED
    assert result.normalized_value is None


# ---------------------------------------------------------------------------
# Missing unit -> unit unresolved
# ---------------------------------------------------------------------------


def test_missing_unit_is_unit_unresolved():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = db_resolving(hemoglobin)

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("Hemoglobin", value="12.4", unit=None)],
    )

    result = extraction.results[0]
    assert result.normalization_status == NormalizationStatus.RESOLVED
    assert result.unit_normalization_status == UnitNormalizationStatus.UNRESOLVED
    assert result.normalized_value is None
    assert result.normalized_unit is None


# ---------------------------------------------------------------------------
# Raw fields untouched
# ---------------------------------------------------------------------------


def test_raw_test_name_value_and_unit_are_never_overwritten():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = db_resolving(hemoglobin)

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL",
        [make_gemini_item("HGB", value="12.4", unit="g/dL",
                           evidence="HGB 12.4 g/dL", confidence=0.91)],
    )

    result = extraction.results[0]
    assert result.test_name == "HGB"
    assert result.value == "12.4"
    assert result.unit == "g/dL"
    assert result.evidence == "HGB 12.4 g/dL"
    assert result.confidence == 0.91


# ---------------------------------------------------------------------------
# verification_status remains PENDING
# ---------------------------------------------------------------------------


def test_verification_status_remains_pending_across_all_outcomes():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = db_resolving(hemoglobin)

    candidates = [
        make_gemini_item("Hemoglobin", value="12.4", unit="g/dL"),
        make_gemini_item("Unknown Test", value="5", unit=None,
                          evidence="Unknown: 5"),
        make_gemini_item("T3", value="1", unit="furlongs",
                          evidence="T3: 1 furlongs"),
    ]

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL Unknown: 5 T3: 3 g/dL",
        candidates,
    )

    for result in extraction.results:
        assert result.verification_status in (
            None, CandidateVerificationStatus.PENDING
        )
        # RESOLVED normalization must never be conflated with
        # verification (task rule 7).
        assert not hasattr(result, "verified")


# ---------------------------------------------------------------------------
# Mixed batch persists successfully as a whole
# ---------------------------------------------------------------------------


def test_mixed_resolved_unresolved_ambiguous_batch_persists_together():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = db_resolving(hemoglobin)

    candidates = [
        make_gemini_item("Hemoglobin", value="12.4", unit="g/dL"),
        make_gemini_item("Completely Unknown Test", value="5", unit=None,
                          evidence="Unknown: 5"),
        make_gemini_item("T3", value="3", unit="g/dL",
                          evidence="T3: 3 g/dL"),
    ]

    extraction = svc._persist_completed_extraction(
        db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
        "Hemoglobin 12.4 g/dL Unknown: 5 T3: 3 g/dL",
        candidates,
    )

    assert len(extraction.results) == 3
    resolved, unresolved, ambiguous = extraction.results
    assert resolved.normalization_status == NormalizationStatus.RESOLVED
    assert unresolved.normalization_status == NormalizationStatus.UNRESOLVED
    assert ambiguous.normalization_status == NormalizationStatus.AMBIGUOUS
    # One extraction, one commit — a mixed-outcome batch is not split
    # into partial transactions or partial failures.
    db.add.assert_called_once()
    db.commit.assert_called_once()


# ---------------------------------------------------------------------------
# Repeated persistence remains deterministic
# ---------------------------------------------------------------------------


def test_repeated_persistence_of_the_same_candidate_is_deterministic():
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")

    outcomes = []
    for _ in range(3):
        db = db_resolving(hemoglobin)
        extraction = svc._persist_completed_extraction(
            db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
            "HGB 12.4 g/dL",
            [make_gemini_item("HGB", value="12.4", unit="g/dL")],
        )
        result = extraction.results[0]
        outcomes.append(
            (
                result.normalization_status,
                result.unit_normalization_status,
                result.normalized_value,
                result.normalized_unit,
            )
        )

    assert len(set(outcomes)) == 1


# ---------------------------------------------------------------------------
# No Gemini call is introduced by normalization
# ---------------------------------------------------------------------------


def test_normalization_fields_helper_has_no_gemini_dependency():
    source = inspect.getsource(svc._normalization_fields)
    assert "gemini" not in source.lower()


def test_persistence_does_not_call_gemini_extraction_again():
    """_persist_completed_extraction takes already-extracted candidates
    as a parameter — normalization must not trigger any further Gemini
    call while attaching normalization outcomes to them."""
    hemoglobin = make_canonical_test("HEMOGLOBIN", "Hemoglobin")
    db = db_resolving(hemoglobin)

    with_gemini_patched = svc.extract_candidates_from_text
    called = {"count": 0}

    def _tripwire(*args, **kwargs):
        called["count"] += 1
        return with_gemini_patched(*args, **kwargs)

    svc.extract_candidates_from_text = _tripwire
    try:
        svc._persist_completed_extraction(
            db, uuid.uuid4(), ExtractionSourceField.EXTRACTED_TEXT,
            "Hemoglobin 12.4 g/dL",
            [make_gemini_item("Hemoglobin", value="12.4", unit="g/dL")],
        )
    finally:
        svc.extract_candidates_from_text = with_gemini_patched

    assert called["count"] == 0
