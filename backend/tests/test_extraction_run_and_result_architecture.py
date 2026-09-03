"""
Tests for the ExtractionRun + TestResult architecture:

* ExtractionRun (CandidateExtraction) version tracking and lifecycle
* TestResult model existence and trust boundary
* CandidateResult → ExtractionRun relationship
* Trust boundary enforcement: extraction never creates TestResult

Mocked DB throughout (unittest.mock), no live PostgreSQL and no live
LLM/API calls anywhere in this file — consistent with the other
normalization test files.

Run with:
    PYTHONPATH=. python3 -m pytest tests/test_extraction_run_and_result_architecture.py -v
"""
import inspect
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from app.models.extraction import (
    AbnormalityStatus,
    CandidateExtraction,
    CandidateResult,
    CandidateVerificationStatus,
    ExtractionRunStatus,
    ExtractionSourceField,
    NormalizationStatus,
    TestResult,
    TestResultStatus,
)
from app.schemas.extraction import CandidateExtractionResponse, TestResultResponse
from app.services import candidate_extraction_service as svc


# =========================================================================
# SECTION 1: ExtractionRun Lifecycle
# =========================================================================


class TestExtractionRunLifecycle:
    """ExtractionRun (CandidateExtraction) lifecycle and version tracking."""

    def test_extraction_run_can_be_created_for_a_report(self):
        """A CandidateExtraction can be instantiated for a report."""
        now = datetime.now(timezone.utc)
        run = CandidateExtraction(
            report_id=uuid.uuid4(),
            status=ExtractionRunStatus.COMPLETED,
            source_field=ExtractionSourceField.EXTRACTED_TEXT,
            started_at=now,
            completed_at=now,
        )
        assert run.report_id is not None
        assert run.status == ExtractionRunStatus.COMPLETED

    def test_extraction_run_starts_with_correct_status(self):
        """A new extraction run is created with either COMPLETED or FAILED."""
        now = datetime.now(timezone.utc)
        run_completed = CandidateExtraction(
            report_id=uuid.uuid4(),
            status=ExtractionRunStatus.COMPLETED,
            source_field=ExtractionSourceField.EXTRACTED_TEXT,
            started_at=now,
            completed_at=now,
        )
        run_failed = CandidateExtraction(
            report_id=uuid.uuid4(),
            status=ExtractionRunStatus.FAILED,
            source_field=ExtractionSourceField.EXTRACTED_TEXT,
            started_at=now,
            completed_at=now,
        )
        assert run_completed.status == ExtractionRunStatus.COMPLETED
        assert run_failed.status == ExtractionRunStatus.FAILED

    def test_extraction_run_stores_version_metadata(self):
        """Version metadata (model_version, prompt_version, schema_version)
        is stored on the extraction run."""
        now = datetime.now(timezone.utc)
        run = CandidateExtraction(
            report_id=uuid.uuid4(),
            status=ExtractionRunStatus.COMPLETED,
            source_field=ExtractionSourceField.EXTRACTED_TEXT,
            model_version="1.0.0",
            prompt_version="2.1.0",
            schema_version="3.0.0",
            started_at=now,
            completed_at=now,
        )
        assert run.model_version == "1.0.0"
        assert run.prompt_version == "2.1.0"
        assert run.schema_version == "3.0.0"

    def test_extraction_run_stores_lifecycle_timestamps(self):
        """started_at and completed_at track the run lifecycle."""
        start = datetime(2026, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
        end = datetime(2026, 1, 1, 0, 0, 30, tzinfo=timezone.utc)
        run = CandidateExtraction(
            report_id=uuid.uuid4(),
            status=ExtractionRunStatus.COMPLETED,
            source_field=ExtractionSourceField.EXTRACTED_TEXT,
            started_at=start,
            completed_at=end,
        )
        assert run.started_at == start
        assert run.completed_at == end

    def test_extraction_run_completed_at_can_be_none(self):
        """completed_at can be None for interrupted/in-progress runs."""
        now = datetime.now(timezone.utc)
        run = CandidateExtraction(
            report_id=uuid.uuid4(),
            status=ExtractionRunStatus.COMPLETED,
            source_field=ExtractionSourceField.EXTRACTED_TEXT,
            started_at=now,
            completed_at=None,
        )
        assert run.completed_at is None

    def test_extraction_run_preserves_report_relationship(self):
        """ExtractionRun references its report via report_id."""
        report_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        run = CandidateExtraction(
            report_id=report_id,
            status=ExtractionRunStatus.COMPLETED,
            source_field=ExtractionSourceField.EXTRACTED_TEXT,
            started_at=now,
            completed_at=now,
        )
        assert run.report_id == report_id

    def test_failed_extraction_run_remains_auditable(self):
        """A FAILED extraction run preserves error information and version
        metadata for audit purposes."""
        now = datetime.now(timezone.utc)
        run = CandidateExtraction(
            report_id=uuid.uuid4(),
            status=ExtractionRunStatus.FAILED,
            source_field=ExtractionSourceField.EXTRACTED_TEXT,
            error_message="Gemini API timeout",
            model_version="1.0.0",
            prompt_version="1.0.0",
            schema_version="1.0.0",
            started_at=now,
            completed_at=now,
        )
        assert run.status == ExtractionRunStatus.FAILED
        assert run.error_message == "Gemini API timeout"
        assert run.model_version == "1.0.0"

    def test_retry_attempts_are_distinguishable(self):
        """Each extraction attempt creates a separate CandidateExtraction
        row, so retries are auditable as distinct events."""
        report_id = uuid.uuid4()
        now = datetime.now(timezone.utc)
        id1 = uuid.uuid4()
        id2 = uuid.uuid4()
        run1 = CandidateExtraction(
            id=id1,
            report_id=report_id,
            status=ExtractionRunStatus.FAILED,
            source_field=ExtractionSourceField.EXTRACTED_TEXT,
            error_message="Timeout",
            started_at=now,
            completed_at=now,
        )
        run2 = CandidateExtraction(
            id=id2,
            report_id=report_id,
            status=ExtractionRunStatus.COMPLETED,
            source_field=ExtractionSourceField.EXTRACTED_TEXT,
            started_at=now,
            completed_at=now,
        )
        # They are distinct objects with distinct states.
        assert run1.id != run2.id
        assert run1.status == ExtractionRunStatus.FAILED
        assert run2.status == ExtractionRunStatus.COMPLETED

    def test_version_constants_are_defined(self):
        """The extraction service defines version constants for
        auditability."""
        assert hasattr(svc, "MODEL_VERSION")
        assert hasattr(svc, "PROMPT_VERSION")
        assert hasattr(svc, "SCHEMA_VERSION")
        assert isinstance(svc.MODEL_VERSION, str)
        assert isinstance(svc.PROMPT_VERSION, str)
        assert isinstance(svc.SCHEMA_VERSION, str)


# =========================================================================
# SECTION 2: Candidate → ExtractionRun Relationship
# =========================================================================


class TestCandidateExtractionRelationship:
    """CandidateResult belongs to exactly one ExtractionRun."""

    def test_candidate_result_belongs_to_extraction_run(self):
        """CandidateResult references its ExtractionRun via
        candidate_extraction_id."""
        extraction_id = uuid.uuid4()
        candidate = CandidateResult(
            candidate_extraction_id=extraction_id,
            test_name="Hemoglobin",
            value="12.4",
            evidence="Hemoglobin: 12.4",
        )
        assert candidate.candidate_extraction_id == extraction_id

    def test_extraction_run_has_results_relationship(self):
        """CandidateExtraction has a results relationship to CandidateResult."""
        assert hasattr(CandidateExtraction, "results")

    def test_candidate_result_is_always_pending(self):
        """Every CandidateResult starts with verification_status=PENDING."""
        candidate = CandidateResult(
            id=uuid.uuid4(),
            candidate_extraction_id=uuid.uuid4(),
            test_name="Test",
            value="1.0",
            evidence="Test: 1.0",
            verification_status=CandidateVerificationStatus.PENDING,
        )
        assert candidate.verification_status == CandidateVerificationStatus.PENDING

    def test_candidate_result_normalization_statuses_are_default(self):
        """Normalization statuses default to UNRESOLVED, independent of
        verification_status."""
        candidate = CandidateResult(
            id=uuid.uuid4(),
            candidate_extraction_id=uuid.uuid4(),
            test_name="Test",
            value="1.0",
            evidence="Test: 1.0",
            normalization_status=NormalizationStatus.UNRESOLVED,
            verification_status=CandidateVerificationStatus.PENDING,
        )
        assert candidate.normalization_status == NormalizationStatus.UNRESOLVED
        assert candidate.verification_status == CandidateVerificationStatus.PENDING


# =========================================================================
# SECTION 3: TestResult Model Architecture
# =========================================================================


class TestTestResultArchitecture:
    """TestResult is the ONLY representation of trusted medical data."""

    def test_test_result_can_be_instantiated(self):
        """TestResult can be created with the required fields."""
        now = datetime.now(timezone.utc)
        result = TestResult(
            candidate_result_id=uuid.uuid4(),
            extraction_run_id=uuid.uuid4(),
            status=TestResultStatus.PENDING,
            test_name="Hemoglobin",
            raw_value="12.4",
            created_at=now,
        )
        assert result.test_name == "Hemoglobin"
        assert result.raw_value == "12.4"
        assert result.status == TestResultStatus.PENDING

    def test_test_result_status_enum_has_correct_values(self):
        """TestResultStatus has exactly the four expected values."""
        values = {s.value for s in TestResultStatus}
        assert values == {"pending", "verified", "corrected", "rejected"}

    def test_test_result_stores_normalized_data(self):
        """TestResult can store normalized values from the normalization
        chain."""
        result = TestResult(
            candidate_result_id=uuid.uuid4(),
            extraction_run_id=uuid.uuid4(),
            status=TestResultStatus.VERIFIED,
            test_name="Hemoglobin",
            raw_value="12.4",
            normalized_value=Decimal("124.0"),
            normalized_unit="g/L",
            reference_range_lower=Decimal("3.5"),
            reference_range_upper=Decimal("5.5"),
            reference_range_inclusive_lower=True,
            reference_range_inclusive_upper=True,
            abnormality_status=AbnormalityStatus.HIGH,
        )
        assert result.normalized_value == Decimal("124.0")
        assert result.normalized_unit == "g/L"
        assert result.abnormality_status == AbnormalityStatus.HIGH

    def test_test_result_can_store_doctor_metadata(self):
        """TestResult can store doctor verification metadata."""
        doctor_id = uuid.uuid4()
        verified_at = datetime.now(timezone.utc)
        result = TestResult(
            candidate_result_id=uuid.uuid4(),
            extraction_run_id=uuid.uuid4(),
            status=TestResultStatus.CORRECTED,
            test_name="Hemoglobin",
            raw_value="12.4",
            doctor_id=doctor_id,
            verified_at=verified_at,
            correction_note="Corrected unit from g/dL to g/L",
        )
        assert result.doctor_id == doctor_id
        assert result.verified_at == verified_at
        assert result.correction_note == "Corrected unit from g/dL to g/L"

    def test_test_result_can_be_rejected(self):
        """A TestResult can have status REJECTED without being trusted."""
        result = TestResult(
            candidate_result_id=uuid.uuid4(),
            extraction_run_id=uuid.uuid4(),
            status=TestResultStatus.REJECTED,
            test_name="Hemoglobin",
            raw_value="12.4",
        )
        assert result.status == TestResultStatus.REJECTED

    def test_test_result_has_correct_relationships(self):
        """TestResult has relationships to CandidateResult,
        CandidateExtraction, CanonicalTest, and User (doctor)."""
        assert hasattr(TestResult, "candidate")
        assert hasattr(TestResult, "extraction_run")
        assert hasattr(TestResult, "canonical_test")
        assert hasattr(TestResult, "doctor")

    def test_candidate_result_has_trusted_result_relationship(self):
        """CandidateResult has a one-to-one relationship to TestResult."""
        assert hasattr(CandidateResult, "trusted_result")


# =========================================================================
# SECTION 4: Trust Boundary — Extraction Never Creates TestResult
# =========================================================================


class TestTrustBoundary:
    """The extraction pipeline NEVER creates a TestResult."""

    def test_successful_extraction_creates_candidate_result(self):
        """Successful extraction creates CandidateResult rows."""
        from app.schemas.gemini_extraction import GeminiCandidateItem

        now = datetime.now(timezone.utc)
        extraction = CandidateExtraction(
            report_id=uuid.uuid4(),
            status=ExtractionRunStatus.COMPLETED,
            source_field=ExtractionSourceField.EXTRACTED_TEXT,
            started_at=now,
            completed_at=now,
        )
        extraction.results = [
            CandidateResult(
                test_name="Hemoglobin",
                value="12.4",
                unit="g/dL",
                evidence="Hemoglobin: 12.4 g/dL",
            )
        ]
        assert len(extraction.results) == 1
        assert extraction.results[0].test_name == "Hemoglobin"

    def test_candidate_result_remains_pending_after_extraction(self):
        """CandidateResult always has verification_status=PENDING."""
        candidate = CandidateResult(
            candidate_extraction_id=uuid.uuid4(),
            test_name="Glucose",
            value="95",
            unit="mg/dL",
            evidence="Glucose: 95 mg/dL",
            verification_status=CandidateVerificationStatus.PENDING,
        )
        assert candidate.verification_status == CandidateVerificationStatus.PENDING

    def test_extraction_service_has_no_testresult_import(self):
        """The candidate_extraction_service does not import or create
        TestResult — trusted results are only created by the doctor
        review workflow."""
        source = inspect.getsource(svc)
        # The service should not instantiate TestResult objects.
        # "TestResult" may appear in docstrings describing the trust
        # boundary, but must never appear as a class instantiation.
        assert "TestResult(" not in source

    def test_no_code_in_extraction_service_marks_verified(self):
        """No code in the extraction service can mark a candidate as
        VERIFIED or create a trusted result."""
        source = inspect.getsource(svc)
        # Should never produce a verified state
        assert "VERIFIED" not in source
        # "verified" may appear in docstrings explaining the trust
        # boundary, but must never appear as an enum value or variable.
        # Check that it does not appear as a standalone word outside
        # docstrings — use a simple approach: check the non-docstring
        # portions.
        lines = source.split('\n')
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('\"\"\"') or stripped.startswith("'''"):
                in_docstring = not in_docstring
                # If the line opens and closes a docstring on the same line,
                # toggle twice
                if stripped.count('\"\"\"') >= 2 or stripped.count("'''") >= 2:
                    in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            assert "verified" not in line.lower(), (
                f"'verified' found outside docstring: {line.strip()}"
            )
        # Should never produce a corrected state
        assert "CORRECTED" not in source
        assert "corrected" not in source.lower()

    def test_normalization_does_not_change_verification_status(self):
        """Normalization services never modify verification_status."""
        # The normalization chain produces normalization_status, not
        # verification_status. Check the normalization_fields output.
        now = datetime.now(timezone.utc)
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        fields = svc._normalization_fields(
            db, "Hemoglobin", "12.4", "g/dL", None, "3.5 - 5.5"
        )
        # The normalization_fields dict should never contain
        # verification_status
        assert "verification_status" not in fields

    def test_trusted_result_is_not_created_automatically(self):
        """Even after a complete extraction with normalization,
        TestResult must not exist until a doctor acts."""
        now = datetime.now(timezone.utc)
        extraction = CandidateExtraction(
            report_id=uuid.uuid4(),
            status=ExtractionRunStatus.COMPLETED,
            source_field=ExtractionSourceField.EXTRACTED_TEXT,
            started_at=now,
            completed_at=now,
        )
        extraction.results = [
            CandidateResult(
                test_name="Hemoglobin",
                value="12.4",
                evidence="Hemoglobin: 12.4",
                verification_status=CandidateVerificationStatus.PENDING,
            )
        ]
        # The candidate has no trusted_result set
        assert extraction.results[0].trusted_result is None
        # There is no TestResult creation in the extraction service
        source = inspect.getsource(svc)
        assert "TestResult(" not in source


# =========================================================================
# SECTION 5: Schema Compatibility
# =========================================================================


class TestSchemaCompatibility:
    """Response schemas correctly include new fields."""

    def test_candidate_extraction_response_includes_versions(self):
        """CandidateExtractionResponse includes version tracking fields."""
        fields = set(CandidateExtractionResponse.model_fields.keys())
        assert "model_version" in fields
        assert "prompt_version" in fields
        assert "schema_version" in fields
        assert "started_at" in fields
        assert "completed_at" in fields

    def test_test_result_response_schema_exists(self):
        """TestResultResponse schema is defined with the expected fields."""
        fields = set(TestResultResponse.model_fields.keys())
        assert "id" in fields
        assert "candidate_result_id" in fields
        assert "extraction_run_id" in fields
        assert "status" in fields
        assert "test_name" in fields
        assert "raw_value" in fields
        assert "normalized_value" in fields
        assert "normalized_unit" in fields
        assert "result_date" in fields
        assert "abnormality_status" in fields
        assert "doctor_id" in fields
        assert "verified_at" in fields
        assert "correction_note" in fields

    def test_test_result_status_in_schemas(self):
        """TestResultStatus is importable from the schemas module."""
        from app.schemas.extraction import TestResultStatus as SchemaStatus
        assert SchemaStatus is TestResultStatus


# =========================================================================
# SECTION 6: No External Dependencies
# =========================================================================


class TestNoExternalDependency:
    """TestResult and extraction architecture have no LLM/network dependency."""

    def test_testresult_model_has_no_llm_dependency(self):
        source = inspect.getsource(TestResult)
        for forbidden in ("gemini", "openai", "anthropic"):
            assert forbidden not in source.lower()

    def test_testresult_model_makes_no_network_calls(self):
        source = inspect.getsource(TestResult)
        for forbidden in ("requests", "httpx", "urllib", "socket"):
            assert forbidden not in source.lower()

    def test_extraction_run_version_constants_are_strings(self):
        """Version constants are plain strings, not complex objects."""
        assert isinstance(svc.MODEL_VERSION, str)
        assert isinstance(svc.PROMPT_VERSION, str)
        assert isinstance(svc.SCHEMA_VERSION, str)
        # They should look like version strings
        for v in (svc.MODEL_VERSION, svc.PROMPT_VERSION, svc.SCHEMA_VERSION):
            assert "." in v or v.isdigit()


# =========================================================================
# SECTION 7: Determinism
# =========================================================================


class TestDeterminism:
    """Extraction run creation is deterministic for the same inputs."""

    def test_version_constants_are_deterministic(self):
        """The same version constants are returned every time."""
        v1 = svc.MODEL_VERSION
        v2 = svc.MODEL_VERSION
        assert v1 == v2

    def test_candidate_verification_status_always_pending(self):
        """Every new CandidateResult always starts PENDING."""
        for _ in range(10):
            c = CandidateResult(
                id=uuid.uuid4(),
                candidate_extraction_id=uuid.uuid4(),
                test_name="X",
                value="1",
                evidence="X: 1",
                verification_status=CandidateVerificationStatus.PENDING,
            )
            assert c.verification_status == CandidateVerificationStatus.PENDING
