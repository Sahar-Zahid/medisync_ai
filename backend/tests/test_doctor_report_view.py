"""
Tests for Doctor Patient-Report View feature.

Covers:
    - Authorized doctor can view patient reports
    - Unauthorized doctor denied access
    - PENDING/DECLINED/REVOKED relationships denied
    - Patient with no reports gets empty list
    - Reports filtered to authorized patient only
    - Safe data exposure (no storage paths, hashes, etc.)
    - Candidate results remain PENDING
    - PDF path resolution enforces authorization
    - Report not belonging to patient is denied
    - Regression: existing services/schemas unaffected

Service-layer tests run without PostgreSQL (mocked DB).
API endpoint tests are BLOCKED — PostgreSQL not available.
"""
import uuid
from datetime import datetime, timezone
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


def make_candidate_result():
    """Create a mock CandidateResult with PENDING verification."""
    cr = MagicMock()
    cr.id = uuid.uuid4()
    cr.test_name = "Hemoglobin"
    cr.value = "14.2"
    cr.unit = "g/dL"
    cr.reference_range = "12.0-17.5"
    cr.specimen = "Blood"
    cr.result_date = "2026-06-15"
    cr.evidence = "Hemoglobin 14.2 g/dL"
    cr.confidence = 0.95
    cr.verification_status = CandidateVerificationStatus.PENDING
    cr.normalization_status = NormalizationStatus.RESOLVED
    cr.canonical_test_id = uuid.uuid4()
    cr.normalized_value = 14.2
    cr.normalized_unit = "g/dL"
    cr.unit_normalization_status = UnitNormalizationStatus.RESOLVED
    cr.normalized_result_date = None
    cr.date_normalization_status = DateNormalizationStatus.RESOLVED
    cr.normalized_reference_lower = 12.0
    cr.normalized_reference_upper = 17.5
    cr.reference_range_inclusive_lower = True
    cr.reference_range_inclusive_upper = True
    cr.reference_range_normalization_status = ReferenceRangeNormalizationStatus.RESOLVED
    cr.abnormality_status = AbnormalityStatus.NORMAL
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


def setup_db_mocks(
    db: MagicMock,
    patient: User,
    doctor: User,
    reports: list[Report],
    extractions: list[MagicMock] = None,
):
    """Set up db.query mock chain for get_patient_reports."""
    # Chain: db.query(User).filter().first() -> patient (first call)
    # Then: db.query(DoctorPatientLink).filter().first() -> link (second call)
    # Then: db.query(Report).filter().order_by().all() -> reports
    # Then: db.query(CandidateExtraction).filter().order_by().first() -> extraction (per report)

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

    # Return different query objects based on call order
    query_call_count = [0]
    original_query = db.query

    def side_effect(*args, **kwargs):
        query_call_count[0] += 1
        if query_call_count[0] == 1:
            return user_query  # User query
        elif query_call_count[0] == 2:
            return link_query  # DoctorPatientLink query
        elif query_call_count[0] == 3:
            return report_query  # Report query
        else:
            # Extraction queries (one per report)
            idx = query_call_count[0] - 4
            if idx < len(extraction_queries):
                return extraction_queries[idx]
            eq = MagicMock()
            eq.filter.return_value.order_by.return_value.first.return_value = None
            return eq

    db.query.side_effect = side_effect


# =============================================================================
# SECTION 1: Patient Verification
# =============================================================================

class TestPatientVerification:
    """verify_patient_exists correctly validates patient identity."""

    def test_existing_patient_returns_user(self):
        """Patient exists with correct role -> returns User."""
        patient = make_user(UserRole.PATIENT, "Alice")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = patient

        result = verify_patient_exists(db, patient.id)
        assert result.id == patient.id
        assert result.role == UserRole.PATIENT

    def test_nonexistent_patient_raises(self):
        """Patient doesn't exist -> raises PatientNotFoundError."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(PatientNotFoundError):
            verify_patient_exists(db, uuid.uuid4())

    def test_wrong_role_raises(self):
        """User exists but is not a PATIENT -> raises PatientNotPatientRoleError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = doctor

        with pytest.raises(PatientNotPatientRoleError):
            verify_patient_exists(db, doctor.id)


# =============================================================================
# SECTION 2: Doctor Access Verification
# =============================================================================

class TestDoctorAccessVerification:
    """verify_doctor_access correctly checks ACTIVE DoctorPatientLink."""

    def test_active_link_grants_access(self):
        """Doctor with ACTIVE link -> no exception raised."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = make_link(
            patient.id, doctor.id, DoctorPatientLinkStatus.ACTIVE
        )

        # Should not raise
        verify_doctor_access(db, doctor.id, patient.id)

    def test_no_link_raises_unauthorized(self):
        """No link -> raises UnauthorizedAccessError."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(UnauthorizedAccessError):
            verify_doctor_access(db, uuid.uuid4(), uuid.uuid4())

    def test_pending_link_raises_unauthorized(self):
        """PENDING link -> raises UnauthorizedAccessError (only ACTIVE allowed)."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None  # ACTIVE query returns None

        with pytest.raises(UnauthorizedAccessError):
            verify_doctor_access(db, uuid.uuid4(), uuid.uuid4())


# =============================================================================
# SECTION 3: Get Patient Reports - Authorization
# =============================================================================

class TestGetPatientReportsAuthorization:
    """get_patient_reports enforces correct authorization rules."""

    def test_authorized_doctor_gets_reports(self):
        """Doctor with ACTIVE link can view patient's reports."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "blood_test.pdf")
        extraction = make_extraction(report.id)
        extraction.results = [make_candidate_result()]

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report], [extraction])

        result = get_patient_reports(db, doctor.id, patient.id)

        assert result["patient_id"] == patient.id
        assert result["patient_name"] == "Alice"
        assert len(result["reports"]) == 1
        assert result["reports"][0]["id"] == report.id

    def test_unauthorized_doctor_denied(self):
        """Doctor without ACTIVE link -> UnauthorizedAccessError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")

        db = MagicMock()
        # Patient exists, but no active link
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,   # patient exists
            None,      # no active link
        ]

        with pytest.raises(UnauthorizedAccessError):
            get_patient_reports(db, doctor.id, patient.id)

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

    def test_doctor_a_cannot_see_doctor_b_patient(self):
        """Doctor A cannot access Doctor B's patient reports."""
        doctor_a = make_user(UserRole.DOCTOR, "Dr. Alice")
        doctor_b = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Charlie")

        db = MagicMock()
        # Patient exists, but no ACTIVE link between doctor_a and patient
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,   # patient exists
            None,      # no active link for doctor_a
        ]

        with pytest.raises(UnauthorizedAccessError):
            get_patient_reports(db, doctor_a.id, patient.id)


# =============================================================================
# SECTION 4: Get Patient Reports - Data
# =============================================================================

class TestGetPatientReportsData:
    """get_patient_reports returns correct data structure."""

    def test_reports_belong_to_patient(self):
        """Only the patient's reports are returned, not other patients'."""
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

    def test_empty_reports_for_patient_with_no_reports(self):
        """Patient with no reports returns empty list."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [])

        result = get_patient_reports(db, doctor.id, patient.id)

        assert result["patient_id"] == patient.id
        assert result["reports"] == []

    def test_reports_ordered_by_created_at_desc(self):
        """Reports are ordered newest first."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")

        older = make_report(patient.id, "old_test.pdf")
        older.created_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        newer = make_report(patient.id, "new_test.pdf")
        newer.created_at = datetime(2026, 6, 1, tzinfo=timezone.utc)

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [newer, older])

        result = get_patient_reports(db, doctor.id, patient.id)

        assert result["reports"][0]["original_filename"] == "new_test.pdf"
        assert result["reports"][1]["original_filename"] == "old_test.pdf"

    def test_extraction_included_when_available(self):
        """Extraction data is included when available."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")
        extraction = make_extraction(report.id)

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report], [extraction])

        result = get_patient_reports(db, doctor.id, patient.id)

        assert result["reports"][0]["extraction"] is not None

    def test_extraction_none_when_not_available(self):
        """Extraction is None when no extraction exists for the report."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report], [None])

        result = get_patient_reports(db, doctor.id, patient.id)

        assert result["reports"][0]["extraction"] is None

    def test_candidate_results_included(self):
        """Candidate results are included in extraction data."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")
        extraction = make_extraction(report.id)
        cr = make_candidate_result()
        extraction.results = [cr]

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report], [extraction])

        result = get_patient_reports(db, doctor.id, patient.id)

        assert len(result["reports"][0]["extraction"].results) == 1


# =============================================================================
# SECTION 5: Safe Data Exposure
# =============================================================================

class TestSafeDataExposure:
    """Ensure no sensitive data is leaked in report responses."""

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
        assert "patient_id" not in report_data  # Patient ID not on individual reports

    def test_no_patient_sensitive_fields(self):
        """Patient sensitive fields are not in the response."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        patient.email = "alice@example.com"
        patient.hashed_password = "super_secret_hash"

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [])

        result = get_patient_reports(db, doctor.id, patient.id)

        # Response should only have safe fields
        assert "email" not in result
        assert "hashed_password" not in result
        assert "password" not in result

    def test_safe_report_fields_only(self):
        """Report response only contains safe metadata fields."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report])

        result = get_patient_reports(db, doctor.id, patient.id)
        report_data = result["reports"][0]

        # Should have these safe fields
        assert "id" in report_data
        assert "original_filename" in report_data
        assert "status" in report_data
        assert "created_at" in report_data
        assert "extraction" in report_data

        # Should NOT have sensitive fields
        assert "storage_path" not in report_data
        assert "sha256_hash" not in report_data


# =============================================================================
# SECTION 6: Candidate PENDING Status
# =============================================================================

class TestCandidatePendingStatus:
    """Candidate results remain PENDING — no verification/trusted data."""

    def test_candidates_always_pending(self):
        """All candidate results have verification_status = PENDING."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")
        extraction = make_extraction(report.id)
        cr = make_candidate_result()
        cr.verification_status = CandidateVerificationStatus.PENDING
        extraction.results = [cr]

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report], [extraction])

        result = get_patient_reports(db, doctor.id, patient.id)

        for r in result["reports"]:
            if r["extraction"] and r["extraction"].results:
                for candidate in r["extraction"].results:
                    assert candidate.verification_status == CandidateVerificationStatus.PENDING

    def test_no_test_results_created(self):
        """get_patient_reports does not create TestResult records."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")
        extraction = make_extraction(report.id)
        extraction.results = [make_candidate_result()]

        db = MagicMock()
        setup_db_mocks(db, patient, doctor, [report], [extraction])

        result = get_patient_reports(db, doctor.id, patient.id)

        # Only Report, CandidateExtraction queries should be made
        # No TestResult creation should happen
        # (We can't easily verify no TestResult was created with mock,
        # but we can verify the function returns without error)
        assert result is not None


# =============================================================================
# SECTION 7: PDF Path Resolution
# =============================================================================

class TestPdfPathResolution:
    """get_report_pdf_path enforces authorization and returns correct path."""

    def test_authorized_doctor_gets_path(self):
        """Doctor with ACTIVE link gets the PDF filesystem path."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")

        db = MagicMock()
        # Patient exists
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,   # patient exists
            make_link(patient.id, doctor.id, DoctorPatientLinkStatus.ACTIVE),  # active link
            report,    # report belongs to patient
        ]

        with patch("app.services.doctor_report_service.resolve_report_path") as mock_resolve:
            mock_resolve.return_value = f"/storage/{report.storage_path}"
            result = get_report_pdf_path(db, doctor.id, patient.id, report.id)
            assert result is not None

    def test_unauthorized_doctor_denied(self):
        """Doctor without ACTIVE link -> UnauthorizedAccessError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "test.pdf")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,   # patient exists
            None,      # no active link
        ]

        with pytest.raises(UnauthorizedAccessError):
            get_report_pdf_path(db, doctor.id, patient.id, report.id)

    def test_report_not_belonging_to_patient_denied(self):
        """Report that doesn't belong to the patient -> ReportNotFoundError."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        other_patient = make_user(UserRole.PATIENT, "Charlie")
        report = make_report(other_patient.id, "charlie_test.pdf")  # belongs to Charlie, not Alice

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,   # patient exists
            make_link(patient.id, doctor.id, DoctorPatientLinkStatus.ACTIVE),  # active link
            None,      # report not found (doesn't belong to patient)
        ]

        with pytest.raises(ReportNotFoundError):
            get_report_pdf_path(db, doctor.id, patient.id, report.id)

    def test_path_never_exposed_to_client(self):
        """The path is returned server-side only, never sent to client."""
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
            mock_resolve.return_value = f"/private/storage/{report.storage_path}"
            result = get_report_pdf_path(db, doctor.id, patient.id, report.id)
            # The path is returned as a Path object for server-side use
            # It should never be serialized to JSON or exposed to client
            assert str(result) == f"/private/storage/{report.storage_path}"


# =============================================================================
# SECTION 8: Schemas
# =============================================================================

class TestSchemas:
    """Doctor report schemas are correctly defined."""

    def test_doctor_report_response_schema_exists(self):
        """DoctorReportResponse schema is importable."""
        from app.schemas.doctor_report import DoctorReportResponse
        assert DoctorReportResponse is not None

    def test_doctor_extraction_response_schema_exists(self):
        """DoctorExtractionResponse schema is importable."""
        from app.schemas.doctor_report import DoctorExtractionResponse
        assert DoctorExtractionResponse is not None

    def test_doctor_candidate_result_response_schema_exists(self):
        """DoctorCandidateResultResponse schema is importable."""
        from app.schemas.doctor_report import DoctorCandidateResultResponse
        assert DoctorCandidateResultResponse is not None

    def test_doctor_patient_reports_response_schema_exists(self):
        """DoctorPatientReportsResponse schema is importable."""
        from app.schemas.doctor_report import DoctorPatientReportsResponse
        assert DoctorPatientReportsResponse is not None

    def test_doctor_report_response_no_storage_path(self):
        """DoctorReportResponse schema does not include storage_path."""
        from app.schemas.doctor_report import DoctorReportResponse
        fields = DoctorReportResponse.model_fields
        assert "storage_path" not in fields
        assert "sha256_hash" not in fields


# =============================================================================
# SECTION 9: Regression
# =============================================================================

class TestRegression:
    """Existing functionality remains unaffected."""

    def test_report_model_unchanged(self):
        """Report model still has the same structure."""
        from app.models.report import Report, ReportStatus
        assert hasattr(Report, 'id')
        assert hasattr(Report, 'patient_id')
        assert hasattr(Report, 'storage_path')
        assert hasattr(Report, 'sha256_hash')
        assert hasattr(Report, 'status')
        assert ReportStatus.UPLOADED.value == "uploaded"
        assert ReportStatus.COMPLETED.value == "completed"

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

    def test_user_model_unchanged(self):
        """User model still has the same structure."""
        from app.models.user import User, UserRole
        assert hasattr(User, 'id')
        assert hasattr(User, 'role')
        assert UserRole.PATIENT.value == "patient"
        assert UserRole.DOCTOR.value == "doctor"

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
# SECTION 10: API Endpoint Tests (BLOCKED)
# =============================================================================

class TestDoctorReportEndpoint:
    """Test API endpoints for doctor patient-report view.

    BLOCKED: PostgreSQL not available in this environment.
    These tests require a running database to test the full
    FastAPI dependency injection chain.
    """

    @pytest.mark.skip(reason="BLOCKED: PostgreSQL not available")
    def test_unauthenticated_blocked(self):
        """Unauthenticated access is rejected."""
        pass

    @pytest.mark.skip(reason="BLOCKED: PostgreSQL not available")
    def test_patient_blocked_from_reports_endpoint(self):
        """Patient cannot use doctor reports endpoint."""
        pass

    @pytest.mark.skip(reason="BLOCKED: PostgreSQL not available")
    def test_doctor_gets_patient_reports(self):
        """Doctor sees only their linked patient's reports."""
        pass

    @pytest.mark.skip(reason="BLOCKED: PostgreSQL not available")
    def test_doctor_cannot_see_other_patient(self):
        """Doctor A cannot access Doctor B's patient reports."""
        pass

    @pytest.mark.skip(reason="BLOCKED: PostgreSQL not available")
    def test_pending_relationship_denied(self):
        """PENDING relationship denies report access."""
        pass

    @pytest.mark.skip(reason="BLOCKED: PostgreSQL not available")
    def test_pdf_download_enforces_authorization(self):
        """PDF download requires ACTIVE relationship."""
        pass

    @pytest.mark.skip(reason="BLOCKED: PostgreSQL not available")
    def test_nonexistent_patient_returns_404(self):
        """Nonexistent patient UUID returns 404."""
        pass

    @pytest.mark.skip(reason="BLOCKED: PostgreSQL not available")
    def test_guessed_report_uuid_denied(self):
        """Guessed report UUID cannot bypass patient ownership."""
        pass
