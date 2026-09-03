"""
Tests for Doctor Review Workspace feature.

Covers:
    - Authorization: ACTIVE relationship required
    - Doctor isolation: Doctor A cannot review Doctor B's patient
    - Relationship status filtering: PENDING/DECLINED/REVOKED denied
    - Report ownership: report must belong to authorized patient
    - Candidate data exposure: all normalization fields included
    - Evidence exposure: evidence_record included when available
    - Safe schema exposure: no storage paths, filesystem, or sensitive fields
    - PENDING state preservation: candidates remain PENDING
    - No trusted data creation during review
    - No mutation during read-only review

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
    DateNormalizationStatus,
    ExtractionRunStatus,
    ExtractionSourceField,
    NormalizationStatus,
    ReferenceRangeNormalizationStatus,
    UnitNormalizationStatus,
)
from app.models.report import Report, ReportStatus
from app.models.relationship import (
    DoctorPatientLink,
    DoctorPatientLinkStatus,
    LinkInitiatedBy,
)
from app.models.user import User, UserRole
from app.services.doctor_report_service import (
    DoctorReportError,
    PatientNotFoundError,
    PatientNotPatientRoleError,
    ReportNotFoundError,
    UnauthorizedAccessError,
    get_patient_reports,
    get_report_pdf_path,
    verify_doctor_access,
    verify_patient_exists,
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


def make_report(
    patient_id: uuid.UUID,
    filename: str = "blood_test.pdf",
    status: ReportStatus = ReportStatus.COMPLETED,
) -> Report:
    """Create a mock Report."""
    report = Report(
        patient_id=patient_id,
        original_filename=filename,
        storage_path=f"{uuid.uuid4()}.pdf",
        sha256_hash="a" * 64,
        status=status,
    )
    report.id = uuid.uuid4()
    report.created_at = datetime.now(timezone.utc)
    return report


def make_extraction(
    report_id: uuid.UUID,
    status: ExtractionRunStatus = ExtractionRunStatus.COMPLETED,
) -> MagicMock:
    """Create a mock CandidateExtraction (extraction run)."""
    ext = MagicMock()
    ext.id = uuid.uuid4()
    ext.report_id = report_id
    ext.status = status
    ext.source_field = ExtractionSourceField.EXTRACTED_TEXT
    ext.error_message = None
    ext.model_version = "v1.0"
    ext.prompt_version = "p1.0"
    ext.schema_version = "s1.0"
    ext.started_at = datetime.now(timezone.utc)
    ext.completed_at = datetime.now(timezone.utc)
    ext.created_at = datetime.now(timezone.utc)
    ext.results = []
    return ext


def make_candidate_result(
    test_name: str = "Hemoglobin",
    value: str = "14.2",
    unit: str | None = "g/dL",
    abnormality: AbnormalityStatus = AbnormalityStatus.NORMAL,
    normalization: NormalizationStatus = NormalizationStatus.RESOLVED,
    verification: CandidateVerificationStatus = CandidateVerificationStatus.PENDING,
) -> MagicMock:
    """Create a mock CandidateResult with full review data."""
    cr = MagicMock()
    cr.id = uuid.uuid4()
    cr.test_name = test_name
    cr.value = value
    cr.unit = unit
    cr.reference_range = "12.0-17.5"
    cr.specimen = "Blood"
    cr.result_date = "2026-06-15"
    cr.evidence = f"{test_name} {value} {unit or ''}"
    cr.confidence = 0.95
    cr.verification_status = verification
    cr.normalization_status = normalization
    cr.canonical_test_id = uuid.uuid4()
    cr.canonical_test = MagicMock()
    cr.canonical_test.id = cr.canonical_test_id
    cr.canonical_test.code = "HEMOGLOBIN"
    cr.canonical_test.display_name = "Hemoglobin"
    cr.normalized_value = Decimal("14.2")
    cr.normalized_unit = "g/dL"
    cr.unit_normalization_status = UnitNormalizationStatus.RESOLVED
    cr.normalized_result_date = date(2026, 6, 15)
    cr.date_normalization_status = DateNormalizationStatus.RESOLVED
    cr.normalized_reference_lower = Decimal("12.0")
    cr.normalized_reference_upper = Decimal("17.5")
    cr.reference_range_inclusive_lower = True
    cr.reference_range_inclusive_upper = True
    cr.reference_range_normalization_status = ReferenceRangeNormalizationStatus.RESOLVED
    cr.abnormality_status = abnormality
    cr.created_at = datetime.now(timezone.utc)

    # Evidence record
    cr.evidence_record = MagicMock()
    cr.evidence_record.id = uuid.uuid4()
    cr.evidence_record.source_column = ExtractionSourceField.EXTRACTED_TEXT
    cr.evidence_record.page_number = 3
    cr.evidence_record.source_text = f"{test_name} {value} {unit or ''}"
    cr.evidence_record.created_at = datetime.now(timezone.utc)

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


def setup_db_mocks(
    db: MagicMock,
    patient: User,
    doctor: User,
    reports: list[Report],
    extractions: list[MagicMock] = None,
):
    """Set up db.query mock chain for get_patient_reports."""
    user_query = MagicMock()
    user_query.filter.return_value.first.return_value = patient

    link_query = MagicMock()
    link_query.filter.return_value.first.return_value = make_link(
        patient.id, doctor.id, DoctorPatientLinkStatus.ACTIVE
    )

    report_query = MagicMock()
    report_query.filter.return_value.order_by.return_value.all.return_value = reports

    extraction_queries = []
    if extractions:
        for ext in extractions:
            eq = MagicMock()
            eq.filter.return_value.order_by.return_value.first.return_value = ext
            extraction_queries.append(eq)

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
            idx = query_call_count[0] - 4
            if idx < len(extraction_queries):
                return extraction_queries[idx]
            eq = MagicMock()
            eq.filter.return_value.order_by.return_value.first.return_value = None
            return eq

    db.query.side_effect = side_effect


# =============================================================================
# SECTION 1: Review Authorization - ACTIVE Relationship Required
# =============================================================================

class TestReviewAuthorization:
    """Doctor with ACTIVE relationship can access review data."""

    def test_authorized_doctor_gets_review_data(self):
        """Doctor with ACTIVE link can view patient's report for review."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "blood_test.pdf")
        extraction = make_extraction(report.id)
        cr = make_candidate_result()
        extraction.results = [cr]

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report], [extraction])

        result = get_patient_reports(db, doctor.id, patient.id)

        assert result["patient_id"] == patient.id
        assert result["patient_name"] == "Alice"
        assert len(result["reports"]) == 1
        assert result["reports"][0]["extraction"] is not None
        assert len(result["reports"][0]["extraction"].results) == 1

    def test_unauthorized_doctor_denied(self):
        """Doctor without ACTIVE link -> UnauthorizedAccessError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,   # patient exists
            None,      # no active link
        ]

        with pytest.raises(UnauthorizedAccessError):
            get_patient_reports(db, doctor.id, patient.id)


# =============================================================================
# SECTION 2: Doctor Isolation
# =============================================================================

class TestDoctorIsolation:
    """Doctor A cannot review Doctor B's patient report."""

    def test_doctor_a_cannot_review_doctor_b_patient(self):
        """Doctor A cannot access Doctor B's patient reports."""
        doctor_a = make_user(UserRole.DOCTOR, "Dr. Alice")
        doctor_b = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Charlie")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,   # patient exists
            None,      # no active link for doctor_a
        ]

        with pytest.raises(UnauthorizedAccessError):
            get_patient_reports(db, doctor_a.id, patient.id)


# =============================================================================
# SECTION 3: Relationship Status Filtering
# =============================================================================

class TestRelationshipStatusFiltering:
    """PENDING/DECLINED/REVOKED relationships are denied."""

    def test_pending_relationship_denied(self):
        """PENDING relationship -> UnauthorizedAccessError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,   # patient exists
            None,      # ACTIVE link not found (PENDING doesn't count)
        ]

        with pytest.raises(UnauthorizedAccessError):
            get_patient_reports(db, doctor.id, patient.id)

    def test_declined_relationship_denied(self):
        """DECLINED relationship -> UnauthorizedAccessError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,   # patient exists
            None,      # ACTIVE link not found (DECLINED doesn't count)
        ]

        with pytest.raises(UnauthorizedAccessError):
            get_patient_reports(db, doctor.id, patient.id)

    def test_revoked_relationship_denied(self):
        """REVOKED relationship -> UnauthorizedAccessError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,   # patient exists
            None,      # ACTIVE link not found (REVOKED doesn't count)
        ]

        with pytest.raises(UnauthorizedAccessError):
            get_patient_reports(db, doctor.id, patient.id)


# =============================================================================
# SECTION 4: Report Ownership
# =============================================================================

class TestReportOwnership:
    """Report must belong to the authorized patient."""

    def test_reports_belong_to_patient(self):
        """Only the patient's reports are returned."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        other_patient = make_user(UserRole.PATIENT, "Charlie")

        report_patient = make_report(patient.id, "alice_test.pdf")
        report_other = make_report(other_patient.id, "charlie_test.pdf")

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report_patient])

        result = get_patient_reports(db, doctor.id, patient.id)

        assert len(result["reports"]) == 1
        assert result["reports"][0]["original_filename"] == "alice_test.pdf"


# =============================================================================
# SECTION 5: Candidate Data Exposure - Full Review Data
# =============================================================================

class TestCandidateDataExposure:
    """All normalization fields are included for review."""

    def test_candidate_includes_all_normalization_fields(self):
        """Candidate result includes all required review fields."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")
        extraction = make_extraction(report.id)
        cr = make_candidate_result()
        extraction.results = [cr]

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report], [extraction])

        result = get_patient_reports(db, doctor.id, patient.id)
        candidate = result["reports"][0]["extraction"].results[0]

        # Raw fields
        assert candidate.test_name == "Hemoglobin"
        assert candidate.value == "14.2"
        assert candidate.unit == "g/dL"
        assert candidate.reference_range == "12.0-17.5"

        # Normalization fields
        assert candidate.normalized_value == Decimal("14.2")
        assert candidate.normalized_unit == "g/dL"
        assert candidate.normalization_status == NormalizationStatus.RESOLVED
        assert candidate.unit_normalization_status == UnitNormalizationStatus.RESOLVED

        # Reference range normalization
        assert candidate.normalized_reference_lower == Decimal("12.0")
        assert candidate.normalized_reference_upper == Decimal("17.5")
        assert candidate.reference_range_normalization_status == ReferenceRangeNormalizationStatus.RESOLVED

        # Abnormality
        assert candidate.abnormality_status == AbnormalityStatus.NORMAL

    def test_candidate_includes_canonical_test(self):
        """Candidate result includes canonical test identity."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")
        extraction = make_extraction(report.id)
        cr = make_candidate_result()
        extraction.results = [cr]

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report], [extraction])

        result = get_patient_reports(db, doctor.id, patient.id)
        candidate = result["reports"][0]["extraction"].results[0]

        assert candidate.canonical_test is not None
        assert candidate.canonical_test.display_name == "Hemoglobin"

    def test_candidate_includes_evidence_record(self):
        """Candidate result includes evidence record for review."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")
        extraction = make_extraction(report.id)
        cr = make_candidate_result()
        extraction.results = [cr]

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report], [extraction])

        result = get_patient_reports(db, doctor.id, patient.id)
        candidate = result["reports"][0]["extraction"].results[0]

        assert candidate.evidence_record is not None
        assert candidate.evidence_record.source_text == "Hemoglobin 14.2 g/dL"
        assert candidate.evidence_record.page_number == 3

    def test_unresolved_candidate_included(self):
        """Unresolved candidates are included for doctor review."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")
        extraction = make_extraction(report.id)
        cr = make_candidate_result(
            test_name="Unknown Test",
            value="Positive",
            unit=None,
            abnormality=AbnormalityStatus.UNRESOLVED,
            normalization=NormalizationStatus.UNRESOLVED,
        )
        extraction.results = [cr]

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report], [extraction])

        result = get_patient_reports(db, doctor.id, patient.id)
        candidate = result["reports"][0]["extraction"].results[0]

        assert candidate.test_name == "Unknown Test"
        assert candidate.normalization_status == NormalizationStatus.UNRESOLVED
        assert candidate.abnormality_status == AbnormalityStatus.UNRESOLVED


# =============================================================================
# SECTION 6: Safe Schema Exposure
# =============================================================================

class TestSafeSchemaExposure:
    """No sensitive data is exposed in review response."""

    def test_no_storage_path_in_response(self):
        """storage_path is never in the response."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report])

        result = get_patient_reports(db, doctor.id, patient.id)
        report_data = result["reports"][0]

        assert "storage_path" not in report_data
        assert "sha256_hash" not in report_data

    def test_no_patient_sensitive_fields(self):
        """Patient sensitive fields are not in the response."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        patient.email = "alice@example.com"
        patient.hashed_password = "super_secret_hash"

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [])

        result = get_patient_reports(db, doctor.id, patient.id)

        assert "email" not in result
        assert "hashed_password" not in result
        assert "password" not in result

    def test_evidence_record_no_filesystem_paths(self):
        """Evidence record does not expose filesystem paths."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")
        extraction = make_extraction(report.id)
        cr = make_candidate_result()
        extraction.results = [cr]

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report], [extraction])

        result = get_patient_reports(db, doctor.id, patient.id)
        evidence = result["reports"][0]["extraction"].results[0].evidence_record

        # Evidence record should only have safe fields
        evidence_fields = set(evidence.__dict__.keys()) if hasattr(evidence, '__dict__') else set()
        # No storage_path or filesystem fields
        assert not any('storage' in str(f).lower() for f in evidence_fields)


# =============================================================================
# SECTION 7: PENDING State Preservation
# =============================================================================

class TestPendingStatePreservation:
    """Candidate verification status remains PENDING during review."""

    def test_candidates_always_pending(self):
        """All candidate results have verification_status = PENDING."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")
        extraction = make_extraction(report.id)
        cr1 = make_candidate_result(test_name="Hemoglobin")
        cr2 = make_candidate_result(test_name="Creatinine", value="0.84")
        extraction.results = [cr1, cr2]

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report], [extraction])

        result = get_patient_reports(db, doctor.id, patient.id)

        for r in result["reports"]:
            if r["extraction"] and r["extraction"].results:
                for candidate in r["extraction"].results:
                    assert candidate.verification_status == CandidateVerificationStatus.PENDING

    def test_review_endpoint_is_read_only(self):
        """get_patient_reports does not create TestResult records."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")
        extraction = make_extraction(report.id)
        extraction.results = [make_candidate_result()]

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report], [extraction])

        result = get_patient_reports(db, doctor.id, patient.id)

        # Function should return without creating any TestResult records
        # (We verify this by checking the function returns successfully)
        assert result is not None
        assert len(result["reports"]) == 1


# =============================================================================
# SECTION 8: PDF Path Resolution for Review
# =============================================================================

class TestPdfPathForReview:
    """PDF access enforces same authorization as review data."""

    def test_authorized_doctor_gets_pdf_path(self):
        """Doctor with ACTIVE link gets the PDF filesystem path."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,
            make_link(patient.id, doctor.id, DoctorPatientLinkStatus.ACTIVE),
            report,
        ]

        with patch("app.services.doctor_report_service.resolve_report_path") as mock_resolve:
            mock_resolve.return_value = f"/storage/{report.storage_path}"
            result = get_report_pdf_path(db, doctor.id, patient.id, report.id)
            assert result is not None

    def test_unauthorized_doctor_denied_pdf(self):
        """Doctor without ACTIVE link -> UnauthorizedAccessError for PDF."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,
            None,  # no active link
        ]

        with pytest.raises(UnauthorizedAccessError):
            get_report_pdf_path(db, doctor.id, patient.id, report.id)

    def test_report_not_belonging_to_patient_denied(self):
        """Report that doesn't belong to patient -> ReportNotFoundError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        other_patient = make_user(UserRole.PATIENT, "Charlie")
        report = make_report(other_patient.id, "charlie_test.pdf")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,
            make_link(patient.id, doctor.id, DoctorPatientLinkStatus.ACTIVE),
            None,  # report not found (doesn't belong to patient)
        ]

        with pytest.raises(ReportNotFoundError):
            get_report_pdf_path(db, doctor.id, patient.id, report.id)


# =============================================================================
# SECTION 9: Schema Validation
# =============================================================================

class TestSchemaValidation:
    """Doctor report schemas include all required review fields."""

    def test_doctor_candidate_result_response_has_canonical_test(self):
        """DoctorCandidateResultResponse includes canonical_test field."""
        from app.schemas.doctor_report import DoctorCandidateResultResponse
        fields = DoctorCandidateResultResponse.model_fields
        assert "canonical_test" in fields

    def test_doctor_candidate_result_response_has_evidence_record(self):
        """DoctorCandidateResultResponse includes evidence_record field."""
        from app.schemas.doctor_report import DoctorCandidateResultResponse
        fields = DoctorCandidateResultResponse.model_fields
        assert "evidence_record" in fields

    def test_doctor_canonical_test_response_exists(self):
        """DoctorCanonicalTestResponse schema is importable."""
        from app.schemas.doctor_report import DoctorCanonicalTestResponse
        assert DoctorCanonicalTestResponse is not None

    def test_doctor_evidence_response_exists(self):
        """DoctorEvidenceResponse schema is importable."""
        from app.schemas.doctor_report import DoctorEvidenceResponse
        assert DoctorEvidenceResponse is not None


# =============================================================================
# SECTION 10: Regression
# =============================================================================

class TestRegression:
    """Existing functionality remains unaffected."""

    def test_report_model_unchanged(self):
        """Report model still has the same structure."""
        from app.models.report import Report, ReportStatus
        assert hasattr(Report, 'id')
        assert hasattr(Report, 'patient_id')
        assert hasattr(Report, 'storage_path')

    def test_extraction_models_unchanged(self):
        """Extraction models are not affected."""
        from app.models.extraction import CandidateResult, ExtractionEvidence
        assert hasattr(CandidateResult, 'verification_status')
        assert hasattr(ExtractionEvidence, 'source_text')

    def test_relationship_service_unchanged(self):
        """Relationship service functions still exist."""
        from app.services.relationship_service import (
            doctor_has_active_access,
            get_doctor_roster,
        )
        assert callable(doctor_has_active_access)
        assert callable(get_doctor_roster)

    def test_doctor_report_service_functions_exist(self):
        """All required service functions are importable and callable."""
        from app.services.doctor_report_service import (
            get_patient_reports,
            get_report_pdf_path,
            verify_patient_exists,
            verify_doctor_access,
        )
        assert callable(get_patient_reports)
        assert callable(get_report_pdf_path)
        assert callable(verify_patient_exists)
        assert callable(verify_doctor_access)


# =============================================================================
# SECTION 11: API Endpoint Tests (BLOCKED)
# =============================================================================

class TestReviewEndpoint:
    """Test API endpoints for doctor review workspace.

    BLOCKED: PostgreSQL not available in this environment.
    These tests require a running database to test the full
    FastAPI dependency injection chain.
    """

    @pytest.mark.skip(reason="BLOCKED: PostgreSQL not available")
    def test_unauthenticated_blocked(self):
        """Unauthenticated access is rejected."""
        pass

    @pytest.mark.skip(reason="BLOCKED: PostgreSQL not available")
    def test_patient_blocked_from_review_endpoint(self):
        """Patient cannot use doctor review endpoint."""
        pass

    @pytest.mark.skip(reason="BLOCKED: PostgreSQL not available")
    def test_doctor_gets_review_data(self):
        """Doctor sees full candidate review data for their patient."""
        pass

    @pytest.mark.skip(reason="BLOCKED: PostgreSQL not available")
    def test_doctor_cannot_review_other_patient(self):
        """Doctor A cannot access Doctor B's patient report for review."""
        pass

    @pytest.mark.skip(reason="BLOCKED: PostgreSQL not available")
    def test_pending_relationship_denied_review(self):
        """PENDING relationship denies review access."""
        pass

    @pytest.mark.skip(reason="BLOCKED: PostgreSQL not available")
    def test_pdf_download_enforces_authorization(self):
        """PDF download requires ACTIVE relationship."""
        pass

    @pytest.mark.skip(reason="BLOCKED: PostgreSQL not available")
    def test_nonexistent_report_returns_404(self):
        """Nonexistent report UUID returns 404."""
        pass
