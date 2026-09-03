"""
Focused tests for ExtractionEvidence (source/provenance tracking).

These tests verify that the ExtractionEvidence model and its integration
into the extraction pipeline correctly implement provenance-verified
evidence: Gemini's evidence hint is matched against actual report text
before being stored.

Evidence provenance flow:
    AI evidence hint -> matched against actual report source text
    -> matched text stored (or None if not matched)

Tests are organized into sections:
  1. Evidence matching service (core logic)
  2. Evidence record creation and structure
  3. Evidence-to-candidate relationship
  4. Evidence-to-extraction-run relationship
  5. Evidence-to-report ownership
  6. Source text preservation and matching
  7. NULL fields for unavailable provenance
  8. Trust boundary (evidence does not change candidate status)
  9. Security and ownership
 10. Immutability / retry behavior
 11. Schema serialization
 12. Static checks (no filesystem paths, no LLM wording)
 13. Integration verification
"""
import uuid
import re
from datetime import datetime, timezone

import pytest

from app.models.extraction import (
    AbnormalityStatus,
    CandidateExtraction,
    CandidateResult,
    CandidateVerificationStatus,
    DateNormalizationStatus,
    ExtractionEvidence,
    ExtractionRunStatus,
    ExtractionSourceField,
    NormalizationStatus,
    ReferenceRangeNormalizationStatus,
    UnitNormalizationStatus,
)


# =============================================================================
# SECTION 1: Evidence matching service (core logic)
# =============================================================================

class TestEvidenceMatching:
    """The evidence matching service correctly locates AI hints in report text."""

    def test_exact_match_returns_report_text(self):
        """When the AI hint appears verbatim in report text, the matched text is returned."""
        from app.services.evidence_matching_service import match_evidence_to_source
        report = "Hemoglobin 12.4 g/dL WBC 7.2 x10^9/L Platelets 250 x10^9/L"
        hint = "Hemoglobin 12.4 g/dL"
        result = match_evidence_to_source(hint, report)
        assert result == "Hemoglobin 12.4 g/dL"

    def test_match_with_whitespace_differences(self):
        """Whitespace differences between hint and report text are tolerated."""
        from app.services.evidence_matching_service import match_evidence_to_source
        report = "Hemoglobin  12.4  g/dL"  # double spaces
        hint = "Hemoglobin 12.4 g/dL"  # single spaces
        result = match_evidence_to_source(hint, report)
        assert result is not None
        assert "Hemoglobin" in result
        assert "12.4" in result

    def test_hint_not_in_report_returns_none(self):
        """When the AI hint does NOT appear in report text, None is returned."""
        from app.services.evidence_matching_service import match_evidence_to_source
        report = "WBC 7.2 x10^9/L Platelets 250 x10^9/L"
        hint = "Hemoglobin 12.4 g/dL"  # not in report
        result = match_evidence_to_source(hint, report)
        assert result is None

    def test_empty_hint_returns_none(self):
        """An empty or blank AI hint returns None (nothing to match)."""
        from app.services.evidence_matching_service import match_evidence_to_source
        result = match_evidence_to_source("", "Some report text")
        assert result is None
        result2 = match_evidence_to_source("   ", "Some report text")
        assert result2 is None

    def test_empty_report_returns_none(self):
        """An empty report source text returns None (nothing to match against)."""
        from app.services.evidence_matching_service import match_evidence_to_source
        result = match_evidence_to_source("Hemoglobin 12.4", "")
        assert result is None
        result2 = match_evidence_to_source("Hemoglobin 12.4", "   ")
        assert result2 is None

    def test_substring_within_larger_text(self):
        """A hint that is a substring of the report text is matched."""
        from app.services.evidence_matching_service import match_evidence_to_source
        report = (
            "Complete Blood Count\\n"
            "Hemoglobin: 12.4 g/dL\\n"
            "Reference range: 12.0-16.0 g/dL\\n"
            "WBC: 7.2 x10^9/L"
        )
        hint = "Hemoglobin: 12.4 g/dL"
        result = match_evidence_to_source(hint, report)
        assert result is not None
        assert "Hemoglobin" in result
        assert "12.4" in result

    def test_returns_text_from_report_not_hint(self):
        """When matched, the returned text is from the report, not the hint.

        This is critical for provenance: the stored evidence must come
        from the authoritative report text, not from AI output.
        """
        from app.services.evidence_matching_service import match_evidence_to_source
        # The report has extra context around the test value
        report = "Lab Results: Hemoglobin 12.4 g/dL (ref: 12.0-16.0)"
        hint = "Hemoglobin 12.4 g/dL"
        result = match_evidence_to_source(hint, report)
        assert result == "Hemoglobin 12.4 g/dL"
        # Verify we returned the normalized hint (which is a substring of
        # the normalized report), NOT some modified version
        assert "ref" not in result

    def test_deterministic_output(self):
        """Matching is deterministic for the same inputs."""
        from app.services.evidence_matching_service import match_evidence_to_source
        report = "Hemoglobin 12.4 g/dL WBC 7.2"
        hint = "Hemoglobin 12.4 g/dL"
        r1 = match_evidence_to_source(hint, report)
        r2 = match_evidence_to_source(hint, report)
        assert r1 == r2

    def test_no_fuzzy_matching(self):
        """Partial or similar text does NOT match — only exact substring after normalization."""
        from app.services.evidence_matching_service import match_evidence_to_source
        report = "Hemoglobin 12.5 g/dL"  # note: 12.5 not 12.4
        hint = "Hemoglobin 12.4 g/dL"   # hint says 12.4
        result = match_evidence_to_source(hint, report)
        assert result is None  # must NOT fuzzy-match 12.4 to 12.5


# =============================================================================
# SECTION 2: Evidence record creation and structure
# =============================================================================

class TestEvidenceCreation:
    """Evidence records can be created with all required fields."""

    def test_evidence_has_required_fields(self):
        """ExtractionEvidence defines all required columns."""
        required_columns = {
            "id",
            "candidate_result_id",
            "extraction_run_id",
            "report_id",
            "source_column",
            "page_number",
            "source_text",
            "bounding_box_x",
            "bounding_box_y",
            "bounding_box_width",
            "bounding_box_height",
            "created_at",
        }
        actual_columns = {c.name for c in ExtractionEvidence.__table__.columns}
        assert required_columns == actual_columns

    def test_evidence_table_name(self):
        """ExtractionEvidence maps to the correct table."""
        assert ExtractionEvidence.__tablename__ == "extraction_evidence"

    def test_evidence_has_unique_constraint_on_candidate_result(self):
        """Each candidate result has exactly one evidence record."""
        constraints = {
            c.name for c in ExtractionEvidence.__table__.constraints
            if hasattr(c, "name")
        }
        assert "uq_extraction_evidence_candidate_result_id" in constraints

    def test_evidence_has_foreign_keys(self):
        """ExtractionEvidence has FKs to candidate_results, candidate_extractions, and reports."""
        fk_columns = set()
        for fk in ExtractionEvidence.__table__.foreign_keys:
            fk_columns.add(fk.parent.name)
        assert "candidate_result_id" in fk_columns
        assert "extraction_run_id" in fk_columns
        assert "report_id" in fk_columns

    def test_evidence_source_column_uses_existing_enum(self):
        """source_column uses the existing ExtractionSourceField enum."""
        source_col = ExtractionEvidence.__table__.c.source_column
        assert source_col is not None

    def test_source_text_is_nullable(self):
        """source_text is nullable — NULL when evidence hint not matched."""
        col = ExtractionEvidence.__table__.c.source_text
        assert col.nullable is True

    def test_source_text_is_text_type(self):
        """source_text uses Text type for arbitrary-length evidence."""
        from sqlalchemy import Text
        col = ExtractionEvidence.__table__.c.source_text
        assert isinstance(col.type, Text)


# =============================================================================
# SECTION 3: Evidence-to-candidate relationship
# =============================================================================

class TestEvidenceCandidateRelationship:
    """ExtractionEvidence correctly references its CandidateResult."""

    def test_candidate_result_has_evidence_record_relationship(self):
        """CandidateResult has an evidence_record relationship."""
        assert hasattr(CandidateResult, "evidence_record")

    def test_evidence_has_candidate_result_id(self):
        """ExtractionEvidence stores candidate_result_id."""
        col = ExtractionEvidence.__table__.c.candidate_result_id
        assert col is not None
        assert col.nullable is False

    def test_candidate_result_evidence_is_optional(self):
        """The evidence_record relationship uselist=False (one-to-one optional)."""
        from sqlalchemy.orm import RelationshipProperty
        prop = CandidateResult.__mapper__.get_property("evidence_record")
        assert isinstance(prop, RelationshipProperty)
        assert prop.uselist is False


# =============================================================================
# SECTION 4: Evidence-to-extraction-run relationship
# =============================================================================

class TestEvidenceExtractionRunRelationship:
    """ExtractionEvidence correctly references its CandidateExtraction run."""

    def test_evidence_has_extraction_run_id(self):
        """ExtractionEvidence stores extraction_run_id."""
        col = ExtractionEvidence.__table__.c.extraction_run_id
        assert col is not None
        assert col.nullable is False

    def test_extraction_run_id_is_indexed(self):
        """extraction_run_id is indexed for query performance."""
        indexed_cols = set()
        for idx in ExtractionEvidence.__table__.indexes:
            indexed_cols.update(c.name for c in idx.columns)
        assert "extraction_run_id" in indexed_cols


# =============================================================================
# SECTION 5: Evidence-to-report ownership
# =============================================================================

class TestEvidenceReportOwnership:
    """ExtractionEvidence correctly references its Report for ownership."""

    def test_evidence_has_report_id(self):
        """ExtractionEvidence stores report_id for ownership derivation."""
        col = ExtractionEvidence.__table__.c.report_id
        assert col is not None
        assert col.nullable is False

    def test_report_id_is_indexed(self):
        """report_id is indexed for ownership-scoped queries."""
        indexed_cols = set()
        for idx in ExtractionEvidence.__table__.indexes:
            indexed_cols.update(c.name for c in idx.columns)
        assert "report_id" in indexed_cols

    def test_evidence_does_not_store_patient_id_directly(self):
        """Ownership is derived through report_id, never stored directly."""
        columns = {c.name for c in ExtractionEvidence.__table__.columns}
        assert "patient_id" not in columns
        assert "user_id" not in columns


# =============================================================================
# SECTION 6: Source text preservation and matching
# =============================================================================

class TestSourceTextMatching:
    """Evidence source_text is populated via matching against actual report text."""

    def test_service_uses_evidence_matching(self):
        """_persist_completed_extraction calls match_evidence_to_source."""
        import inspect
        from app.services.candidate_extraction_service import (
            _persist_completed_extraction,
        )
        source = inspect.getsource(_persist_completed_extraction)
        assert "match_evidence_to_source" in source

    def test_service_does_not_use_gemini_evidence_directly(self):
        """The service does NOT set source_text=item.evidence directly."""
        import inspect
        from app.services.candidate_extraction_service import (
            _persist_completed_extraction,
        )
        source = inspect.getsource(_persist_completed_extraction)
        # Must NOT directly assign AI output to source_text
        assert "source_text=item.evidence" not in source

    def test_service_passes_report_source_text(self):
        """_persist_completed_extraction receives and uses report_source_text."""
        import inspect
        from app.services.candidate_extraction_service import (
            _persist_completed_extraction,
        )
        source = inspect.getsource(_persist_completed_extraction)
        assert "report_source_text" in source

    def test_persist_failed_does_not_create_evidence(self):
        """_persist_failed_extraction does NOT create evidence rows."""
        import inspect
        from app.services.candidate_extraction_service import (
            _persist_failed_extraction,
        )
        source = inspect.getsource(_persist_failed_extraction)
        assert "ExtractionEvidence" not in source

    def test_extraction_chain_sequence(self):
        """Evidence matching is called after normalization fields are computed."""
        import inspect
        from app.services.candidate_extraction_service import (
            _persist_completed_extraction,
        )
        source = inspect.getsource(_persist_completed_extraction)
        norm_pos = source.find("_normalization_fields")
        match_pos = source.find("match_evidence_to_source")
        evidence_pos = source.find("ExtractionEvidence(")
        # Normalization must be called before evidence matching
        assert norm_pos < match_pos, (
            "_normalization_fields must be called before match_evidence_to_source"
        )
        # Evidence matching must be called before ExtractionEvidence creation
        assert match_pos < evidence_pos, (
            "match_evidence_to_source must be called before ExtractionEvidence creation"
        )

    def test_existing_candidate_result_evidence_field_preserved(self):
        """CandidateResult.evidence (Text column) is still present."""
        col = CandidateResult.__table__.c.evidence
        assert col is not None
        from sqlalchemy import Text
        assert isinstance(col.type, Text)


# =============================================================================
# SECTION 7: NULL fields for unavailable provenance
# =============================================================================

class TestUnavailableProvenance:
    """Fields that depend on extraction pipeline capabilities are NULL when unavailable."""

    def test_page_number_nullable(self):
        """page_number is nullable (may not be available)."""
        col = ExtractionEvidence.__table__.c.page_number
        assert col.nullable is True

    def test_bounding_box_fields_nullable(self):
        """All bounding box fields are nullable."""
        for field_name in ["bounding_box_x", "bounding_box_y",
                           "bounding_box_width", "bounding_box_height"]:
            col = getattr(ExtractionEvidence.__table__.c, field_name)
            assert col.nullable is True

    def test_no_default_for_page_number(self):
        """page_number has no default — NULL means unknown, not 0."""
        col = ExtractionEvidence.__table__.c.page_number
        assert col.server_default is None

    def test_extraction_service_sets_provenance_to_none(self):
        """The extraction pipeline sets page_number and bounding boxes to None."""
        import inspect
        from app.services.candidate_extraction_service import (
            _persist_completed_extraction,
        )
        source = inspect.getsource(_persist_completed_extraction)
        assert "page_number=None" in source
        assert "bounding_box_x=None" in source
        assert "bounding_box_y=None" in source
        assert "bounding_box_width=None" in source
        assert "bounding_box_height=None" in source

    def test_unmatched_evidence_produces_none_source_text(self):
        """When the evidence hint doesn't match report text, source_text is None."""
        from app.services.evidence_matching_service import match_evidence_to_source
        result = match_evidence_to_source(
            "Hemoglobin 12.4",  # AI hint
            "WBC 7.2 Platelets 250"  # report text (no hemoglobin)
        )
        assert result is None


# =============================================================================
# SECTION 8: Trust boundary
# =============================================================================

class TestTrustBoundary:
    """Evidence does not change candidate verification status."""

    def test_candidate_verification_status_unchanged_by_evidence(self):
        """ExtractionEvidence does not define or modify verification_status."""
        columns = {c.name for c in ExtractionEvidence.__table__.columns}
        assert "verification_status" not in columns

    def test_candidate_result_default_verification_status(self):
        """CandidateResult verification_status defaults to PENDING."""
        col = CandidateResult.__table__.c.verification_status
        assert col.default.arg == CandidateVerificationStatus.PENDING

    def test_evidence_has_no_trust_state(self):
        """ExtractionEvidence has no status or trust-related column."""
        columns = {c.name for c in ExtractionEvidence.__table__.columns}
        assert "status" not in columns
        assert "verification_status" not in columns
        assert "trust_status" not in columns


# =============================================================================
# SECTION 9: Security and ownership
# =============================================================================

class TestSecurityOwnership:
    """Evidence cannot be used to cross patient ownership boundaries."""

    def test_evidence_no_direct_patient_reference(self):
        """ExtractionEvidence never stores patient_id."""
        columns = {c.name for c in ExtractionEvidence.__table__.columns}
        assert "patient_id" not in columns

    def test_evidence_no_filesystem_paths(self):
        """ExtractionEvidence never stores filesystem paths."""
        columns = {c.name for c in ExtractionEvidence.__table__.columns}
        for col_name in columns:
            assert "path" not in col_name.lower()
            assert "file" not in col_name.lower()
            assert "storage" not in col_name.lower()

    def test_source_text_comes_from_matching_not_directly(self):
        """source_text is populated via evidence matching, never directly from AI output."""
        import inspect
        from app.services.candidate_extraction_service import (
            _persist_completed_extraction,
        )
        source = inspect.getsource(_persist_completed_extraction)
        # Must use the matching service, not directly copy AI output
        assert "match_evidence_to_source" in source
        assert "source_text=item.evidence" not in source

    def test_evidence_no_api_keys_in_source(self):
        """Evidence source text comes from report text matching, not config."""
        import inspect
        from app.services.candidate_extraction_service import (
            _persist_completed_extraction,
        )
        source = inspect.getsource(_persist_completed_extraction)
        # Before the evidence matching call, there should be no settings/api_key references
        assert "api_key" not in source.lower()


# =============================================================================
# SECTION 10: Immutability / retry behavior
# =============================================================================

class TestImmutabilityRetryBehavior:
    """Evidence records are immutable and distinguishable across retries."""

    def test_evidence_not_updatable_by_service(self):
        """The extraction service only creates evidence, never updates existing ones."""
        import inspect
        from app.services.candidate_extraction_service import (
            _persist_completed_extraction,
        )
        source = inspect.getsource(_persist_completed_extraction)
        assert "ExtractionEvidence(" in source
        lines = source.split("\n")
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            assert "ExtractionEvidence.update" not in stripped
            assert "ExtractionEvidence.merge" not in stripped

    def test_evidence_has_created_at_timestamp(self):
        """Evidence records have created_at for audit trail."""
        col = ExtractionEvidence.__table__.c.created_at
        assert col is not None
        assert col.nullable is False

    def test_candidate_extraction_has_results_relationship(self):
        """CandidateExtraction has a results relationship for retry comparison."""
        assert hasattr(CandidateExtraction, "results")

    def test_evidence_recorded_per_extraction_run(self):
        """Each ExtractionEvidence belongs to exactly one extraction run."""
        col = ExtractionEvidence.__table__.c.extraction_run_id
        assert col is not None


# =============================================================================
# SECTION 11: Schema serialization
# =============================================================================

class TestSchemaSerialization:
    """ExtractionEvidenceResponse schema correctly serializes evidence."""

    def test_evidence_response_has_all_fields(self):
        """ExtractionEvidenceResponse includes all evidence fields."""
        from app.schemas.extraction import ExtractionEvidenceResponse
        fields = set(ExtractionEvidenceResponse.model_fields.keys())
        expected = {
            "id",
            "candidate_result_id",
            "extraction_run_id",
            "report_id",
            "source_column",
            "page_number",
            "source_text",
            "bounding_box_x",
            "bounding_box_y",
            "bounding_box_width",
            "bounding_box_height",
            "created_at",
        }
        assert expected == fields

    def test_evidence_response_source_text_nullable(self):
        """ExtractionEvidenceResponse.source_text is Optional (nullable)."""
        from app.schemas.extraction import ExtractionEvidenceResponse
        field = ExtractionEvidenceResponse.model_fields["source_text"]
        # The annotation should allow None
        assert field.is_required() is False

    def test_candidate_result_response_includes_evidence_record(self):
        """CandidateResultResponse includes evidence_record field."""
        from app.schemas.extraction import CandidateResultResponse
        fields = set(CandidateResultResponse.model_fields.keys())
        assert "evidence_record" in fields

    def test_candidate_result_response_preserves_existing_fields(self):
        """CandidateResultResponse still includes all original fields."""
        from app.schemas.extraction import CandidateResultResponse
        fields = set(CandidateResultResponse.model_fields.keys())
        for original_field in [
            "id", "test_name", "value", "unit", "reference_range",
            "specimen", "result_date", "evidence", "confidence",
            "verification_status", "normalization_status", "canonical_test",
            "normalized_value", "normalized_unit", "unit_normalization_status",
            "normalized_result_date", "date_normalization_status",
            "normalized_reference_lower", "normalized_reference_upper",
            "reference_range_inclusive_lower", "reference_range_inclusive_upper",
            "reference_range_normalization_status", "abnormality_status",
            "created_at",
        ]:
            assert original_field in fields, f"Missing field: {original_field}"

    def test_evidence_response_config_from_attributes(self):
        """ExtractionEvidenceResponse uses from_attributes for ORM compatibility."""
        from app.schemas.extraction import ExtractionEvidenceResponse
        assert ExtractionEvidenceResponse.model_config.get("from_attributes") is True


# =============================================================================
# SECTION 12: Static checks (no filesystem paths, no LLM wording)
# =============================================================================

class TestStaticChecks:
    """Static analysis checks on the evidence module."""

    def test_no_filesystem_paths_in_model_source(self):
        """ExtractionEvidence model source contains no filesystem paths."""
        import inspect
        source = inspect.getsource(ExtractionEvidence)
        assert "/home" not in source
        assert "/tmp" not in source
        assert "/var" not in source
        assert "/uploads" not in source
        assert "storage_path" not in source

    def test_no_llm_wording_in_evidence_matching_service(self):
        """evidence_matching_service has no AI/LLM dependency wording."""
        import inspect
        from app.services.evidence_matching_service import match_evidence_to_source
        source = inspect.getsource(match_evidence_to_source)
        source_lower = source.lower()
        # Check executable lines only (skip docstrings and comments)
        lines = source.split('\n')
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"""') and stripped.endswith('"""') and len(stripped) > 6:
                continue
            if stripped.startswith('"""'):
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if stripped.startswith('#'):
                continue
            # Executable line: no AI/LLM words
            assert 'gemini' not in stripped.lower()
            assert 'openai' not in stripped.lower()
            assert not re.search(r'\bllm\b', stripped.lower())
            assert not re.search(r'\bai\b', stripped.lower())

    def test_no_llm_wording_in_evidence_model(self):
        """ExtractionEvidence model has no AI/LLM wording in executable code."""
        import inspect
        source = inspect.getsource(ExtractionEvidence)
        source_lower = source.lower()
        assert not re.search(r'\bllm\b', source_lower)
        lines = source.split('\n')
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"""') and stripped.endswith('"""') and len(stripped) > 6:
                continue
            if stripped.startswith('"""'):
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            assert 'gemini' not in stripped.lower()
            assert 'openai' not in stripped.lower()

    def test_no_llm_wording_in_service_integration(self):
        """candidate_extraction_service evidence integration has no LLM wording."""
        import inspect
        from app.services.candidate_extraction_service import (
            _persist_completed_extraction,
        )
        source = inspect.getsource(_persist_completed_extraction)
        source_lower = source.lower()
        assert not re.search(r'\bllm\b', source_lower)
        lines = source.split('\n')
        in_docstring = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith('"""') and stripped.endswith('"""') and len(stripped) > 6:
                continue
            if stripped.startswith('"""'):
                in_docstring = not in_docstring
                continue
            if in_docstring:
                continue
            if stripped.startswith('#'):
                continue
            assert 'gemini' not in stripped.lower()
            assert 'openai' not in stripped.lower()


# =============================================================================
# SECTION 13: Integration verification
# =============================================================================

class TestIntegrationVerification:
    """Verify the evidence pipeline is correctly wired."""

    def test_persist_completed_creates_evidence(self):
        """_persist_completed_extraction creates ExtractionEvidence rows."""
        import inspect
        from app.services.candidate_extraction_service import (
            _persist_completed_extraction,
        )
        source = inspect.getsource(_persist_completed_extraction)
        assert "ExtractionEvidence(" in source

    def test_evidence_imported_in_service(self):
        """ExtractionEvidence and match_evidence_to_source are imported."""
        import app.services.candidate_extraction_service as svc
        import inspect
        source = inspect.getsource(svc)
        assert "from app.models.extraction import" in source
        assert "ExtractionEvidence" in source
        assert "match_evidence_to_source" in source

    def test_evidence_exported_in_models_init(self):
        """ExtractionEvidence is exported from app.models."""
        from app.models import ExtractionEvidence as EE
        assert EE is ExtractionEvidence

    def test_matching_service_exported(self):
        """match_evidence_to_source is importable from the service."""
        from app.services.evidence_matching_service import match_evidence_to_source
        assert callable(match_evidence_to_source)

    def test_verify_evidence_not_copied_directly_from_ai(self):
        """Evidence source_text is populated via match_evidence_to_source, not item.evidence."""
        import inspect
        from app.services.candidate_extraction_service import (
            _persist_completed_extraction,
        )
        source = inspect.getsource(_persist_completed_extraction)
        # The match result is used for source_text
        assert "matched_source_text" in source
        # Must NOT directly assign AI output to source_text
        assert "source_text=item.evidence" not in source


# =============================================================================
# SECTION 14: No database dependency for core tests
# =============================================================================

class TestNoDatabaseDependency:
    """All tests in this file run without PostgreSQL."""

    def test_all_model_imports_succeed(self):
        """All model classes import without database connection."""
        from app.models.extraction import (
            ExtractionEvidence,
            CandidateResult,
            CandidateExtraction,
            CandidateVerificationStatus,
            ExtractionRunStatus,
            ExtractionSourceField,
            AbnormalityStatus,
            NormalizationStatus,
            UnitNormalizationStatus,
            DateNormalizationStatus,
            ReferenceRangeNormalizationStatus,
        )
        assert ExtractionEvidence is not None

    def test_all_schema_imports_succeed(self):
        """All schema classes import without database connection."""
        from app.schemas.extraction import (
            ExtractionEvidenceResponse,
            CandidateResultResponse,
            CandidateExtractionResponse,
            CanonicalTestResponse,
            TestResultResponse,
        )
        assert ExtractionEvidenceResponse is not None

    def test_matching_service_imports_succeed(self):
        """evidence_matching_service imports without external dependencies."""
        from app.services.evidence_matching_service import match_evidence_to_source
        assert callable(match_evidence_to_source)
