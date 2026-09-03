"""
Focused tests for the doctor candidate CORRECT action.

All tests use patched service helpers — no live PostgreSQL.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.models.extraction import (
    AbnormalityStatus,
    CandidateExtraction,
    CandidateResult,
    CandidateVerificationStatus,
    DateNormalizationStatus,
    ExtractionRunStatus,
    ExtractionSourceField,
    NormalizationStatus,
    ReferenceRangeNormalizationStatus,
    TestResult,
    TestResultStatus,
    UnitNormalizationStatus,
)
from app.models.report import IdentityCheckStatus
from app.models.user import User, UserRole
from app.services import correct_candidate_service as svc


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_user(role=UserRole.DOCTOR):
    u = User(full_name="Dr. Test", email="t@e.com", hashed_password="h", role=role)
    u.id = uuid.uuid4()
    u.created_at = datetime.now(timezone.utc)
    u.updated_at = datetime.now(timezone.utc)
    return u


def _make_candidate(status=CandidateVerificationStatus.PENDING):
    c = CandidateResult(
        candidate_extraction_id=uuid.uuid4(),
        test_name="Hemoglobin", value="14.2", unit="g/dL",
        reference_range="12.0-16.0", specimen="Blood",
        result_date="2026-01-15", evidence="Hemoglobin 14.2 g/dL",
        confidence=0.95, verification_status=status,
        normalization_status=NormalizationStatus.RESOLVED,
        canonical_test_id=uuid.uuid4(),
        unit_normalization_status=UnitNormalizationStatus.RESOLVED,
        normalized_value=Decimal("14.2"), normalized_unit="g/dL",
        date_normalization_status=DateNormalizationStatus.RESOLVED,
        normalized_result_date=date(2026, 1, 15),
        reference_range_normalization_status=ReferenceRangeNormalizationStatus.RESOLVED,
        normalized_reference_lower=Decimal("12.0"),
        normalized_reference_upper=Decimal("16.0"),
        reference_range_inclusive_lower=True,
        reference_range_inclusive_upper=True,
        abnormality_status=AbnormalityStatus.NORMAL,
    )
    c.id = uuid.uuid4()
    c.created_at = datetime.now(timezone.utc)
    return c


def _make_extraction(report_id=None):
    e = CandidateExtraction(
        report_id=report_id or uuid.uuid4(),
        status=ExtractionRunStatus.COMPLETED,
        source_field=ExtractionSourceField.EXTRACTED_TEXT,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    e.id = uuid.uuid4()
    e.created_at = datetime.now(timezone.utc)
    return e


def _setup_mocks(candidate, report_id=None):
    """Return (db, doctor, patient, extraction) with all patches applied."""
    doctor = _make_user(UserRole.DOCTOR)
    patient = _make_user(UserRole.PATIENT)
    extraction = _make_extraction(report_id)
    candidate.candidate_extraction_id = extraction.id

    db = MagicMock()
    report = MagicMock(
        id=extraction.report_id,
        patient_id=patient.id,
        identity_check_status=IdentityCheckStatus.MATCH,
        identity_confirmed_by_doctor=False,
    )

    candidate_query = MagicMock()
    candidate_query.join.return_value.filter.return_value.options.return_value.first.return_value = candidate

    canonical_query = MagicMock()
    canonical_query.filter.return_value.first.return_value = None

    report_query = MagicMock()
    report_query.filter.return_value.first.return_value = report

    def query_side_effect(model):
        if model is CandidateResult:
            return candidate_query
        if model is CandidateExtraction:
            return MagicMock()
        return MagicMock()

    db.query.side_effect = query_side_effect

    patches = {
        'verify_patient_exists': patch.object(svc, 'verify_patient_exists', return_value=patient),
        'verify_doctor_access': patch.object(svc, 'verify_doctor_access'),
        'Report': patch.object(svc, 'Report', query=report_query),
        'CanonicalTest': patch.object(svc, 'CanonicalTest', query=canonical_query),
    }

    return db, doctor, patient, extraction, patches


def _run_correction(candidate, data):
    """Run correct_candidate with mocks. Returns (result, db, doctor)."""
    db, doctor, patient, extraction, patches = _setup_mocks(candidate)

    with patches['verify_patient_exists'], \
         patches['verify_doctor_access'], \
         patches['Report'], \
         patches['CanonicalTest']:

        result = svc.correct_candidate(
            db=db,
            doctor_id=doctor.id,
            patient_id=patient.id,
            report_id=extraction.report_id,
            candidate_id=candidate.id,
            correction_data=data,
        )
        return result, db, doctor


# ===========================================================================
# SECTION 1: Reason validation
# ===========================================================================

class TestCorrectionReasonValidation:
    def test_missing_reason_raises(self):
        with pytest.raises(svc.CorrectionReasonRequiredError):
            svc._validate_correction_reason(None)

    def test_empty_reason_raises(self):
        with pytest.raises(svc.CorrectionReasonRequiredError):
            svc._validate_correction_reason("")

    def test_whitespace_only_reason_raises(self):
        with pytest.raises(svc.CorrectionReasonRequiredError):
            svc._validate_correction_reason("   \t\n  ")

    def test_valid_reason_returns_stripped(self):
        assert svc._validate_correction_reason("  Fix  ") == "Fix"

    def test_non_string_reason_raises(self):
        with pytest.raises(svc.CorrectionReasonRequiredError):
            svc._validate_correction_reason(123)


# ===========================================================================
# SECTION 2: Correction data validation
# ===========================================================================

class TestCorrectionDataValidation:
    def test_valid_numeric_values(self):
        svc._validate_numeric_value("14.2", "V")
        svc._validate_numeric_value("-3.5", "V")

    def test_invalid_numeric_raises(self):
        with pytest.raises(svc.InvalidCorrectionError):
            svc._validate_numeric_value("abc", "V")

    def test_empty_numeric_raises(self):
        with pytest.raises(svc.InvalidCorrectionError):
            svc._validate_numeric_value("", "V")

    def test_valid_date_string(self):
        svc._validate_date_string("2026-01-15")

    def test_invalid_date_raises(self):
        with pytest.raises(svc.InvalidCorrectionError):
            svc._validate_date_string("not a date!")

    def test_empty_date_ok(self):
        svc._validate_date_string("")
        svc._validate_date_string("   ")


# ===========================================================================
# SECTION 3: State transition protection
# ===========================================================================

class TestStateTransitionProtection:
    def test_pending_candidate_can_be_corrected(self):
        candidate = _make_candidate()
        result, _, _ = _run_correction(candidate, {"value": "15.0", "reason": "Fix"})

        assert candidate.verification_status == CandidateVerificationStatus.CORRECTED
        assert result["test_result"].status == TestResultStatus.CORRECTED
        assert result["test_result"].raw_value == "15.0"

    def test_verified_candidate_rejected(self):
        candidate = _make_candidate(CandidateVerificationStatus.VERIFIED)
        with pytest.raises(svc.CandidateAlreadyFinalizedError):
            _run_correction(candidate, {"value": "15.0", "reason": "Fix"})

    def test_corrected_candidate_rejected(self):
        candidate = _make_candidate(CandidateVerificationStatus.CORRECTED)
        with pytest.raises(svc.CandidateAlreadyFinalizedError):
            _run_correction(candidate, {"value": "15.0", "reason": "Fix"})


# ===========================================================================
# SECTION 4: Data preservation
# ===========================================================================

class TestDataPreservation:
    def test_original_candidate_values_preserved(self):
        candidate = _make_candidate()
        orig_name = candidate.test_name
        orig_val = candidate.value
        orig_unit = candidate.unit
        orig_range = candidate.reference_range
        orig_date = candidate.result_date
        orig_evidence = candidate.evidence

        _run_correction(candidate, {
            "test_name": "Fixed Hgb", "value": "15.0", "unit": "g/dL",
            "reason": "Fix",
        })

        # Original candidate fields unchanged
        assert candidate.test_name == orig_name
        assert candidate.value == orig_val
        assert candidate.unit == orig_unit
        assert candidate.reference_range == orig_range
        assert candidate.result_date == orig_date
        assert candidate.evidence == orig_evidence

    def test_trusted_result_has_corrected_raw_value(self):
        """TestResult.raw_value should contain the corrected value."""
        candidate = _make_candidate()
        result, _, _ = _run_correction(candidate, {"value": "15.0", "reason": "Fix"})
        assert result["test_result"].raw_value == "15.0"

    def test_trusted_result_normalized_is_recomputed(self):
        """TestResult.normalized_value should be RECOMPUTED from the
        corrected raw value, not copied from the stale candidate."""
        candidate = _make_candidate()
        # Original: 14.2 g/dL → normalized_value=14.2
        result, _, _ = _run_correction(candidate, {
            "value": "15.0", "unit": "g/dL", "reason": "Fix",
        })
        tr = result["test_result"]
        # 15.0 g/dL with the unit normalization rule (g/dL → g/L ×10)
        # normalized_value should be recomputed, not stale 14.2
        assert tr.raw_value == "15.0"
        # The mock CanonicalTest returns None, so the unit normalizer
        # gets canonical_test_code=None. The only rule is g/dL → g/L ×10
        # which is test-independent, so it applies.
        # 15.0 × 10 = 150.0
        assert tr.normalized_value == Decimal("150.0")
        assert tr.normalized_unit == "g/L"


# ===========================================================================
# SECTION 5: Normalization recomputation
# ===========================================================================

class TestNormalizationRecomputation:
    """Test that normalized fields are recomputed from corrected raw values."""

    def test_correct_value_recomputes_normalized_value(self):
        """Changing value recomputes normalized_value via unit normalization."""
        candidate = _make_candidate()
        # Original: value=14.2, normalized_value=14.2 (g/dL → g/L ×10 → 142)
        # Wait, original unit is g/dL, so normalized = 14.2 * 10 = 142 g/L
        # But the mock canonical test returns None, so the unit normalizer
        # gets canonical_test_code=None. The g/dL→g/L rule is universal.
        # So normalized_value for 14.2 g/dL should be 142.0
        # But the test candidate has normalized_value=14.2 (not recomputed during
        # test setup). Let me just check the recomputation for the corrected value.

        result, _, _ = _run_correction(candidate, {
            "value": "20.0", "unit": "g/dL", "reason": "Fix",
        })
        tr = result["test_result"]
        # 20.0 g/dL → normalized: 20.0 × 10 = 200.0 g/L
        assert tr.raw_value == "20.0"
        assert tr.normalized_value == Decimal("200.0")
        assert tr.normalized_unit == "g/L"

    def test_correct_unit_recomputes_normalized_unit(self):
        """Changing unit recomputes normalized_value/normalized_unit."""
        candidate = _make_candidate()
        # Original: 14.2 g/dL. Change to 14.2 g/L (no conversion needed)
        result, _, _ = _run_correction(candidate, {
            "value": "14.2", "unit": "g/L", "reason": "Fix unit",
        })
        tr = result["test_result"]
        # g/L is not a source_unit in the conversion rules, so
        # normalize_unit returns UNSUPPORTED → normalized_value=None
        assert tr.raw_value == "14.2"
        assert tr.normalized_unit is None or tr.normalized_unit != "g/dL"

    def test_correct_test_name_recomputes_canonical_test(self):
        """Changing test_name recomputes the canonical test via normalization."""
        candidate = _make_candidate()
        # Mock the normalization service to simulate HGB resolving to HEMOGLOBIN
        mock_canonical = MagicMock()
        mock_canonical.id = uuid.uuid4()
        mock_canonical.code = "HEMOGLOBIN"
        mock_norm_result = MagicMock()
        mock_norm_result.status = NormalizationStatus.RESOLVED
        mock_norm_result.canonical_test = mock_canonical

        with patch.object(svc, 'normalize_test_name', return_value=mock_norm_result):
            result, _, _ = _run_correction(candidate, {
                "test_name": "HGB", "reason": "Fix test name",
            })
        tr = result["test_result"]
        assert tr.test_name == "HGB"
        assert tr.canonical_test_id == mock_canonical.id

    def test_correct_reference_range_recomputes_bounds(self):
        """Changing reference_range recomputes the numeric bounds."""
        candidate = _make_candidate()
        result, _, _ = _run_correction(candidate, {
            "reference_range": "10.0-20.0", "reason": "Fix range",
        })
        tr = result["test_result"]
        assert tr.reference_range_lower == Decimal("10.0")
        assert tr.reference_range_upper == Decimal("20.0")
        assert tr.reference_range_inclusive_lower is True
        assert tr.reference_range_inclusive_upper is True

    def test_correct_value_and_range_recomputes_abnormality(self):
        """Changing value + reference range recomputes abnormality status."""
        candidate = _make_candidate()
        # Original: value=14.2, range=12.0-16.0 → NORMAL
        # Correct: value=25.0, range=12.0-16.0 → HIGH
        result, _, _ = _run_correction(candidate, {
            "value": "25.0", "reference_range": "12.0-16.0",
            "reason": "Value was wrong",
        })
        tr = result["test_result"]
        # 25.0 g/dL → normalized: 250.0 g/L
        # Range 12.0-16.0 → normalized: 12.0-16.0
        # 250.0 > 16.0 → HIGH
        assert tr.abnormality_status == AbnormalityStatus.HIGH

    def test_correct_result_date_recomputes_normalized_date(self):
        """Changing result_date recomputes the normalized date."""
        candidate = _make_candidate()
        result, _, _ = _run_correction(candidate, {
            "result_date": "2026-06-15", "reason": "Fix date",
        })
        tr = result["test_result"]
        assert tr.result_date == date(2026, 6, 15)

    def test_multiple_corrected_fields_all_consistent(self):
        """Correcting multiple fields produces internally consistent data."""
        candidate = _make_candidate()
        result, _, _ = _run_correction(candidate, {
            "test_name": "Creatinine",
            "value": "1.2",
            "unit": "mg/dL",
            "reference_range": "0.7-1.3",
            "result_date": "2026-03-20",
            "reason": "Multiple corrections",
        })
        tr = result["test_result"]
        # All raw values are corrected
        assert tr.test_name == "Creatinine"
        assert tr.raw_value == "1.2"
        # All normalized values are RECOMPUTED (not stale)
        assert tr.result_date == date(2026, 3, 20)
        assert tr.reference_range_lower == Decimal("0.7")
        assert tr.reference_range_upper == Decimal("1.3")
        # Abnormality is recomputed: 1.2 mg/dL is not convertible via g/dL rule
        # so normalized_value is None → UNRESOLVED
        assert tr.abnormality_status == AbnormalityStatus.UNRESOLVED

    def test_omitted_fields_use_original_raw_values(self):
        """Fields omitted from correction use the original candidate values."""
        candidate = _make_candidate()
        # Only correct value, leave everything else as original
        result, _, _ = _run_correction(candidate, {
            "value": "15.0", "reason": "Only value",
        })
        tr = result["test_result"]
        # Corrected
        assert tr.raw_value == "15.0"
        # Preserved from original
        assert tr.test_name == candidate.test_name
        # Normalized values are RECOMPUTED from the corrected raw + original raw
        # 15.0 g/dL → 150.0 g/L (via universal g/dL→g/L rule)
        assert tr.normalized_value == Decimal("150.0")
        assert tr.normalized_unit == "g/L"
        # Reference range bounds recomputed from original reference_range
        assert tr.reference_range_lower == Decimal("12.0")
        assert tr.reference_range_upper == Decimal("16.0")
        # Abnormality recomputed: 150.0 g/L vs 12.0-16.0 → HIGH
        assert tr.abnormality_status == AbnormalityStatus.HIGH


# ===========================================================================
# SECTION 6: Trusted TestResult metadata
# ===========================================================================

class TestTrustedResultMetadata:
    def test_status_is_corrected(self):
        candidate = _make_candidate()
        result, _, _ = _run_correction(candidate, {"reason": "Fix"})
        assert result["test_result"].status == TestResultStatus.CORRECTED

    def test_correction_note_stored(self):
        candidate = _make_candidate()
        result, _, _ = _run_correction(candidate, {"reason": "Value was wrong"})
        assert result["test_result"].correction_note == "Value was wrong"

    def test_doctor_id_from_auth(self):
        candidate = _make_candidate()
        result, _, doctor = _run_correction(candidate, {"reason": "Fix"})
        assert result["test_result"].doctor_id == doctor.id

    def test_timestamp_is_server_generated(self):
        before = datetime.now(timezone.utc)
        candidate = _make_candidate()
        result, _, _ = _run_correction(candidate, {"reason": "Fix"})
        after = datetime.now(timezone.utc)
        assert before <= result["test_result"].verified_at <= after

    def test_candidate_linkage_preserved(self):
        candidate = _make_candidate()
        result, _, _ = _run_correction(candidate, {"reason": "Fix"})
        tr = result["test_result"]
        assert tr.candidate_result_id == candidate.id
        assert tr.extraction_run_id == candidate.candidate_extraction_id


# ===========================================================================
# SECTION 7: Race safety
# ===========================================================================

class TestRaceSafety:
    def test_integrity_error_triggers_rollback(self):
        candidate = _make_candidate()
        db, doctor, patient, extraction, patches = _setup_mocks(candidate)
        db.commit.side_effect = IntegrityError("t", "t", Exception("dup"))

        with patches['verify_patient_exists'], \
             patches['verify_doctor_access'], \
             patches['Report'], \
             patches['CanonicalTest']:

            with pytest.raises(svc.CandidateAlreadyFinalizedError):
                svc.correct_candidate(
                    db=db, doctor_id=doctor.id, patient_id=patient.id,
                    report_id=extraction.report_id, candidate_id=candidate.id,
                    correction_data={"value": "15.0", "reason": "Fix"},
                )
            db.rollback.assert_called_once()

    def test_sqlalchemy_error_triggers_rollback(self):
        candidate = _make_candidate()
        db, doctor, patient, extraction, patches = _setup_mocks(candidate)
        db.commit.side_effect = SQLAlchemyError("db down")

        with patches['verify_patient_exists'], \
             patches['verify_doctor_access'], \
             patches['Report'], \
             patches['CanonicalTest']:

            with pytest.raises(svc.CorrectError):
                svc.correct_candidate(
                    db=db, doctor_id=doctor.id, patient_id=patient.id,
                    report_id=extraction.report_id, candidate_id=candidate.id,
                    correction_data={"value": "15.0", "reason": "Fix"},
                )
            db.rollback.assert_called_once()


# ===========================================================================
# SECTION 8: Full correction
# ===========================================================================

class TestFullCorrection:
    def test_correct_all_fields(self):
        candidate = _make_candidate()
        result, _, _ = _run_correction(candidate, {
            "test_name": "Fixed Hgb", "value": "15.5", "unit": "g/dL",
            "reference_range": "12.0-17.0", "result_date": "2026-01-16",
            "reason": "Multiple errors",
        })
        tr = result["test_result"]
        assert tr.test_name == "Fixed Hgb"
        assert tr.raw_value == "15.5"
        assert tr.correction_note == "Multiple errors"
        # All normalized values are recomputed
        assert tr.reference_range_lower == Decimal("12.0")
        assert tr.reference_range_upper == Decimal("17.0")
        assert tr.result_date == date(2026, 1, 16)

    def test_partial_correction_preserves_originals(self):
        candidate = _make_candidate()
        orig_name = candidate.test_name
        result, _, _ = _run_correction(candidate, {
            "value": "15.0", "reason": "Only value wrong",
        })
        assert result["test_result"].raw_value == "15.0"
        assert result["test_result"].test_name == orig_name
        # Normalized values are recomputed, not stale
        assert result["test_result"].normalized_value == Decimal("150.0")


# ===========================================================================
# SECTION 9: Regression
# ===========================================================================

class TestRegression:
    def test_verify_service_importable(self):
        from app.services.verify_candidate_service import verify_candidate
        assert callable(verify_candidate)

    def test_enum_values(self):
        assert CandidateVerificationStatus.CORRECTED.value == "corrected"
        assert TestResultStatus.CORRECTED.value == "corrected"

    def test_correction_request_schema(self):
        from app.schemas.correction import CorrectionRequest
        req = CorrectionRequest(value="15.0", reason="Fix")
        assert req.value == "15.0"
        with pytest.raises(Exception):
            CorrectionRequest(value="15.0")  # missing reason

    def test_correct_endpoint_registered(self):
        from app.routers.doctor_reports import router
        paths = [route.path for route in router.routes]
        assert any("correct" in p for p in paths)

    def test_recompute_function_importable(self):
        """The _recompute_normalized_fields helper should be importable."""
        assert callable(svc._recompute_normalized_fields)
