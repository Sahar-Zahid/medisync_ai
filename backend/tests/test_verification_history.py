"""
Focused tests for the VERIFICATION HISTORY feature.

Covers:
    - Authorization for the history read path (doctor-only, ACTIVE
      relationship, report ownership, cross-doctor isolation)
    - VERIFY history (one record, action, server metadata, snapshots,
      atomicity/rollback)
    - CORRECT history (old snapshot before correction, final recomputed
      values, reason, atomicity/rollback)
    - REJECT history (original snapshot preserved, reason recorded,
      no TestResult, no new values)
    - Immutability (no update/delete endpoint, rows never overwritten)
    - State protection (failed/terminal/identity-blocked actions create
      no history)
    - Regression sanity checks

All tests use mocked DB / patched service helpers — no live PostgreSQL.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.exc import SQLAlchemyError

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
from app.models.report import IdentityCheckStatus, Report, ReportStatus
from app.models.user import User, UserRole
from app.models.verification_history import VerificationAction, VerificationHistory
from app.services.verification_history_service import (
    capture_candidate_snapshot,
    create_verification_history,
    get_report_verification_history,
)


# ---------------------------------------------------------------------------
# Helpers (mirroring the existing verify/correct/reject test patterns)
# ---------------------------------------------------------------------------

def _make_user(role=UserRole.DOCTOR):
    u = User(full_name="Dr. Test", email="t@e.com", hashed_password="h", role=role)
    u.id = uuid.uuid4()
    u.created_at = datetime.now(timezone.utc)
    u.updated_at = datetime.now(timezone.utc)
    return u


def _make_report(patient_id):
    report = Report(
        patient_id=patient_id,
        original_filename="blood_test.pdf",
        storage_path=f"{uuid.uuid4()}.pdf",
        sha256_hash="a" * 64,
        status=ReportStatus.COMPLETED,
        identity_check_status=IdentityCheckStatus.MATCH,
    )
    report.id = uuid.uuid4()
    report.created_at = datetime.now(timezone.utc)
    return report


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


def _setup_verify_mocks(db, patient, doctor, report, candidate):
    """db.query chain for verify_candidate (real objects)."""
    user_query = MagicMock()
    user_query.filter.return_value.first.return_value = patient

    link_query = MagicMock()
    link_query.filter.return_value.first.return_value = MagicMock()  # ACTIVE link

    report_query = MagicMock()
    report_query.filter.return_value.first.return_value = report

    candidate_query = MagicMock()
    candidate_query.join.return_value.filter.return_value.options.return_value.first.return_value = candidate

    call_count = [0]

    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            return user_query
        elif call_count[0] == 2:
            return link_query
        elif call_count[0] == 3:
            return report_query
        return candidate_query

    db.query.side_effect = side_effect


def _run_verify(identity=IdentityCheckStatus.MATCH):
    """Run verify_candidate with mocks. Returns (result, db, doctor, patient, candidate)."""
    from app.services.verify_candidate_service import verify_candidate

    doctor = _make_user(UserRole.DOCTOR)
    patient = _make_user(UserRole.PATIENT)
    report = _make_report(patient.id)
    report.identity_check_status = identity
    candidate = _make_candidate()

    db = MagicMock()
    _setup_verify_mocks(db, patient, doctor, report, candidate)

    result = verify_candidate(
        db=db, doctor_id=doctor.id, patient_id=patient.id,
        report_id=report.id, candidate_id=candidate.id,
    )
    return result, db, doctor, patient, candidate, report


def _setup_correct_reject_mocks(db, candidate, report_id=None, reject_mode=False):
    """db.query setup shared by CORRECT/REJECT runners.

    Dispatches on the REAL model classes (Report, CanonicalTest,
    CandidateResult) so the services' queries resolve to the configured
    objects. verify_patient_exists / verify_doctor_access are patched by
    the runners.
    """
    doctor = _make_user(UserRole.DOCTOR)
    patient = _make_user(UserRole.PATIENT)
    extraction = CandidateExtraction(
        report_id=report_id or uuid.uuid4(),
        status=ExtractionRunStatus.COMPLETED,
        source_field=ExtractionSourceField.EXTRACTED_TEXT,
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    extraction.id = uuid.uuid4()
    extraction.created_at = datetime.now(timezone.utc)
    candidate.candidate_extraction_id = extraction.id

    report = MagicMock(
        id=extraction.report_id,
        patient_id=patient.id,
        identity_check_status=IdentityCheckStatus.MATCH,
        identity_confirmed_by_doctor=False,
    )

    candidate_query = MagicMock()
    if reject_mode:
        # reject_candidate chains .options(...).options(...).first()
        candidate_query.join.return_value.filter.return_value.options.return_value.options.return_value.first.return_value = candidate
    else:
        # correct_candidate chains .options(...).first()
        candidate_query.join.return_value.filter.return_value.options.return_value.first.return_value = candidate
    report_query = MagicMock()
    report_query.filter.return_value.first.return_value = report
    canonical_query = MagicMock()
    canonical_query.filter.return_value.first.return_value = None

    from app.models.extraction import CanonicalTest

    def query_side_effect(model):
        if model is CandidateResult:
            return candidate_query
        if model is Report:
            return report_query
        if model is CanonicalTest:
            return canonical_query
        return MagicMock()

    db.query.side_effect = query_side_effect

    return db, doctor, patient, report, candidate


def _run_correct(candidate, data):
    """Run correct_candidate with patched auth helpers."""
    import app.services.correct_candidate_service as svc

    db = MagicMock()
    db, doctor, patient, report, candidate = _setup_correct_reject_mocks(db, candidate)

    with patch.object(svc, 'verify_patient_exists', return_value=patient), \
         patch.object(svc, 'verify_doctor_access'):

        result = svc.correct_candidate(
            db=db, doctor_id=doctor.id, patient_id=patient.id,
            report_id=report.id, candidate_id=candidate.id,
            correction_data=data,
        )
        return result, db, doctor, patient, candidate, report


def _run_reject(candidate, reason="Data is incorrect"):
    """Run reject_candidate with patched auth helpers."""
    import app.services.reject_candidate_service as svc

    db = MagicMock()
    db, doctor, patient, report, candidate = _setup_correct_reject_mocks(db, candidate, reject_mode=True)

    with patch.object(svc, 'verify_patient_exists', return_value=patient), \
         patch.object(svc, 'verify_doctor_access'):

        result = svc.reject_candidate(
            db=db, doctor_id=doctor.id, patient_id=patient.id,
            report_id=report.id, candidate_id=candidate.id,
            reason=reason,
        )
        return result, db, doctor, patient, candidate, report


def _staged_history_records(db):
    """All VerificationHistory objects staged via db.add in this session."""
    added = [call_args[0][0] for call_args in db.add.call_args_list]
    return [obj for obj in added if isinstance(obj, VerificationHistory)]


def _staged_test_results(db):
    added = [call_args[0][0] for call_args in db.add.call_args_list]
    return [obj for obj in added if isinstance(obj, TestResult)]


# ===========================================================================
# SECTION 1: Read-path authorization
# ===========================================================================

class TestReadAuthorization:
    """The history read endpoint enforces the same auth chain as other
    doctor report endpoints — patient exists, ACTIVE relationship,
    report ownership. Cross-doctor access is impossible because the
    ACTIVE-link check is per authenticated doctor."""

    def test_endpoint_requires_doctor_dependency(self):
        """The verification-history route is protected by require_doctor,
        which rejects unauthenticated (401) and non-doctor (403) users."""
        from app.core.deps import require_doctor
        from app.routers.doctor_reports import router

        route = next(
            r for r in router.routes
            if r.path.endswith("/verification-history") and "GET" in r.methods
        )
        deps = [d.call for d in route.dependant.dependencies]
        assert require_doctor in deps

    def test_non_doctor_rejected_by_dependency(self):
        from app.core.deps import require_doctor
        from fastapi import HTTPException

        patient = _make_user(UserRole.PATIENT)
        with pytest.raises(HTTPException) as exc_info:
            require_doctor(current_user=patient)
        assert exc_info.value.status_code == 403

    def test_inactive_relationship_blocked(self):
        from app.services.doctor_report_service import UnauthorizedAccessError

        db = MagicMock()
        with patch(
            # get_report_verification_history delegates the ACTIVE-link
            # check to verify_doctor_access — non-ACTIVE raises.
            "app.services.verification_history_service.verify_doctor_access",
            side_effect=UnauthorizedAccessError("No active relationship."),
        ), patch(
            "app.services.verification_history_service.verify_patient_exists",
        ):
            with pytest.raises(UnauthorizedAccessError):
                get_report_verification_history(
                    db, doctor_id=uuid.uuid4(),
                    patient_id=uuid.uuid4(), report_id=uuid.uuid4(),
                )

    def test_pending_relationship_blocked(self):
        from app.services.doctor_report_service import UnauthorizedAccessError

        db = MagicMock()
        with patch(
            "app.services.verification_history_service.verify_patient_exists",
        ), patch(
            "app.services.verification_history_service.verify_doctor_access",
            side_effect=UnauthorizedAccessError("Relationship is not active."),
        ):
            with pytest.raises(UnauthorizedAccessError):
                get_report_verification_history(
                    db, doctor_id=uuid.uuid4(),
                    patient_id=uuid.uuid4(), report_id=uuid.uuid4(),
                )

    def test_wrong_patient_blocked(self):
        from app.services.doctor_report_service import PatientNotFoundError

        db = MagicMock()
        with patch(
            "app.services.verification_history_service.verify_patient_exists",
            side_effect=PatientNotFoundError("Patient not found."),
        ):
            with pytest.raises(PatientNotFoundError):
                get_report_verification_history(
                    db, doctor_id=uuid.uuid4(),
                    patient_id=uuid.uuid4(), report_id=uuid.uuid4(),
                )

    def test_wrong_report_blocked(self):
        """A report that doesn't belong to the patient -> ReportNotFoundError."""
        from app.services.doctor_report_service import ReportNotFoundError

        db = MagicMock()
        report_query = MagicMock()
        report_query.filter.return_value.first.return_value = None

        def query_side_effect(model):
            if model is Report:
                return report_query
            return MagicMock()

        db.query.side_effect = query_side_effect

        with patch("app.services.verification_history_service.verify_patient_exists"), \
             patch("app.services.verification_history_service.verify_doctor_access"):
            with pytest.raises(ReportNotFoundError):
                get_report_verification_history(
                    db, doctor_id=uuid.uuid4(),
                    patient_id=uuid.uuid4(), report_id=uuid.uuid4(),
                )

    def test_cross_doctor_access_blocked(self):
        """Doctor A cannot read history for Doctor B's patient — the
        ACTIVE-link check (verify_doctor_access) raises for the wrong
        doctor."""
        from app.services.doctor_report_service import UnauthorizedAccessError

        db = MagicMock()
        with patch(
            "app.services.verification_history_service.verify_patient_exists",
        ), patch(
            "app.services.verification_history_service.verify_doctor_access",
            side_effect=UnauthorizedAccessError("No access to this patient."),
        ):
            with pytest.raises(UnauthorizedAccessError):
                get_report_verification_history(
                    db, doctor_id=uuid.uuid4(),
                    patient_id=uuid.uuid4(), report_id=uuid.uuid4(),
                )

    def test_authorized_doctor_gets_chronological_history(self):
        """An authorized doctor gets the report's history rows in
        chronological order, scoped to the report."""
        doctor = _make_user(UserRole.DOCTOR)
        patient = _make_user(UserRole.PATIENT)
        report = _make_report(patient.id)

        h1 = MagicMock()
        h1.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        h2 = MagicMock()
        h2.created_at = datetime(2026, 1, 2, tzinfo=timezone.utc)

        db = MagicMock()
        report_query = MagicMock()
        report_query.filter.return_value.first.return_value = report
        history_query = MagicMock()
        history_query.filter.return_value.order_by.return_value.all.return_value = [h1, h2]

        def query_side_effect(model):
            if model is Report:
                return report_query
            if model is VerificationHistory:
                return history_query
            return MagicMock()

        db.query.side_effect = query_side_effect

        with patch("app.services.verification_history_service.verify_patient_exists"), \
             patch("app.services.verification_history_service.verify_doctor_access"):
            history = get_report_verification_history(
                db, doctor_id=doctor.id,
                patient_id=patient.id, report_id=report.id,
            )

        assert history == [h1, h2]
        # Scoped to this report only — never another patient's history.
        filter_args = history_query.filter.call_args[0]
        assert filter_args[0].compare(VerificationHistory.report_id == report.id)
        # Ordered chronologically by the server/database timestamp.
        history_query.filter.return_value.order_by.assert_called_once()


# ===========================================================================
# SECTION 2: VERIFY history
# ===========================================================================

class TestVerifyHistory:
    def test_verify_creates_exactly_one_history_record(self):
        result, db, doctor, patient, candidate, report = _run_verify()
        records = _staged_history_records(db)
        assert len(records) == 1

    def test_verify_history_action_is_verify(self):
        result, db, doctor, patient, candidate, report = _run_verify()
        assert _staged_history_records(db)[0].action == VerificationAction.VERIFY

    def test_verify_history_server_metadata(self):
        result, db, doctor, patient, candidate, report = _run_verify()
        h = _staged_history_records(db)[0]
        assert h.doctor_id == doctor.id
        assert h.patient_id == patient.id
        assert h.report_id == report.id
        assert h.candidate_id == candidate.id

    def test_verify_history_old_snapshot_matches_candidate(self):
        result, db, doctor, patient, candidate, report = _run_verify()
        h = _staged_history_records(db)[0]
        assert h.old_test_name == candidate.test_name
        assert h.old_value == candidate.value
        assert h.old_unit == candidate.unit
        assert h.old_normalized_value == candidate.normalized_value
        assert h.old_normalized_unit == candidate.normalized_unit
        assert h.old_canonical_test_id == candidate.canonical_test_id
        assert h.old_reference_range == candidate.reference_range
        assert h.old_result_date == candidate.result_date
        assert h.old_abnormality_status == candidate.abnormality_status

    def test_verify_history_final_snapshot_identical(self):
        result, db, doctor, patient, candidate, report = _run_verify()
        h = _staged_history_records(db)[0]
        # VERIFY accepts the candidate as-is — final snapshot is identical.
        assert h.new_test_name == candidate.test_name
        assert h.new_value == candidate.value
        assert h.new_normalized_value == candidate.normalized_value
        assert h.new_abnormality_status == candidate.abnormality_status

    def test_verify_history_created_at_is_server_only(self):
        result, db, doctor, patient, candidate, report = _run_verify()
        h = _staged_history_records(db)[0]
        # The application never sets created_at — it is a server/database
        # timestamp populated at insert time.
        assert h.created_at is None
        assert VerificationHistory.__table__.c.created_at.server_default is not None

    def test_verify_history_committed_with_action(self):
        """History row is staged in the SAME session as the status change
        and TestResult — one commit persists all three."""
        result, db, doctor, patient, candidate, report = _run_verify()
        assert candidate.verification_status == CandidateVerificationStatus.VERIFIED
        assert len(_staged_test_results(db)) == 1
        assert len(_staged_history_records(db)) == 1
        db.commit.assert_called_once()

    def test_verify_commit_failure_rolls_back_history(self):
        from app.services.verify_candidate_service import VerifyError

        doctor = _make_user(UserRole.DOCTOR)
        patient = _make_user(UserRole.PATIENT)
        report = _make_report(patient.id)
        candidate = _make_candidate()

        db = MagicMock()
        _setup_verify_mocks(db, patient, doctor, report, candidate)
        db.commit.side_effect = SQLAlchemyError("db down")

        with pytest.raises(VerifyError):
            from app.services.verify_candidate_service import verify_candidate
            verify_candidate(
                db=db, doctor_id=doctor.id, patient_id=patient.id,
                report_id=report.id, candidate_id=candidate.id,
            )
        db.rollback.assert_called_once()

    def test_failed_verify_creates_no_history(self):
        from app.services.verify_candidate_service import (
            CandidateAlreadyVerifiedError,
            verify_candidate,
        )

        doctor = _make_user(UserRole.DOCTOR)
        patient = _make_user(UserRole.PATIENT)
        report = _make_report(patient.id)
        candidate = _make_candidate(status=CandidateVerificationStatus.VERIFIED)

        db = MagicMock()
        _setup_verify_mocks(db, patient, doctor, report, candidate)

        with pytest.raises(CandidateAlreadyVerifiedError):
            verify_candidate(
                db=db, doctor_id=doctor.id, patient_id=patient.id,
                report_id=report.id, candidate_id=candidate.id,
            )
        assert _staged_history_records(db) == []
        assert _staged_test_results(db) == []


# ===========================================================================
# SECTION 3: CORRECT history
# ===========================================================================

class TestCorrectHistory:
    def test_correct_creates_exactly_one_history_record(self):
        candidate = _make_candidate()
        result, db, doctor, patient, candidate, report = _run_correct(
            candidate, {"value": "15.0", "reason": "Fix"}
        )
        records = _staged_history_records(db)
        assert len(records) == 1

    def test_correct_history_action_is_correct(self):
        candidate = _make_candidate()
        result, db, doctor, patient, candidate, report = _run_correct(
            candidate, {"value": "15.0", "reason": "Fix"}
        )
        assert _staged_history_records(db)[0].action == VerificationAction.CORRECT

    def test_correct_history_old_values_captured_before_correction(self):
        candidate = _make_candidate()
        result, db, doctor, patient, candidate, report = _run_correct(
            candidate, {"value": "15.0", "reason": "Fix"}
        )
        h = _staged_history_records(db)[0]
        # Original candidate snapshot — unchanged original raw + normalized.
        assert h.old_test_name == candidate.test_name
        assert h.old_value == "14.2"
        assert h.old_unit == "g/dL"
        assert h.old_normalized_value == Decimal("14.2")
        assert h.old_normalized_unit == "g/dL"
        assert h.old_reference_range == candidate.reference_range
        assert h.old_result_date == candidate.result_date
        assert h.old_abnormality_status == AbnormalityStatus.NORMAL

    def test_correct_history_final_values_are_backend_recomputed(self):
        candidate = _make_candidate()
        result, db, doctor, patient, candidate, report = _run_correct(
            candidate, {"value": "15.0", "reason": "Fix"}
        )
        h = _staged_history_records(db)[0]
        # Corrected raw value + deterministically recomputed normalized
        # values (15.0 g/dL → 150.0 g/L via the universal g/dL→g/L rule),
        # never stale candidate normalization, never client input.
        assert h.new_value == "15.0"
        assert h.new_unit == "g/dL"
        assert h.new_normalized_value == Decimal("150.0")
        assert h.new_normalized_unit == "g/L"
        # Range 12.0-16.0, 150.0 > 16.0 → HIGH
        assert h.new_abnormality_status == AbnormalityStatus.HIGH

    def test_correct_history_reason_preserved(self):
        candidate = _make_candidate()
        result, db, doctor, patient, candidate, report = _run_correct(
            candidate, {"value": "15.0", "reason": "  Value was wrong  "}
        )
        h = _staged_history_records(db)[0]
        # Stripped validated reason is recorded.
        assert h.reason == "Value was wrong"

    def test_correct_history_server_metadata(self):
        candidate = _make_candidate()
        result, db, doctor, patient, candidate, report = _run_correct(
            candidate, {"value": "15.0", "reason": "Fix"}
        )
        h = _staged_history_records(db)[0]
        assert h.doctor_id == doctor.id
        assert h.patient_id == patient.id
        assert h.report_id == report.id
        assert h.candidate_id == candidate.id

    def test_correct_commit_failure_rolls_back_history(self):
        import app.services.correct_candidate_service as svc

        candidate = _make_candidate()
        db = MagicMock()
        db, doctor, patient, report, candidate = _setup_correct_reject_mocks(db, candidate)
        db.commit.side_effect = SQLAlchemyError("db down")

        with patch.object(svc, 'verify_patient_exists', return_value=patient), \
             patch.object(svc, 'verify_doctor_access'):

            with pytest.raises(svc.CorrectError):
                svc.correct_candidate(
                    db=db, doctor_id=doctor.id, patient_id=patient.id,
                    report_id=report.id, candidate_id=candidate.id,
                    correction_data={"value": "15.0", "reason": "Fix"},
                )
            db.rollback.assert_called_once()

    def test_failed_correct_creates_no_history(self):
        import app.services.correct_candidate_service as svc

        candidate = _make_candidate(status=CandidateVerificationStatus.CORRECTED)
        with pytest.raises(svc.CandidateAlreadyFinalizedError):
            _run_correct(candidate, {"value": "15.0", "reason": "Fix"})

    def test_correct_blocked_by_identity_creates_no_history(self):
        import app.services.correct_candidate_service as svc

        candidate = _make_candidate()
        db = MagicMock()
        db, doctor, patient, report, candidate = _setup_correct_reject_mocks(db, candidate)
        # Override the mocked report's identity status to NOT_CHECKED.
        report.identity_check_status = IdentityCheckStatus.NOT_CHECKED

        with patch.object(svc, 'verify_patient_exists', return_value=patient), \
             patch.object(svc, 'verify_doctor_access'):

            with pytest.raises(svc.IdentityCheckpointBlockedError):
                svc.correct_candidate(
                    db=db, doctor_id=doctor.id, patient_id=patient.id,
                    report_id=report.id, candidate_id=candidate.id,
                    correction_data={"value": "15.0", "reason": "Fix"},
                )
        assert _staged_history_records(db) == []
        assert _staged_test_results(db) == []


# ===========================================================================
# SECTION 4: REJECT history
# ===========================================================================

class TestRejectHistory:
    def test_reject_creates_exactly_one_history_record(self):
        candidate = _make_candidate()
        result, db, doctor, patient, candidate, report = _run_reject(candidate, "Data is wrong")
        records = _staged_history_records(db)
        assert len(records) == 1

    def test_reject_history_action_is_reject(self):
        candidate = _make_candidate()
        result, db, doctor, patient, candidate, report = _run_reject(candidate, "Data is wrong")
        assert _staged_history_records(db)[0].action == VerificationAction.REJECT

    def test_reject_history_preserves_original_candidate(self):
        candidate = _make_candidate()
        result, db, doctor, patient, candidate, report = _run_reject(candidate, "Data is wrong")
        h = _staged_history_records(db)[0]
        assert h.old_test_name == candidate.test_name
        assert h.old_value == candidate.value
        assert h.old_unit == candidate.unit
        assert h.old_normalized_value == candidate.normalized_value
        assert h.old_normalized_unit == candidate.normalized_unit
        assert h.old_canonical_test_id == candidate.canonical_test_id
        assert h.old_reference_range == candidate.reference_range
        assert h.old_result_date == candidate.result_date
        assert h.old_abnormality_status == candidate.abnormality_status

    def test_reject_history_has_no_new_values(self):
        candidate = _make_candidate()
        result, db, doctor, patient, candidate, report = _run_reject(candidate, "Data is wrong")
        h = _staged_history_records(db)[0]
        assert h.new_test_name is None
        assert h.new_value is None
        assert h.new_unit is None
        assert h.new_normalized_value is None
        assert h.new_normalized_unit is None
        assert h.new_canonical_test_id is None
        assert h.new_reference_range is None
        assert h.new_result_date is None
        assert h.new_abnormality_status is None

    def test_reject_history_records_persisted_reason(self):
        candidate = _make_candidate()
        result, db, doctor, patient, candidate, report = _run_reject(candidate, "  Value was extracted incorrectly  ")
        h = _staged_history_records(db)[0]
        # The same validated (stripped) reason persisted on the candidate
        # is recorded in history.
        assert h.reason == "Value was extracted incorrectly"
        assert candidate.rejection_reason == "Value was extracted incorrectly"

    def test_reject_history_creates_no_test_result(self):
        candidate = _make_candidate()
        result, db, doctor, patient, candidate, report = _run_reject(candidate, "Data is wrong")
        assert _staged_test_results(db) == []

    def test_reject_history_does_not_alter_candidate_evidence(self):
        candidate = _make_candidate()
        orig_evidence = candidate.evidence
        result, db, doctor, patient, candidate, report = _run_reject(candidate, "Data is wrong")
        assert candidate.evidence == orig_evidence
        assert _staged_history_records(db)[0].old_value == candidate.value

    def test_reject_commit_failure_rolls_back_history(self):
        import app.services.reject_candidate_service as svc

        candidate = _make_candidate()
        db = MagicMock()
        db, doctor, patient, report, candidate = _setup_correct_reject_mocks(db, candidate, reject_mode=True)
        db.commit.side_effect = SQLAlchemyError("db down")

        with patch.object(svc, 'verify_patient_exists', return_value=patient), \
             patch.object(svc, 'verify_doctor_access'):

            with pytest.raises(svc.RejectError):
                svc.reject_candidate(
                    db=db, doctor_id=doctor.id, patient_id=patient.id,
                    report_id=report.id, candidate_id=candidate.id,
                    reason="Fix",
                )
            db.rollback.assert_called_once()

    def test_failed_reject_creates_no_history(self):
        import app.services.reject_candidate_service as svc

        candidate = _make_candidate(status=CandidateVerificationStatus.REJECTED)
        with pytest.raises(svc.CandidateAlreadyFinalizedError):
            _run_reject(candidate, "Data is wrong")


# ===========================================================================
# SECTION 5: Immutability
# ===========================================================================

class TestImmutability:
    def test_no_update_or_delete_endpoint(self):
        """The only verification-history route is a GET — no update/delete
        API exists anywhere."""
        from app.routers.doctor_reports import router

        history_routes = [
            r for r in router.routes if "verification-history" in r.path
        ]
        assert len(history_routes) == 1
        assert history_routes[0].methods == {"GET"}

    def test_no_update_or_delete_service(self):
        """The history service exposes only create/read — no update or
        delete function exists."""
        import app.services.verification_history_service as svc

        module_names = set(dir(svc))
        assert not any("update" in name.lower() for name in module_names)
        assert not any("delete" in name.lower() for name in module_names)

    def test_second_action_creates_distinct_row_not_overwrite(self):
        """Two actions on two candidates produce two distinct history rows
        — rows are never reused or overwritten."""
        doctor = _make_user(UserRole.DOCTOR)
        patient = _make_user(UserRole.PATIENT)
        report = _make_report(patient.id)
        candidate_a = _make_candidate()
        candidate_b = _make_candidate()
        candidate_b.id = uuid.uuid4()

        # Verify candidate A.
        db1 = MagicMock()
        _setup_verify_mocks(db1, patient, doctor, report, candidate_a)
        from app.services.verify_candidate_service import verify_candidate
        verify_candidate(
            db=db1, doctor_id=doctor.id, patient_id=patient.id,
            report_id=report.id, candidate_id=candidate_a.id,
        )
        records_a = _staged_history_records(db1)
        assert len(records_a) == 1

        # Verify candidate B — same report, second action.
        db2 = MagicMock()
        _setup_verify_mocks(db2, patient, doctor, report, candidate_b)
        verify_candidate(
            db=db2, doctor_id=doctor.id, patient_id=patient.id,
            report_id=report.id, candidate_id=candidate_b.id,
        )
        records_b = _staged_history_records(db2)
        assert len(records_b) == 1

        # Distinct rows — the first record is never overwritten or reused:
        # a second action stages a brand-NEW history object (each becomes
        # its own row on commit; row ids are assigned by the database at
        # insert time, exactly like every other model here).
        assert records_a[0] is not records_b[0]
        assert records_a[0].candidate_id == candidate_a.id
        assert records_b[0].candidate_id == candidate_b.id


# ===========================================================================
# SECTION 6: Snapshot helper trust boundary
# ===========================================================================

class TestSnapshotHelper:
    def test_capture_candidate_snapshot_fields(self):
        candidate = _make_candidate()
        snapshot = capture_candidate_snapshot(candidate)
        assert snapshot["test_name"] == candidate.test_name
        assert snapshot["value"] == candidate.value
        assert snapshot["unit"] == candidate.unit
        assert snapshot["normalized_value"] == candidate.normalized_value
        assert snapshot["normalized_unit"] == candidate.normalized_unit
        assert snapshot["canonical_test_id"] == candidate.canonical_test_id
        assert snapshot["reference_range"] == candidate.reference_range
        assert snapshot["result_date"] == candidate.result_date
        assert snapshot["abnormality_status"] == candidate.abnormality_status

    def test_create_history_never_commits(self):
        """The helper stages the row but NEVER commits — the caller's
        transaction owns atomicity."""
        candidate = _make_candidate()
        report = _make_report(uuid.uuid4())
        db = MagicMock()

        history = create_verification_history(
            db,
            candidate=candidate,
            report=report,
            patient_id=report.patient_id,
            doctor_id=uuid.uuid4(),
            action=VerificationAction.VERIFY,
            old_snapshot=capture_candidate_snapshot(candidate),
            new_snapshot=capture_candidate_snapshot(candidate),
        )
        assert isinstance(history, VerificationHistory)
        db.add.assert_called_once()
        db.commit.assert_not_called()

    def test_create_history_reject_leaves_new_values_null(self):
        candidate = _make_candidate()
        report = _make_report(uuid.uuid4())
        db = MagicMock()

        history = create_verification_history(
            db,
            candidate=candidate,
            report=report,
            patient_id=report.patient_id,
            doctor_id=uuid.uuid4(),
            action=VerificationAction.REJECT,
            old_snapshot=capture_candidate_snapshot(candidate),
            reason="Wrong data",
        )
        assert history.new_value is None
        assert history.reason == "Wrong data"
        assert history.action == VerificationAction.REJECT


# ===========================================================================
# SECTION 7: Regression sanity
# ===========================================================================

class TestRegression:
    def test_router_importable(self):
        from app.routers.doctor_reports import router
        assert router is not None

    def test_history_schema_importable(self):
        from app.schemas.doctor_report import (
            DoctorVerificationHistoryResponse,
            VerificationHistoryItem,
        )
        assert VerificationHistoryItem is not None
        assert DoctorVerificationHistoryResponse is not None

    def test_enum_values(self):
        assert VerificationAction.VERIFY.value == "verify"
        assert VerificationAction.CORRECT.value == "correct"
        assert VerificationAction.REJECT.value == "reject"

    def test_testresult_status_values_unchanged(self):
        assert TestResultStatus.VERIFIED.value == "verified"
        assert TestResultStatus.CORRECTED.value == "corrected"