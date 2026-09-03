"""
Tests for the Patient Identity Checkpoint feature.

Covers:
    - Identity extraction (deterministic regex patterns)
    - Identity matching (report vs account)
    - Identity checkpoint service (run, confirm, guard)
    - Backend guard behavior (verify_identity_checkpoint_for_trust)
    - Integration with verify/correct (guard blocks untrusted)
    - State transitions (NOT_CHECKED -> MATCH/MISMATCH/UNRESOLVED)
    - Doctor confirmation (UNRESOLVED only — MISMATCH is a hard block)
    - MISMATCH hard block: never trusted, even with confirmation
    - None/missing identity_check_status handling

All tests use mocked DB or pure unit tests — no live PostgreSQL.
"""
import uuid
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.report import (
    IdentityCheckStatus,
    Report,
    ReportStatus,
)
from app.models.user import User, UserRole
from app.services.identity_extraction_service import (
    ExtractedIdentity,
    extract_patient_identity,
)
from app.services.identity_matching_service import (
    IdentityMatchResult,
    match_identity,
)
from app.services.identity_checkpoint_service import (
    IdentityAlreadyConfirmedError,
    IdentityCheckpointError,
    IdentityCheckNotRunError,
    IdentityMismatchCannotConfirmError,
    IdentityNotConfirmedError,
    confirm_identity_checkpoint,
    run_identity_check,
    verify_identity_checkpoint_for_trust,
)


# ---------------------------------------------------------------------------
# SECTION 1: Identity Extraction (pure unit tests)
# ---------------------------------------------------------------------------

class TestIdentityExtraction:
    """Deterministic regex-based identity extraction from report text."""

    def test_empty_text_returns_none_fields(self):
        result = extract_patient_identity("")
        assert result.patient_name is None
        assert result.patient_dob is None
        assert result.patient_mrn is None

    def test_none_text_returns_none_fields(self):
        result = extract_patient_identity(None)
        assert result.patient_name is None
        assert result.patient_dob is None
        assert result.patient_mrn is None

    def test_whitespace_only_text_returns_none_fields(self):
        result = extract_patient_identity("   \n\t  ")
        assert result.patient_name is None
        assert result.patient_dob is None
        assert result.patient_mrn is None

    def test_patient_name_colon_format(self):
        text = "Patient Name: John Smith\nDOB: 01/15/1980\nMRN: 12345678"
        result = extract_patient_identity(text)
        assert result.patient_name == "John Smith"
        assert result.patient_dob == "01/15/1980"
        assert result.patient_mrn == "12345678"

    def test_name_lowercase_colon_format(self):
        text = "name: Jane Doe"
        result = extract_patient_identity(text)
        assert result.patient_name == "Jane Doe"

    def test_patient_lowercase_colon_format(self):
        text = "patient: Robert Johnson"
        result = extract_patient_identity(text)
        assert result.patient_name == "Robert Johnson"

    def test_dob_format_with_slashes(self):
        text = "DOB: 01/15/1980"
        result = extract_patient_identity(text)
        assert result.patient_dob == "01/15/1980"

    def test_dob_format_with_dashes(self):
        text = "Date of Birth: 1980-01-15"
        result = extract_patient_identity(text)
        assert result.patient_dob == "1980-01-15"

    def test_dob_lowercase(self):
        text = "dob: 03/22/1975"
        result = extract_patient_identity(text)
        assert result.patient_dob == "03/22/1975"

    def test_mrn_format(self):
        text = "MRN: 87654321"
        result = extract_patient_identity(text)
        assert result.patient_mrn == "87654321"

    def test_mrn_with_hash(self):
        text = "MRN# 12345678"
        result = extract_patient_identity(text)
        assert result.patient_mrn == "12345678"

    def test_medical_record_number_format(self):
        text = "Medical Record Number: 99887766"
        result = extract_patient_identity(text)
        assert result.patient_mrn == "99887766"

    def test_no_identity_info_returns_none_fields(self):
        text = "Hemoglobin: 14.2 g/dL\nWBC: 5.0 x10^9/L"
        result = extract_patient_identity(text)
        assert result.patient_name is None
        assert result.patient_dob is None
        assert result.patient_mrn is None

    def test_partial_identity_info(self):
        text = "Patient Name: Alice Smith\nHemoglobin: 14.2 g/dL"
        result = extract_patient_identity(text)
        assert result.patient_name == "Alice Smith"
        assert result.patient_dob is None
        assert result.patient_mrn is None


# ---------------------------------------------------------------------------
# SECTION 2: Identity Matching (pure unit tests)
# ---------------------------------------------------------------------------

class TestIdentityMatching:
    """Deterministic comparison of extracted report identity vs account."""

    def test_no_evidence_is_unresolved(self):
        result = match_identity(
            extracted_name=None,
            extracted_dob=None,
            extracted_mrn=None,
            account_name="John Smith",
        )
        assert result.status == IdentityCheckStatus.UNRESOLVED
        assert "No patient identity" in result.reason

    def test_empty_name_only_is_unresolved(self):
        result = match_identity(
            extracted_name="",
            extracted_dob=None,
            extracted_mrn=None,
            account_name="John Smith",
        )
        assert result.status == IdentityCheckStatus.UNRESOLVED

    def test_exact_name_match(self):
        result = match_identity(
            extracted_name="John Smith",
            extracted_dob=None,
            extracted_mrn=None,
            account_name="John Smith",
        )
        assert result.status == IdentityCheckStatus.MATCH
        assert "matches" in result.reason.lower()

    def test_name_case_insensitive_match(self):
        result = match_identity(
            extracted_name="john smith",
            extracted_dob=None,
            extracted_mrn=None,
            account_name="John Smith",
        )
        assert result.status == IdentityCheckStatus.MATCH

    def test_name_whitespace_insensitive_match(self):
        result = match_identity(
            extracted_name="  John   Smith  ",
            extracted_dob=None,
            extracted_mrn=None,
            account_name="John Smith",
        )
        assert result.status == IdentityCheckStatus.MATCH

    def test_name_mismatch(self):
        result = match_identity(
            extracted_name="Jane Doe",
            extracted_dob=None,
            extracted_mrn=None,
            account_name="John Smith",
        )
        assert result.status == IdentityCheckStatus.MISMATCH
        assert "does not match" in result.reason.lower()

    def test_dob_only_no_name_is_unresolved(self):
        """DOB/MRN without name can't be compared against the account."""
        result = match_identity(
            extracted_name=None,
            extracted_dob="01/15/1980",
            extracted_mrn=None,
            account_name="John Smith",
        )
        assert result.status == IdentityCheckStatus.UNRESOLVED
        assert "insufficient" in result.reason.lower()

    def test_mrn_only_no_name_is_unresolved(self):
        result = match_identity(
            extracted_name=None,
            extracted_dob=None,
            extracted_mrn="12345678",
            account_name="John Smith",
        )
        assert result.status == IdentityCheckStatus.UNRESOLVED

    def test_name_matches_with_dob_present(self):
        """When name matches AND DOB is present, status is MATCH."""
        result = match_identity(
            extracted_name="John Smith",
            extracted_dob="01/15/1980",
            extracted_mrn=None,
            account_name="John Smith",
        )
        assert result.status == IdentityCheckStatus.MATCH

    def test_name_mismatches_with_dob_present(self):
        """When name mismatches AND DOB is present, status is MISMATCH."""
        result = match_identity(
            extracted_name="Jane Doe",
            extracted_dob="01/15/1980",
            extracted_mrn=None,
            account_name="John Smith",
        )
        assert result.status == IdentityCheckStatus.MISMATCH


# ---------------------------------------------------------------------------
# SECTION 3: Identity Checkpoint Service (mocked DB)
# ---------------------------------------------------------------------------

class TestVerifyIdentityCheckpointForTrust:
    """Backend guard that blocks VERIFY/CORRECT when identity
    requirements are not satisfied."""

    def _make_report(self, identity_status, confirmed=False):
        """Create a mock Report with the given identity check status."""
        report = MagicMock()
        report.identity_check_status = identity_status
        report.identity_confirmed_by_doctor = confirmed
        return report

    def test_not_checked_blocks(self):
        report = self._make_report(IdentityCheckStatus.NOT_CHECKED)
        with pytest.raises(IdentityNotConfirmedError):
            verify_identity_checkpoint_for_trust(report)

    def test_none_status_blocks(self):
        """None identity_check_status is treated as NOT_CHECKED."""
        report = self._make_report(None)
        with pytest.raises(IdentityNotConfirmedError):
            verify_identity_checkpoint_for_trust(report)

    def test_match_allows(self):
        report = self._make_report(IdentityCheckStatus.MATCH)
        # Should not raise
        verify_identity_checkpoint_for_trust(report)

    def test_mismatch_without_confirmation_blocks(self):
        report = self._make_report(IdentityCheckStatus.MISMATCH, confirmed=False)
        with pytest.raises(IdentityNotConfirmedError):
            verify_identity_checkpoint_for_trust(report)

    def test_mismatch_with_confirmation_blocks(self):
        """MISMATCH is a hard block — doctor confirmation must NOT override
        a deterministic mismatch."""
        report = self._make_report(IdentityCheckStatus.MISMATCH, confirmed=True)
        with pytest.raises(IdentityNotConfirmedError):
            verify_identity_checkpoint_for_trust(report)

    def test_mismatch_with_manually_true_confirmation_fields_blocks(self):
        """Even if every confirmation field is manually set true, a MISMATCH
        report must still be blocked from becoming trusted data."""
        report = MagicMock()
        report.identity_check_status = IdentityCheckStatus.MISMATCH
        report.identity_confirmed_by_doctor = True
        report.identity_confirmed_by = uuid.uuid4()
        report.identity_confirmed_at = datetime.now(timezone.utc)
        with pytest.raises(IdentityNotConfirmedError, match="mismatch"):
            verify_identity_checkpoint_for_trust(report)

    def test_unresolved_without_confirmation_blocks(self):
        report = self._make_report(IdentityCheckStatus.UNRESOLVED, confirmed=False)
        with pytest.raises(IdentityNotConfirmedError):
            verify_identity_checkpoint_for_trust(report)

    def test_unresolved_with_confirmation_allows(self):
        report = self._make_report(IdentityCheckStatus.UNRESOLVED, confirmed=True)
        # Should not raise
        verify_identity_checkpoint_for_trust(report)

    def test_not_checked_error_message_mentioned_identity(self):
        report = self._make_report(IdentityCheckStatus.NOT_CHECKED)
        with pytest.raises(IdentityNotConfirmedError, match="Identity checkpoint has not been performed"):
            verify_identity_checkpoint_for_trust(report)

    def test_mismatch_error_message_includes_status(self):
        report = self._make_report(IdentityCheckStatus.MISMATCH, confirmed=False)
        with pytest.raises(IdentityNotConfirmedError, match="mismatch"):
            verify_identity_checkpoint_for_trust(report)


class TestRunIdentityCheck:
    """Test the run_identity_check orchestration function with mocked DB."""

    def test_run_identity_check_persists_extracted_values(self):
        """run_identity_check should extract, match, and persist."""
        patient = MagicMock()
        patient.full_name = "John Smith"

        report = MagicMock()
        report.patient_id = uuid.uuid4()
        report.extracted_text = "Patient Name: John Smith\nDOB: 01/15/1980\nMRN: 12345678"
        report.ocr_text = None

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = patient

        with patch(
            "app.services.identity_checkpoint_service.extract_patient_identity"
        ) as mock_extract, patch(
            "app.services.identity_checkpoint_service.match_identity"
        ) as mock_match:
            mock_extract.return_value = ExtractedIdentity(
                patient_name="John Smith",
                patient_dob="01/15/1980",
                patient_mrn="12345678",
            )
            mock_match.return_value = IdentityMatchResult(
                status=IdentityCheckStatus.MATCH,
                reason="Name matches.",
            )

            result = run_identity_check(db, report)

        # Verify extracted values were set on the report
        assert report.patient_name_extracted == "John Smith"
        assert report.patient_dob_extracted == "01/15/1980"
        assert report.patient_mrn_extracted == "12345678"
        assert report.identity_check_status == IdentityCheckStatus.MATCH

        # Verify commit was called
        db.commit.assert_called_once()

    def test_run_identity_check_handles_ocr_text_fallback(self):
        """When extracted_text is None, should fall back to ocr_text."""
        patient = MagicMock()
        patient.full_name = "John Smith"

        report = MagicMock()
        report.patient_id = uuid.uuid4()
        report.extracted_text = None
        report.ocr_text = "Patient Name: John Smith"

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = patient

        with patch(
            "app.services.identity_checkpoint_service.extract_patient_identity"
        ) as mock_extract, patch(
            "app.services.identity_checkpoint_service.match_identity"
        ) as mock_match:
            mock_extract.return_value = ExtractedIdentity(patient_name="John Smith")
            mock_match.return_value = IdentityMatchResult(
                status=IdentityCheckStatus.MATCH,
                reason="Name matches.",
            )

            run_identity_check(db, report)

        # Should have been called with the ocr_text
        mock_extract.assert_called_once_with("Patient Name: John Smith")

    def test_run_identity_check_mismatch_persists(self):
        """Mismatch identity should be persisted."""
        patient = MagicMock()
        patient.full_name = "John Smith"

        report = MagicMock()
        report.patient_id = uuid.uuid4()
        report.extracted_text = "Patient Name: Jane Doe"
        report.ocr_text = None

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = patient

        with patch(
            "app.services.identity_checkpoint_service.extract_patient_identity"
        ) as mock_extract, patch(
            "app.services.identity_checkpoint_service.match_identity"
        ) as mock_match:
            mock_extract.return_value = ExtractedIdentity(patient_name="Jane Doe")
            mock_match.return_value = IdentityMatchResult(
                status=IdentityCheckStatus.MISMATCH,
                reason="Name does not match.",
            )

            result = run_identity_check(db, report)

        assert report.identity_check_status == IdentityCheckStatus.MISMATCH
        db.commit.assert_called_once()


class TestConfirmIdentityCheckpoint:
    """Test the doctor confirmation endpoint logic."""

    def _setup(self, identity_status=IdentityCheckStatus.UNRESOLVED, confirmed=False):
        """Set up mocks for confirm_identity_checkpoint test."""
        doctor = MagicMock()
        doctor.id = uuid.uuid4()
        doctor.role = UserRole.DOCTOR

        patient_id = uuid.uuid4()
        report_id = uuid.uuid4()

        report = MagicMock()
        report.id = report_id
        report.patient_id = patient_id
        report.identity_check_status = identity_status
        report.identity_confirmed_by_doctor = confirmed

        db = MagicMock()
        # verify_patient_exists and verify_doctor_access are patched in
        # every test, so the only db.query consumer is the report lookup.
        db.query.return_value.filter.return_value.first.return_value = report

        return db, doctor, patient_id, report_id, report

    @patch("app.services.identity_checkpoint_service.verify_doctor_access")
    @patch("app.services.identity_checkpoint_service.verify_patient_exists")
    def test_confirm_sets_flags(self, mock_verify_patient, mock_verify_access):
        db, doctor, patient_id, report_id, report = self._setup(
            identity_status=IdentityCheckStatus.UNRESOLVED
        )

        result = confirm_identity_checkpoint(db, doctor.id, patient_id, report_id)

        assert result.identity_confirmed_by_doctor is True
        assert result.identity_confirmed_by == doctor.id
        assert result.identity_confirmed_at is not None
        db.commit.assert_called_once()

    @patch("app.services.identity_checkpoint_service.verify_doctor_access")
    @patch("app.services.identity_checkpoint_service.verify_patient_exists")
    def test_confirm_not_checked_raises(self, mock_verify_patient, mock_verify_access):
        doctor = MagicMock()
        doctor.id = uuid.uuid4()
        doctor.role = UserRole.DOCTOR

        patient_id = uuid.uuid4()
        report_id = uuid.uuid4()

        report = MagicMock()
        report.id = report_id
        report.patient_id = patient_id
        report.identity_check_status = IdentityCheckStatus.NOT_CHECKED
        report.identity_confirmed_by_doctor = False

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = report

        with pytest.raises(IdentityCheckNotRunError):
            confirm_identity_checkpoint(db, doctor.id, patient_id, report_id)

    @patch("app.services.identity_checkpoint_service.verify_doctor_access")
    @patch("app.services.identity_checkpoint_service.verify_patient_exists")
    def test_confirm_unresolved_allows(self, mock_verify_patient, mock_verify_access):
        db, doctor, patient_id, report_id, report = self._setup(
            identity_status=IdentityCheckStatus.UNRESOLVED
        )

        result = confirm_identity_checkpoint(db, doctor.id, patient_id, report_id)
        assert result.identity_confirmed_by_doctor is True

    @patch("app.services.identity_checkpoint_service.verify_doctor_access")
    @patch("app.services.identity_checkpoint_service.verify_patient_exists")
    def test_confirm_mismatch_raises(self, mock_verify_patient, mock_verify_access):
        """The confirmation endpoint must reject a MISMATCH outright — a
        deterministic mismatch can never be confirmed as a trust override."""
        db, doctor, patient_id, report_id, report = self._setup(
            identity_status=IdentityCheckStatus.MISMATCH
        )

        with pytest.raises(IdentityMismatchCannotConfirmError):
            confirm_identity_checkpoint(db, doctor.id, patient_id, report_id)
        # Nothing is persisted when confirmation is rejected.
        db.commit.assert_not_called()


# ---------------------------------------------------------------------------
# SECTION 4: Integration — guard blocks verify/correct without identity
# ---------------------------------------------------------------------------

class TestIdentityGuardIntegration:
    """Verify that verify/correct services respect the identity guard."""

    def test_verify_blocks_when_identity_not_checked(self):
        """verify_candidate should raise VerifyError when identity is NOT_CHECKED."""
        from app.services.verify_candidate_service import verify_candidate, VerifyError

        doctor = MagicMock()
        doctor.id = uuid.uuid4()
        doctor.role = UserRole.DOCTOR

        patient = MagicMock()
        patient.id = uuid.uuid4()

        report = MagicMock()
        report.patient_id = patient.id
        report.identity_check_status = IdentityCheckStatus.NOT_CHECKED
        report.identity_confirmed_by_doctor = False

        candidate = MagicMock()
        candidate.id = uuid.uuid4()
        candidate.verification_status = "pending"
        candidate.candidate_extraction_id = uuid.uuid4()

        db = MagicMock()

        # Mock the query chain
        def query_side_effect(model):
            m = MagicMock()
            if hasattr(model, '__tablename__'):
                if model.__tablename__ == 'users':
                    m.filter.return_value.first.return_value = patient
                elif model.__tablename__ == 'reports':
                    m.filter.return_value.first.return_value = report
                elif model.__tablename__ == 'candidate_results':
                    m.join.return_value.filter.return_value.options.return_value.first.return_value = candidate
            return m

        db.query.side_effect = query_side_effect

        with patch("app.services.verify_candidate_service.verify_patient_exists"), \
             patch("app.services.verify_candidate_service.verify_doctor_access"):
            with pytest.raises(VerifyError, match="Identity checkpoint"):
                verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)

    def test_verify_blocks_when_identity_mismatch_confirmed(self):
        """verify_candidate must remain blocked on a MISMATCH even when
        every confirmation field is set — MISMATCH is a hard block that
        confirmation can never override."""
        from app.services.verify_candidate_service import verify_candidate, VerifyError

        doctor = MagicMock()
        doctor.id = uuid.uuid4()
        doctor.role = UserRole.DOCTOR

        patient = MagicMock()
        patient.id = uuid.uuid4()

        report = MagicMock()
        report.patient_id = patient.id
        report.identity_check_status = IdentityCheckStatus.MISMATCH
        report.identity_confirmed_by_doctor = True
        report.identity_confirmed_by = uuid.uuid4()
        report.identity_confirmed_at = datetime.now(timezone.utc)

        candidate = MagicMock()
        candidate.id = uuid.uuid4()
        candidate.verification_status = "pending"
        candidate.candidate_extraction_id = uuid.uuid4()

        db = MagicMock()

        def query_side_effect(model):
            m = MagicMock()
            if hasattr(model, '__tablename__'):
                if model.__tablename__ == 'users':
                    m.filter.return_value.first.return_value = patient
                elif model.__tablename__ == 'reports':
                    m.filter.return_value.first.return_value = report
                elif model.__tablename__ == 'candidate_results':
                    m.join.return_value.filter.return_value.options.return_value.first.return_value = candidate
            return m

        db.query.side_effect = query_side_effect

        with patch("app.services.verify_candidate_service.verify_patient_exists"), \
             patch("app.services.verify_candidate_service.verify_doctor_access"):
            with pytest.raises(VerifyError, match="mismatch"):
                verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)

    def test_correct_blocks_when_identity_not_checked(self):
        """correct_candidate should raise CorrectError when identity is NOT_CHECKED."""
        from app.services.correct_candidate_service import correct_candidate, CorrectError

        doctor = MagicMock()
        doctor.id = uuid.uuid4()
        doctor.role = UserRole.DOCTOR

        patient = MagicMock()
        patient.id = uuid.uuid4()

        report = MagicMock()
        report.patient_id = patient.id
        report.identity_check_status = IdentityCheckStatus.NOT_CHECKED
        report.identity_confirmed_by_doctor = False

        candidate = MagicMock()
        candidate.id = uuid.uuid4()
        candidate.verification_status = "pending"
        candidate.candidate_extraction_id = uuid.uuid4()
        candidate.test_name = "Hemoglobin"
        candidate.value = "14.2"

        db = MagicMock()

        def query_side_effect(model):
            m = MagicMock()
            if hasattr(model, '__tablename__'):
                if model.__tablename__ == 'users':
                    m.filter.return_value.first.return_value = patient
                elif model.__tablename__ == 'reports':
                    m.filter.return_value.first.return_value = report
                elif model.__tablename__ == 'candidate_results':
                    m.join.return_value.filter.return_value.options.return_value.first.return_value = candidate
            return m

        db.query.side_effect = query_side_effect

        with patch("app.services.correct_candidate_service.verify_patient_exists"), \
             patch("app.services.correct_candidate_service.verify_doctor_access"):
            with pytest.raises(CorrectError, match="Identity checkpoint"):
                correct_candidate(
                    db, doctor.id, patient.id, report.id, candidate.id,
                    correction_data={"reason": "Fix value"},
                )

    def test_reject_does_not_use_identity_guard(self):
        """REJECT should NOT require identity checkpoint — rejected candidates
        never become trusted data, so the identity guard is irrelevant."""
        from app.services.reject_candidate_service import reject_candidate

        doctor = MagicMock()
        doctor.id = uuid.uuid4()
        doctor.role = UserRole.DOCTOR

        patient = MagicMock()
        patient.id = uuid.uuid4()

        report = MagicMock()
        report.patient_id = patient.id
        report.identity_check_status = IdentityCheckStatus.NOT_CHECKED

        candidate = MagicMock()
        candidate.id = uuid.uuid4()
        candidate.verification_status = "pending"
        candidate.candidate_extraction_id = uuid.uuid4()

        db = MagicMock()

        def query_side_effect(model):
            m = MagicMock()
            if hasattr(model, '__tablename__'):
                if model.__tablename__ == 'users':
                    m.filter.return_value.first.return_value = patient
                elif model.__tablename__ == 'reports':
                    m.filter.return_value.first.return_value = report
                elif model.__tablename__ == 'candidate_results':
                    m.join.return_value.filter.return_value.options.return_value.options.return_value.first.return_value = candidate
            return m

        db.query.side_effect = query_side_effect

        with patch("app.services.reject_candidate_service.verify_patient_exists"), \
             patch("app.services.reject_candidate_service.verify_doctor_access"):
            # Should succeed even with NOT_CHECKED identity
            result = reject_candidate(
                db, doctor.id, patient.id, report.id, candidate.id,
                reason="Incorrect extraction",
            )
            assert result["candidate"] is candidate
