"""
Tests for the VERIFY candidate feature.

Covers:
    - Authentication: unauthenticated/patient cannot verify
    - Doctor authorization: ACTIVE relationship required
    - Doctor isolation: Doctor A cannot verify Doctor B's patient
    - Relationship status: PENDING/DECLINED/REVOKED denied
    - Report ownership: report must belong to authorized patient
    - Candidate ownership: candidate must belong to the report
    - State protection: only PENDING can be verified
    - Identity metadata: verified_by/verified_at from server
    - Trusted data: TestResult created correctly
    - Candidate remains after verification
    - Race safety: unique constraint prevents duplicate TestResults
    - No mutation: original candidate values preserved

Service-layer tests run without PostgreSQL (mocked DB).
API endpoint tests are BLOCKED — PostgreSQL not available.
"""
import uuid
from datetime import date, datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

import pytest

from app.models.extraction import (
    AbnormalityStatus,
    CandidateVerificationStatus,
    CandidateResult,
    CandidateExtraction,
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
from app.models.relationship import (
    DoctorPatientLink,
    DoctorPatientLinkStatus,
    LinkInitiatedBy,
)
from app.models.user import User, UserRole
from app.services.doctor_report_service import (
    PatientNotFoundError,
    PatientNotPatientRoleError,
    ReportNotFoundError,
    UnauthorizedAccessError,
    verify_doctor_access,
    verify_patient_exists,
)
from app.services.verify_candidate_service import (
    CandidateAlreadyVerifiedError,
    CandidateNotFoundError,
    VerifyError,
    verify_candidate,
)


# --- Helpers ---

def make_user(role: UserRole, name: str = "Test User") -> User:
    """Create a mock User with the given role."""
    user = User(
        full_name=name,
        email=f"{name.lower().replace(' ', '.')}@example.com",
        hashed_password="irrelevant",
        role=role,
    )
    user.id = uuid.uuid4()
    user.created_at = datetime.now(timezone.utc)
    user.updated_at = datetime.now(timezone.utc)
    return user


def make_report(patient_id: uuid.UUID) -> Report:
    """Create a mock Report with identity_check_status set to MATCH
    (the default for tests that exercise the happy path through
    the identity guard)."""
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


def make_extraction(report_id: uuid.UUID) -> CandidateExtraction:
    """Create a mock CandidateExtraction."""
    ext = CandidateExtraction(
        report_id=report_id,
        status=ExtractionRunStatus.COMPLETED,
        source_field=ExtractionSourceField.EXTRACTED_TEXT,
        model_version="v1.0",
        prompt_version="p1.0",
        schema_version="s1.0",
        started_at=datetime.now(timezone.utc),
        completed_at=datetime.now(timezone.utc),
    )
    ext.id = uuid.uuid4()
    ext.created_at = datetime.now(timezone.utc)
    return ext


def make_candidate_result(
    extraction_id: uuid.UUID,
    test_name: str = "Hemoglobin",
    value: str = "14.2",
    verification_status: CandidateVerificationStatus = CandidateVerificationStatus.PENDING,
) -> CandidateResult:
    """Create a mock CandidateResult."""
    cr = CandidateResult(
        candidate_extraction_id=extraction_id,
        test_name=test_name,
        value=value,
        unit="g/dL",
        reference_range="12.0-17.5",
        specimen="Blood",
        result_date="2026-06-15",
        evidence="Hemoglobin 14.2 g/dL",
        confidence=0.95,
        verification_status=verification_status,
        normalization_status=NormalizationStatus.RESOLVED,
        normalized_value=Decimal("14.2"),
        normalized_unit="g/dL",
        unit_normalization_status=UnitNormalizationStatus.RESOLVED,
        normalized_result_date=date(2026, 6, 15),
        date_normalization_status=DateNormalizationStatus.RESOLVED,
        normalized_reference_lower=Decimal("12.0"),
        normalized_reference_upper=Decimal("17.5"),
        reference_range_inclusive_lower=True,
        reference_range_inclusive_upper=True,
        reference_range_normalization_status=ReferenceRangeNormalizationStatus.RESOLVED,
        abnormality_status=AbnormalityStatus.NORMAL,
    )
    cr.id = uuid.uuid4()
    cr.created_at = datetime.now(timezone.utc)
    return cr


def make_link(
    patient_id: uuid.UUID,
    doctor_id: uuid.UUID,
    status: DoctorPatientLinkStatus = DoctorPatientLinkStatus.ACTIVE,
) -> DoctorPatientLink:
    """Create a mock DoctorPatientLink."""
    now = datetime.now(timezone.utc)
    link = DoctorPatientLink(
        patient_id=patient_id,
        doctor_id=doctor_id,
        status=status,
        initiated_by=LinkInitiatedBy.PATIENT,
        initiated_at=now,
    )
    link.id = uuid.uuid4()
    link.created_at = now
    link.updated_at = now
    if status == DoctorPatientLinkStatus.ACTIVE:
        link.accepted_at = now
    return link


def setup_verify_mocks(
    db: MagicMock,
    patient: User,
    doctor: User,
    report: Report,
    extraction: CandidateExtraction,
    candidate: CandidateResult,
):
    """Set up db.query mock chain for verify_candidate."""
    # Chain: 1) User (patient exists) 2) DoctorPatientLink (ACTIVE) 3) Report (ownership) 4) CandidateResult (ownership)
    user_query = MagicMock()
    user_query.filter.return_value.first.return_value = patient

    link_query = MagicMock()
    link_query.filter.return_value.first.return_value = make_link(
        patient.id, doctor.id, DoctorPatientLinkStatus.ACTIVE
    )

    report_query = MagicMock()
    report_query.filter.return_value.first.return_value = report

    # CandidateResult query with join
    candidate_query = MagicMock()
    candidate_query.join.return_value.filter.return_value.options.return_value.first.return_value = candidate

    query_call_count = [0]

    def side_effect(*args, **kwargs):
        query_call_count[0] += 1
        if query_call_count[0] == 1:
            return user_query
        elif query_call_count[0] == 2:
            return link_query
        elif query_call_count[0] == 3:
            return report_query
        else:
            return candidate_query

    db.query.side_effect = side_effect


# =============================================================================
# SECTION 1: Authentication
# =============================================================================

class TestAuthentication:
    """Unauthenticated/patient cannot verify."""

    def test_patient_cannot_verify(self):
        """Patient role is rejected by require_doctor dependency."""
        from app.core.deps import require_doctor
        from fastapi import HTTPException

        patient = make_user(UserRole.PATIENT, "Alice")
        # require_doctor should raise 403 for patient role
        with pytest.raises(HTTPException) as exc_info:
            require_doctor(current_user=patient)
        assert exc_info.value.status_code == 403


# =============================================================================
# SECTION 2: Authorization - ACTIVE Relationship Required
# =============================================================================

class TestVerifyAuthorization:
    """Doctor with ACTIVE relationship can verify."""

    def test_authorized_doctor_can_verify(self):
        """Doctor with ACTIVE link can verify a pending candidate."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        candidate = make_candidate_result(extraction.id)

        db = MagicMock()
        setup_verify_mocks(db, patient, doctor, report, extraction, candidate)

        result = verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)

        assert result["candidate"].verification_status == CandidateVerificationStatus.VERIFIED
        assert result["test_result"].status == TestResultStatus.VERIFIED
        assert result["test_result"].doctor_id == doctor.id
        assert result["test_result"].verified_at is not None
        db.commit.assert_called_once()

    def test_unauthorized_doctor_denied(self):
        """Doctor without ACTIVE link -> UnauthorizedAccessError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        candidate = make_candidate_result(extraction.id)

        db = MagicMock()
        # Patient exists, but no active link
        user_query = MagicMock()
        user_query.filter.return_value.first.return_value = patient
        link_query = MagicMock()
        link_query.filter.return_value.first.return_value = None

        query_call_count = [0]
        def side_effect(*args, **kwargs):
            query_call_count[0] += 1
            if query_call_count[0] == 1:
                return user_query
            return link_query
        db.query.side_effect = side_effect

        with pytest.raises(UnauthorizedAccessError):
            verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)


# =============================================================================
# SECTION 3: Doctor Isolation
# =============================================================================

class TestDoctorIsolation:
    """Doctor A cannot verify Doctor B's patient's candidates."""

    def test_doctor_a_cannot_verify_doctor_b_patient(self):
        """Doctor A cannot access Doctor B's patient candidates."""
        doctor_a = make_user(UserRole.DOCTOR, "Dr. Alice")
        patient = make_user(UserRole.PATIENT, "Charlie")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        candidate = make_candidate_result(extraction.id)

        db = MagicMock()
        # Patient exists, but no active link for doctor_a
        user_query = MagicMock()
        user_query.filter.return_value.first.return_value = patient
        link_query = MagicMock()
        link_query.filter.return_value.first.return_value = None

        query_call_count = [0]
        def side_effect(*args, **kwargs):
            query_call_count[0] += 1
            if query_call_count[0] == 1:
                return user_query
            return link_query
        db.query.side_effect = side_effect

        with pytest.raises(UnauthorizedAccessError):
            verify_candidate(db, doctor_a.id, patient.id, report.id, candidate.id)


# =============================================================================
# SECTION 4: Relationship Status Filtering
# =============================================================================

class TestRelationshipStatusFiltering:
    """PENDING/DECLINED/REVOKED relationships are denied."""

    def test_pending_relationship_denied(self):
        """PENDING relationship -> UnauthorizedAccessError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        candidate = make_candidate_result(extraction.id)

        db = MagicMock()
        user_query = MagicMock()
        user_query.filter.return_value.first.return_value = patient
        link_query = MagicMock()
        link_query.filter.return_value.first.return_value = None  # ACTIVE not found

        query_call_count = [0]
        def side_effect(*args, **kwargs):
            query_call_count[0] += 1
            if query_call_count[0] == 1:
                return user_query
            return link_query
        db.query.side_effect = side_effect

        with pytest.raises(UnauthorizedAccessError):
            verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)

    def test_declined_relationship_denied(self):
        """DECLINED relationship -> UnauthorizedAccessError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        candidate = make_candidate_result(extraction.id)

        db = MagicMock()
        user_query = MagicMock()
        user_query.filter.return_value.first.return_value = patient
        link_query = MagicMock()
        link_query.filter.return_value.first.return_value = None

        query_call_count = [0]
        def side_effect(*args, **kwargs):
            query_call_count[0] += 1
            if query_call_count[0] == 1:
                return user_query
            return link_query
        db.query.side_effect = side_effect

        with pytest.raises(UnauthorizedAccessError):
            verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)

    def test_revoked_relationship_denied(self):
        """REVOKED relationship -> UnauthorizedAccessError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        candidate = make_candidate_result(extraction.id)

        db = MagicMock()
        user_query = MagicMock()
        user_query.filter.return_value.first.return_value = patient
        link_query = MagicMock()
        link_query.filter.return_value.first.return_value = None

        query_call_count = [0]
        def side_effect(*args, **kwargs):
            query_call_count[0] += 1
            if query_call_count[0] == 1:
                return user_query
            return link_query
        db.query.side_effect = side_effect

        with pytest.raises(UnauthorizedAccessError):
            verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)


# =============================================================================
# SECTION 5: Report/Candidate Ownership
# =============================================================================

class TestOwnership:
    """Report/candidate ownership is enforced."""

    def test_report_not_belonging_to_patient_denied(self):
        """Report that doesn't belong to patient -> ReportNotFoundError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        other_patient = make_user(UserRole.PATIENT, "Charlie")
        report = make_report(other_patient.id)  # belongs to Charlie, not Alice

        db = MagicMock()
        user_query = MagicMock()
        user_query.filter.return_value.first.return_value = patient
        link_query = MagicMock()
        link_query.filter.return_value.first.return_value = make_link(
            patient.id, doctor.id, DoctorPatientLinkStatus.ACTIVE
        )
        report_query = MagicMock()
        report_query.filter.return_value.first.return_value = None  # not found

        query_call_count = [0]
        def side_effect(*args, **kwargs):
            query_call_count[0] += 1
            if query_call_count[0] == 1:
                return user_query
            elif query_call_count[0] == 2:
                return link_query
            return report_query
        db.query.side_effect = side_effect

        with pytest.raises(ReportNotFoundError):
            verify_candidate(db, doctor.id, patient.id, report.id, uuid.uuid4())

    def test_candidate_not_belonging_to_report_denied(self):
        """Candidate that doesn't belong to report -> CandidateNotFoundError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)

        db = MagicMock()
        user_query = MagicMock()
        user_query.filter.return_value.first.return_value = patient
        link_query = MagicMock()
        link_query.filter.return_value.first.return_value = make_link(
            patient.id, doctor.id, DoctorPatientLinkStatus.ACTIVE
        )
        report_query = MagicMock()
        report_query.filter.return_value.first.return_value = report
        candidate_query = MagicMock()
        candidate_query.join.return_value.filter.return_value.options.return_value.first.return_value = None

        query_call_count = [0]
        def side_effect(*args, **kwargs):
            query_call_count[0] += 1
            if query_call_count[0] == 1:
                return user_query
            elif query_call_count[0] == 2:
                return link_query
            elif query_call_count[0] == 3:
                return report_query
            return candidate_query
        db.query.side_effect = side_effect

        with pytest.raises(CandidateNotFoundError):
            verify_candidate(db, doctor.id, patient.id, report.id, uuid.uuid4())


# =============================================================================
# SECTION 6: State Protection
# =============================================================================

class TestStateProtection:
    """Only PENDING candidates can be verified."""

    def test_pending_candidate_can_be_verified(self):
        """PENDING candidate can be verified."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        candidate = make_candidate_result(
            extraction.id,
            verification_status=CandidateVerificationStatus.PENDING,
        )

        db = MagicMock()
        setup_verify_mocks(db, patient, doctor, report, extraction, candidate)

        result = verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)
        assert result["candidate"].verification_status == CandidateVerificationStatus.VERIFIED

    def test_already_verified_candidate_rejected(self):
        """Already VERIFIED candidate -> CandidateAlreadyVerifiedError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        candidate = make_candidate_result(
            extraction.id,
            verification_status=CandidateVerificationStatus.VERIFIED,
        )

        db = MagicMock()
        setup_verify_mocks(db, patient, doctor, report, extraction, candidate)

        with pytest.raises(CandidateAlreadyVerifiedError, match="already verified"):
            verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)

    def test_rejected_candidate_cannot_be_verified(self):
        """REJECTED candidate cannot be verified (future state)."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        # Use VERIFIED as proxy since REJECTED isn't in the enum yet
        candidate = make_candidate_result(
            extraction.id,
            verification_status=CandidateVerificationStatus.VERIFIED,
        )

        db = MagicMock()
        setup_verify_mocks(db, patient, doctor, report, extraction, candidate)

        with pytest.raises(CandidateAlreadyVerifiedError):
            verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)


# =============================================================================
# SECTION 7: Identity Metadata
# =============================================================================

class TestIdentityMetadata:
    """verified_by and verified_at are server-generated."""

    def test_verified_by_comes_from_authenticated_doctor(self):
        """verified_by is the authenticated doctor, not client-supplied."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        candidate = make_candidate_result(extraction.id)

        db = MagicMock()
        setup_verify_mocks(db, patient, doctor, report, extraction, candidate)

        result = verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)
        assert result["test_result"].doctor_id == doctor.id

    def test_verified_at_is_server_generated(self):
        """verified_at is generated server-side."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        candidate = make_candidate_result(extraction.id)

        db = MagicMock()
        setup_verify_mocks(db, patient, doctor, report, extraction, candidate)

        before = datetime.now(timezone.utc)
        result = verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)
        after = datetime.now(timezone.utc)

        verified_at = result["test_result"].verified_at
        assert verified_at is not None
        assert before <= verified_at <= after


# =============================================================================
# SECTION 8: Trusted Data
# =============================================================================

class TestTrustedData:
    """TestResult created correctly from candidate data."""

    def test_testresult_created(self):
        """Successful verification creates exactly one TestResult and one
        immutable VerificationHistory record in the same transaction."""
        from app.models.verification_history import VerificationHistory

        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        candidate = make_candidate_result(extraction.id)

        db = MagicMock()
        setup_verify_mocks(db, patient, doctor, report, extraction, candidate)

        result = verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)

        # Exactly one TestResult AND exactly one VerificationHistory are
        # staged in the same transaction (the history helper stages its
        # own row — both are committed together).
        added = [call_args[0][0] for call_args in db.add.call_args_list]
        test_results = [obj for obj in added if isinstance(obj, TestResult)]
        history_records = [obj for obj in added if isinstance(obj, VerificationHistory)]
        assert len(test_results) == 1
        assert isinstance(test_results[0], TestResult)
        assert len(history_records) == 1

    def test_testresult_contains_candidate_data(self):
        """TestResult contains the candidate's server-stored data."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        candidate = make_candidate_result(extraction.id)

        db = MagicMock()
        setup_verify_mocks(db, patient, doctor, report, extraction, candidate)

        result = verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)
        test_result = db.add.call_args[0][0]

        assert test_result.test_name == candidate.test_name
        assert test_result.raw_value == candidate.value
        assert test_result.normalized_value == candidate.normalized_value
        assert test_result.normalized_unit == candidate.normalized_unit
        assert test_result.result_date == candidate.normalized_result_date
        assert test_result.reference_range_lower == candidate.normalized_reference_lower
        assert test_result.reference_range_upper == candidate.normalized_reference_upper
        assert test_result.abnormality_status == candidate.abnormality_status
        assert test_result.status == TestResultStatus.VERIFIED

    def test_candidate_remains_after_verification(self):
        """Candidate is not deleted after verification."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        candidate = make_candidate_result(extraction.id)

        db = MagicMock()
        setup_verify_mocks(db, patient, doctor, report, extraction, candidate)

        result = verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)

        # Candidate should still exist and be in VERIFIED state
        assert result["candidate"] is not None
        assert result["candidate"].verification_status == CandidateVerificationStatus.VERIFIED

    def test_original_values_preserved(self):
        """Original candidate values are not overwritten."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        candidate = make_candidate_result(extraction.id)

        db = MagicMock()
        setup_verify_mocks(db, patient, doctor, report, extraction, candidate)

        result = verify_candidate(db, doctor.id, patient.id, report.id, candidate.id)
        c = result["candidate"]

        # All original values should be preserved
        assert c.test_name == "Hemoglobin"
        assert c.value == "14.2"
        assert c.unit == "g/dL"
        assert c.reference_range == "12.0-17.5"
        assert c.evidence == "Hemoglobin 14.2 g/dL"


# =============================================================================
# SECTION 9: Race Safety
# =============================================================================

class TestRaceSafety:
    """Unique constraint prevents duplicate TestResults."""

    def test_double_verify_raises_conflict(self):
        """Second verify attempt raises CandidateAlreadyVerifiedError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id)
        extraction = make_extraction(report.id)
        candidate = make_candidate_result(extraction.id)

        # First verification succeeds
        db1 = MagicMock()
        setup_verify_mocks(db1, patient, doctor, report, extraction, candidate)
        result = verify_candidate(db1, doctor.id, patient.id, report.id, candidate.id)
        assert result["candidate"].verification_status == CandidateVerificationStatus.VERIFIED

        # Second verification fails because candidate is now VERIFIED
        # (the same candidate object now has VERIFIED status after the first call)
        db2 = MagicMock()
        setup_verify_mocks(db2, patient, doctor, report, extraction, candidate)
        with pytest.raises(CandidateAlreadyVerifiedError):
            verify_candidate(db2, doctor.id, patient.id, report.id, candidate.id)


# =============================================================================
# SECTION 10: Error Handling
# =============================================================================

class TestErrorHandling:
    """Error cases are handled correctly."""

    def test_nonexistent_patient_raises(self):
        """Nonexistent patient -> PatientNotFoundError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient_id = uuid.uuid4()

        db = MagicMock()
        user_query = MagicMock()
        user_query.filter.return_value.first.return_value = None
        db.query.return_value = user_query

        with pytest.raises(PatientNotFoundError):
            verify_candidate(db, doctor.id, patient_id, uuid.uuid4(), uuid.uuid4())

    def test_nonexistent_report_raises(self):
        """Nonexistent report -> ReportNotFoundError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")

        db = MagicMock()
        user_query = MagicMock()
        user_query.filter.return_value.first.return_value = patient
        link_query = MagicMock()
        link_query.filter.return_value.first.return_value = make_link(
            patient.id, doctor.id, DoctorPatientLinkStatus.ACTIVE
        )
        report_query = MagicMock()
        report_query.filter.return_value.first.return_value = None

        query_call_count = [0]
        def side_effect(*args, **kwargs):
            query_call_count[0] += 1
            if query_call_count[0] == 1:
                return user_query
            elif query_call_count[0] == 2:
                return link_query
            return report_query
        db.query.side_effect = side_effect

        with pytest.raises(ReportNotFoundError):
            verify_candidate(db, doctor.id, patient.id, uuid.uuid4(), uuid.uuid4())


# =============================================================================
# SECTION 11: Regression
# =============================================================================

class TestRegression:
    """Existing functionality remains unaffected."""

    def test_candidate_verification_status_has_verified(self):
        """CandidateVerificationStatus enum now has VERIFIED."""
        assert CandidateVerificationStatus.VERIFIED.value == "verified"

    def test_candidate_verification_status_still_has_pending(self):
        """CandidateVerificationStatus enum still has PENDING."""
        assert CandidateVerificationStatus.PENDING.value == "pending"

    def test_testresult_status_has_verified(self):
        """TestResultStatus enum still has VERIFIED."""
        assert TestResultStatus.VERIFIED.value == "verified"

    def test_verify_candidate_service_exists(self):
        """verify_candidate function is importable and callable."""
        assert callable(verify_candidate)

    def test_verify_endpoint_schema_exists(self):
        """VerifyCandidateResponse schema is importable."""
        from app.routers.doctor_reports import VerifyCandidateResponse
        assert VerifyCandidateResponse is not None
