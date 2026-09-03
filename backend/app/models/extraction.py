"""
Candidate lab-result extraction ORM models.

These tables hold Gemini's *candidate* interpretation of a report's raw
text — never verified medical data. See the module docstring on
app.services.candidate_extraction_service for the full boundary
explanation.

Conceptual structure (per the locked task spec):

    Report
      -> CandidateExtraction   (one per successful/failed extraction run)
           -> CandidateResult[]  (one per extracted test/analyte)

Deliberately NOT included here (out of scope for this feature):
* any VERIFIED / doctor-confirmed state
* medical-record write-back
* abnormal/normal classification, reference-range interpretation
* timeline/trend tables
* fuzzy/LLM-based matching, or a "complete" normalization engine — this
  module only adds the deterministic foundation (CanonicalTest +
  CandidateResult.canonical_test_id/normalization_status); matching
  logic itself lives in app.services.normalization_service
* a comprehensive unit-conversion dictionary — CandidateResult also
  carries an additive, purely-deterministic unit-normalization
  foundation (normalized_value/normalized_unit/unit_normalization_status);
  the small, explicit conversion rule set itself lives in
  app.services.unit_normalization_service, never here and never Gemini
* free-form/natural-language date parsing — CandidateResult also
  carries an additive, purely-deterministic result-date normalization
  foundation (normalized_result_date/date_normalization_status)
  covering only a small, explicit set of unambiguous formats; the
  parsing/validation logic itself lives in
  app.services.date_normalization_service, never here and never Gemini
* reference-range interpretation or abnormal/normal classification —
  CandidateResult also carries additive, purely-deterministic
  reference-range normalization (normalized_reference_lower/upper /
  reference_range_inclusive_lower/upper /
  reference_range_normalization_status) and abnormality classification
  (abnormality_status); the parsing/validation/comparison logic lives
  in app.services.reference_range_normalization_service and
  app.services.abnormality_classification_service, never here and
  never Gemini

No table is created here (no Base.metadata.create_all()) — see the
accompanying Alembic migration.
"""
import enum
import uuid
from datetime import date as PyDate
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class ExtractionSourceField(str, enum.Enum):
    """Which report text column supplied the input to Gemini, kept for
    auditability. Mirrors the existing ingestion pipeline's own
    native-text-first, OCR-fallback priority — never a new source."""
    EXTRACTED_TEXT = "extracted_text"
    OCR_TEXT = "ocr_text"


class ExtractionRunStatus(str, enum.Enum):
    """Outcome of one Gemini extraction attempt. Deliberately only these
    two terminal values — this describes whether the AI call + validation
    succeeded, not the trustworthiness of the data it produced. See
    CandidateVerificationStatus for the (always-pending) trust state of
    the individual results themselves."""
    COMPLETED = "completed"
    FAILED = "failed"



class TestResultStatus(str, enum.Enum):
    """Trust state of a trusted TestResult row. A TestResult is created
    ONLY by a doctor's explicit verification action — never automatically
    by the extraction pipeline. The four states map to the doctor review
    workflow:

    * PENDING   — the row is created but the doctor has not yet acted
                  (transitional state, rare in practice)
    * VERIFIED  — the doctor confirmed the candidate is accurate
    * CORRECTED — the doctor accepted the result but corrected a value
    * REJECTED  — the doctor determined this candidate is not valid

    This enum is deliberately NOT the same as CandidateVerificationStatus
    (which tracks individual candidate-level trust). A TestResult is the
    final, trusted medical data representation of one test/analyte."""
    PENDING = "pending"
    VERIFIED = "verified"
    CORRECTED = "corrected"
    REJECTED = "rejected"


class CandidateVerificationStatus(str, enum.Enum):
    """Trust state of one CandidateResult.

    * PENDING   — the candidate has not yet been reviewed by a doctor
    * VERIFIED  — a doctor reviewed and approved the candidate data;
                  the trusted TestResult has been created
    * CORRECTED — a doctor reviewed the candidate, found errors, and
                  supplied corrected data; the trusted TestResult
                  contains the corrected values
    * REJECTED  — a doctor determined this candidate is invalid or
                  unusable; no trusted TestResult is created

    Only PENDING -> VERIFIED, PENDING -> CORRECTED, or PENDING -> REJECTED
    is allowed. All other states are terminal."""
    PENDING = "pending"
    VERIFIED = "verified"
    CORRECTED = "corrected"
    REJECTED = "rejected"


class NormalizationStatus(str, enum.Enum):
    """Outcome of deterministic backend test-name normalization (see
    app.services.normalization_service) for one CandidateResult.

    This is entirely separate from CandidateVerificationStatus — a
    result can be normalization_status=RESOLVED while still
    verification_status=PENDING; normalizing *what test this is* says
    nothing about whether the *value* has been clinically verified."""
    # A single, unambiguous canonical test was matched.
    RESOLVED = "resolved"
    # No known alias/canonical match for this source name at all.
    UNRESOLVED = "unresolved"
    # The source name matched more than one canonical test and was
    # deliberately left unresolved rather than guessing (task rule 4).
    AMBIGUOUS = "ambiguous"


class UnitNormalizationStatus(str, enum.Enum):
    """Outcome of deterministic backend unit normalization (see
    app.services.unit_normalization_service) for one CandidateResult.

    Entirely separate from both NormalizationStatus (test-name identity)
    and CandidateVerificationStatus (clinical trust). A result can be
    normalization_status=RESOLVED, unit_normalization_status=RESOLVED,
    and verification_status=PENDING all at once, or any other
    combination — resolving *what test this is* and *what the value
    means in a common unit* say nothing about whether the value has
    been clinically verified."""
    # A supported, deterministic conversion was applied.
    RESOLVED = "resolved"
    # Not enough information to attempt a conversion yet — the source
    # unit is missing, or the only matching conversion rule requires a
    # canonical test identity that hasn't been resolved (task rule 8:
    # an unresolved/ambiguous test identity must never receive a
    # test-specific conversion).
    UNRESOLVED = "unresolved"
    # The source unit (or the value itself, e.g. a qualitative result
    # like "Positive") is known but there is no configured conversion
    # rule for it. Never guessed around (task rule 5).
    UNSUPPORTED = "unsupported"


class DateNormalizationStatus(str, enum.Enum):
    """Outcome of deterministic backend result-date normalization (see
    app.services.date_normalization_service) for one CandidateResult.

    Entirely separate from NormalizationStatus (test-name identity),
    UnitNormalizationStatus (value/unit conversion), and
    CandidateVerificationStatus (clinical trust) — resolving *when this
    result is dated* says nothing about the other three."""
    # The raw result_date string matched one of the small set of
    # explicitly supported, unambiguous formats and denotes a valid
    # calendar date.
    RESOLVED = "resolved"
    # result_date is missing/blank, or it matches a supported format's
    # *shape* but is not safely resolvable — an invalid calendar date
    # (e.g. "2026-02-30", "31/04/2026" in a 30-day month) or a
    # day/month pair that is genuinely ambiguous between locales (e.g.
    # "03/04/2026") and was deliberately not guessed at.
    UNRESOLVED = "unresolved"
    # result_date does not match any of the currently supported date
    # formats at all (e.g. free-form text like "12 Jun 2026"). Never
    # guessed around.
    UNSUPPORTED = "unsupported"


class ReferenceRangeNormalizationStatus(str, enum.Enum):
    """Outcome of deterministic backend reference-range normalization
    (see app.services.reference_range_normalization_service) for one
    CandidateResult.

    Entirely separate from all other normalization statuses and from
    CandidateVerificationStatus — resolving *what numeric range this
    result should be compared against* says nothing about the other
    dimensions."""
    # The raw reference_range string matched one of the small set of
    # explicitly supported numeric-range formats and was successfully
    # parsed into structured lower/upper bounds.
    RESOLVED = "resolved"
    # reference_range is missing/blank, or matches a supported format's
    # shape but the bounds are mathematically inconsistent (e.g.
    # reversed lower > upper for a two-sided range).
    UNRESOLVED = "unresolved"
    # reference_range does not match any of the currently supported
    # range formats at all (e.g. free-form text like "normal range").
    # Never guessed around.
    UNSUPPORTED = "unsupported"


class AbnormalityStatus(str, enum.Enum):
    """Deterministic abnormality classification comparing a candidate's
    normalized value against its normalized reference range (see
    app.services.abnormality_classification_service).

    Entirely separate from all normalization statuses, from
    CandidateVerificationStatus, and from any clinical interpretation.
    This is a purely numeric comparison outcome — NOT a diagnosis, NOT
    a disease classification, NOT medical advice. A status of NORMAL
    means only 'the normalized value falls within the normalized
    reference bounds' and nothing more."""
    # The normalized value falls within the normalized reference bounds.
    NORMAL = "normal"
    # The normalized value is below the lower reference bound.
    LOW = "low"
    # The normalized value is above the upper reference bound.
    HIGH = "high"
    # Abnormality could not be determined — either the value, the
    # reference range, or both could not be normalized, or the units
    # are incompatible and comparison would be meaningless.
    UNRESOLVED = "unresolved"
    # The reference range is absent entirely (no reference provided on
    # the report) — comparison is not applicable.
    NOT_APPLICABLE = "not_applicable"


class CanonicalTest(Base):
    """A backend-curated canonical lab test identity (e.g. "Hemoglobin").

    Deliberately NOT derived from or writable by Gemini output — rows
    here only ever come from a fixed, deterministic set maintained by
    the backend (seeded via migration for this foundation). See
    app.services.normalization_service for how a CandidateResult's raw
    test_name is matched against these.
    """
    __tablename__ = "canonical_tests"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Stable canonical identifier (e.g. "HEMOGLOBIN"), independent of
    # display wording — what CandidateResult.canonical_test_id ultimately
    # points to and what the normalization service's alias dictionary
    # resolves *to*, never what it matches *against* (matching is done
    # against aliases/display text, not this code).
    code: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)

    display_name: Mapped[str] = mapped_column(String(255), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CandidateExtraction(Base):
    """One extraction run for one report.

    This is both the ExtractionRun and the candidate-extraction record.
    It represents one processing attempt: the extraction pipeline
    starts, calls Gemini, validates the response, and persists
    CandidateResults linked to this run.

    Version tracking (model_version, prompt_version, schema_version)
    records exactly which extraction configuration produced these
    candidates, enabling auditability and reproducibility.

    Timestamps (started_at, completed_at) track the run lifecycle
    precisely: started_at is set when the run is created, completed_at
    is set when the run reaches a terminal state (COMPLETED or FAILED).

    At most one row per report is created here in the common case (see
    app.services.candidate_extraction_service for the idempotency rule:
    an existing COMPLETED extraction is reused rather than re-run; a
    FAILED extraction may be retried, which appends a new row rather
    than overwriting the failed one, preserving an honest history of
    attempts).

    A partial unique index enforces, at the database level, that a given
    report can have at most one COMPLETED row — this is what actually
    closes the concurrent-duplicate-extraction race (two simultaneous
    requests can both pass an application-level "no existing completed
    extraction" check; only one of their inserts can win once this index
    exists). FAILED rows are deliberately excluded from the index since
    retries are expected to add new FAILED rows.
    """
    __tablename__ = "candidate_extractions"
    __table_args__ = (
        Index(
            "uq_candidate_extractions_report_completed",
            "report_id",
            unique=True,
            postgresql_where=text("status = 'completed'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Ownership is always implied through the report (which itself is
    # scoped to patient_id) — this table never stores patient_id
    # directly, so there is no separate identity for Gemini output to
    # disagree with.
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("reports.id"), nullable=False, index=True
    )

    status: Mapped[ExtractionRunStatus] = mapped_column(
        Enum(ExtractionRunStatus, name="extraction_run_status", native_enum=True),
        nullable=False,
    )

    source_field: Mapped[ExtractionSourceField] = mapped_column(
        Enum(ExtractionSourceField, name="extraction_source_field", native_enum=True),
        nullable=False,
    )

    # Set only when status == FAILED. Always a generic, client-safe
    # message (see app.services.gemini_extraction_service) — never a raw
    # SDK exception, stack trace, or API key.
    error_message: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # --- Version tracking for auditability ---
    # Records exactly which extraction configuration produced these
    # candidates, enabling later audit and reproducibility.
    model_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String(64), nullable=True)
    schema_version: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # --- Lifecycle timestamps ---
    # started_at: set when the run is initiated (always populated).
    # completed_at: set when the run reaches a terminal state.
    # Null completed_at means the run is still in progress or was
    # interrupted before reaching a terminal state.
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    results: Mapped[list["CandidateResult"]] = relationship(
        back_populates="extraction",
        cascade="all, delete-orphan",
        order_by="CandidateResult.created_at",
    )


class CandidateResult(Base):
    """One candidate test/analyte result extracted from a report's raw
    text by Gemini. Always unverified/pending (see
    CandidateVerificationStatus) and always carries the source evidence
    text it was extracted from.

    Values are stored exactly as the report states them — value/unit/
    result_date are never rewritten, no normal/abnormal classification.
    See task rules 13-14 of the original extraction feature. Test-name
    normalization (canonical_test_id / normalization_status), unit
    normalization (normalized_value / normalized_unit /
    unit_normalization_status), and result-date normalization
    (normalized_result_date / date_normalization_status) are all
    deterministic and purely additive alongside the untouched raw
    fields.
    """
    __tablename__ = "candidate_results"
    __table_args__ = (
        # A given extraction run should never contain the exact same
        # (test_name, value, evidence) triple twice — a lightweight guard
        # against a malformed Gemini response repeating an entry, without
        # inventing any de-duplication *logic* beyond exact-match.
        UniqueConstraint(
            "candidate_extraction_id",
            "test_name",
            "value",
            "evidence",
            name="uq_candidate_results_extraction_test_value_evidence",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    candidate_extraction_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_extractions.id"),
        nullable=False,
        index=True,
    )

    # Required — every candidate result must name what was measured.
    test_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Required — stored as text (not numeric) since Gemini is transcribing
    # what the document states, not producing a normalized measurement;
    # some report values are qualitative (e.g. "Positive", "Trace").
    value: Mapped[str] = mapped_column(String(255), nullable=False)

    unit: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Raw as printed on the report (e.g. "4.0-11.0 x10^9/L").
    # Parsed into structured bounds by the reference-range normalization
    # foundation (normalized_reference_lower/upper /
    # reference_range_normalization_status) — this field itself is never
    # overwritten.
    reference_range: Mapped[str | None] = mapped_column(String(255), nullable=True)

    specimen: Mapped[str | None] = mapped_column(String(255), nullable=True)

    # Raw as printed on the report (e.g. "12 Jun 2026", "2026-06-12").
    # Deliberately a string, not a Date column — parsing/normalizing
    # dates is out of scope for this feature (task rule 14) and a
    # mis-parsed date would silently corrupt otherwise-faithful data.
    result_date: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Required — the source text supporting this candidate value, always
    # taken from the report text Gemini was given, never from the
    # model's own reasoning and never from client input (task rule 6).
    evidence: Mapped[str] = mapped_column(Text, nullable=False)

    # Only populated if the Gemini API/schema mechanism in use exposes a
    # confidence score; null otherwise (task rule 5). Never invented.
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    verification_status: Mapped[CandidateVerificationStatus] = mapped_column(
        Enum(
            CandidateVerificationStatus,
            name="candidate_verification_status",
            native_enum=True,
        ),
        nullable=False,
        default=CandidateVerificationStatus.PENDING,
    )

    # --- Doctor review metadata ---
    # rejection_reason: populated only when verification_status == REJECTED.
    # Stores the doctor's stated reason for rejecting the candidate.
    # NULL for non-rejected candidates. Part of the same transaction
    # as the status change — either both persist or neither does.
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Deterministic test-name normalization (separate from
    # verification — see NormalizationStatus docstring). test_name above
    # remains untouched as the immutable, auditable source name Gemini
    # extracted; these two columns are purely additive.
    canonical_test_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("canonical_tests.id"),
        nullable=True,
        index=True,
    )

    normalization_status: Mapped[NormalizationStatus] = mapped_column(
        Enum(NormalizationStatus, name="normalization_status", native_enum=True),
        nullable=False,
        default=NormalizationStatus.UNRESOLVED,
    )

    # --- Deterministic unit normalization (separate from both
    # normalization_status above and verification_status — see
    # UnitNormalizationStatus docstring). `value`/`unit` above remain the
    # immutable, auditable source Gemini extracted; these three columns
    # are purely additive and are computed by
    # app.services.unit_normalization_service, never Gemini.
    #
    # Numeric (not Float) so a converted value is stored as an exact
    # decimal rather than a binary-floating-point approximation — see
    # task rule 9 ("do not silently introduce false precision"). Scale
    # (24, 12) intentionally exceeds what the source-precision policy in
    # app.services.unit_normalization_service currently needs (typical
    # lab values have at most a handful of decimal places) so the
    # database column is never the thing capping precision — the
    # deterministic policy in that service remains the only precision
    # authority; this column just needs to be able to hold whatever it
    # decides.
    normalized_value: Mapped["Decimal | None"] = mapped_column(
        Numeric(24, 12), nullable=True
    )

    normalized_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)

    unit_normalization_status: Mapped[UnitNormalizationStatus] = mapped_column(
        Enum(
            UnitNormalizationStatus,
            name="unit_normalization_status",
            native_enum=True,
        ),
        nullable=False,
        default=UnitNormalizationStatus.UNRESOLVED,
    )

    # --- Deterministic result-date normalization (separate from
    # normalization_status, unit_normalization_status, and
    # verification_status above). result_date above remains the
    # immutable, auditable source-of-truth string Gemini extracted —
    # never overwritten, never "corrected", never inferred when
    # missing. normalized_result_date/date_normalization_status are
    # purely additive and are computed by
    # app.services.date_normalization_service, never Gemini. A plain
    # PostgreSQL DATE — no time-of-day, no timezone — since a
    # normalized date represents only the calendar date explicitly
    # stated by the source (see the service's docstring).
    normalized_result_date: Mapped["PyDate | None"] = mapped_column(
        Date, nullable=True
    )

    date_normalization_status: Mapped[DateNormalizationStatus] = mapped_column(
        Enum(
            DateNormalizationStatus,
            name="date_normalization_status",
            native_enum=True,
        ),
        nullable=False,
        default=DateNormalizationStatus.UNRESOLVED,
    )

    # --- Deterministic reference-range normalization (separate from
    # all normalization statuses above and from verification_status).
    # reference_range above remains the immutable, auditable source
    # string Gemini extracted — never overwritten, never "corrected",
    # never inferred when missing. normalized_reference_lower/upper,
    # reference_range_inclusive_lower/upper, and
    # reference_range_normalization_status are purely additive and are
    # computed by app.services.reference_range_normalization_service,
    # never Gemini.
    #
    # Numeric (not Float) for the same exact-decimal reason as
    # normalized_value — see the unit normalization's PRECISION POLICY.
    normalized_reference_lower: Mapped["Decimal | None"] = mapped_column(
        Numeric(24, 12), nullable=True
    )
    normalized_reference_upper: Mapped["Decimal | None"] = mapped_column(
        Numeric(24, 12), nullable=True
    )

    # Whether the reference bound is inclusive. None when the range
    # could not be normalized (i.e. status != RESOLVED).
    reference_range_inclusive_lower: Mapped[bool | None] = mapped_column(
        nullable=True
    )
    reference_range_inclusive_upper: Mapped[bool | None] = mapped_column(
        nullable=True
    )

    reference_range_normalization_status: Mapped[
        ReferenceRangeNormalizationStatus
    ] = mapped_column(
        Enum(
            ReferenceRangeNormalizationStatus,
            name="reference_range_normalization_status",
            native_enum=True,
        ),
        nullable=False,
        default=ReferenceRangeNormalizationStatus.UNRESOLVED,
    )

    # --- Deterministic abnormality classification (separate from all
    # normalization statuses and from verification_status). This is
    # a purely numeric comparison outcome computed by
    # app.services.abnormality_classification_service — NOT a
    # diagnosis, NOT a disease classification, NOT medical advice.
    # abnormality_status == NORMAL means only 'the normalized value
    # falls within the normalized reference bounds' and nothing more.
    abnormality_status: Mapped[AbnormalityStatus] = mapped_column(
        Enum(
            AbnormalityStatus,
            name="abnormality_status",
            native_enum=True,
        ),
        nullable=False,
        default=AbnormalityStatus.UNRESOLVED,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    extraction: Mapped["CandidateExtraction"] = relationship(back_populates="results")
    canonical_test: Mapped["CanonicalTest | None"] = relationship()

    # Relationship to the trusted TestResult, if one was created by
    # doctor verification. None until a doctor reviews this candidate.
    trusted_result: Mapped["TestResult | None"] = relationship(
        back_populates="candidate",
        uselist=False,
    )

    # Structured provenance record linking this candidate back to the
    # exact location in the original PDF. Created automatically by the
    # extraction pipeline — never fabricated, never manually provided.
    evidence_record: Mapped["ExtractionEvidence | None"] = relationship(
        back_populates="candidate_result",
        uselist=False,
    )



class TestResult(Base):
    """Trusted medical test result — the ONLY representation of verified
    clinical data in the system.

    A TestResult is created ONLY by an explicit doctor verification
    action (VERIFY, CORRECT, or REJECT). It is NEVER automatically
    created by the extraction pipeline. The flow is:

        CandidateResult (PENDING)
            -> Doctor review
            -> TestResult (VERIFIED / CORRECTED / REJECTED)

    A TestResult stores the medically relevant normalized information
    that was already computed by the normalization chain on the
    CandidateResult, plus the doctor's verification metadata. This
    preserves the full provenance from PDF to trusted result.

    This model is designed so that:
    * VERIFIED: the doctor confirmed the candidate is accurate
    * CORRECTED: the doctor accepted but corrected specific values
    * REJECTED: the doctor determined this candidate is invalid
    * No automatic promotion from candidate to trusted is possible

    The table is intentionally sparse today — the future doctor-review
    workflow will populate it. This task only establishes the schema.
    """
    __tablename__ = "test_results"
    __table_args__ = (
        # Each candidate can have at most one trusted result.
        UniqueConstraint(
            "candidate_result_id",
            name="uq_test_results_candidate_result_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Which candidate result this trusted result derives from.
    candidate_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_results.id"),
        nullable=False,
        index=True,
    )

    # Which extraction run produced the source candidate.
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_extractions.id"),
        nullable=False,
        index=True,
    )

    # --- Trust state ---
    # Set ONLY by doctor verification action. Never automatically
    # promoted. PENDING is a transitional state (the row was just
    # created but the doctor hasn't acted yet).
    status: Mapped[TestResultStatus] = mapped_column(
        Enum(
            TestResultStatus,
            name="test_result_status",
            native_enum=True,
        ),
        nullable=False,
        default=TestResultStatus.PENDING,
    )

    # --- Trusted normalized data ---
    # These fields carry the medically relevant normalized information.
    # When status is VERIFIED, these represent the doctor-confirmed
    # values. When status is CORRECTED, these may differ from the
    # candidate's normalized values (the doctor overrode them).
    # When status is REJECTED, these fields may be NULL.

    # Canonical test identity (from normalization chain).
    canonical_test_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("canonical_tests.id"),
        nullable=True,
    )

    # Raw test name as originally extracted (for audit trail).
    test_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Raw value as originally extracted (for audit trail).
    raw_value: Mapped[str] = mapped_column(String(255), nullable=False)

    # Normalized value (from unit normalization chain).
    normalized_value: Mapped["Decimal | None"] = mapped_column(
        Numeric(24, 12), nullable=True
    )

    # Normalized unit (from unit normalization chain).
    normalized_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # Result date (from date normalization chain).
    result_date: Mapped["PyDate | None"] = mapped_column(Date, nullable=True)

    # Reference range bounds (from reference-range normalization chain).
    reference_range_lower: Mapped["Decimal | None"] = mapped_column(
        Numeric(24, 12), nullable=True
    )
    reference_range_upper: Mapped["Decimal | None"] = mapped_column(
        Numeric(24, 12), nullable=True
    )
    reference_range_inclusive_lower: Mapped[bool | None] = mapped_column(
        nullable=True
    )
    reference_range_inclusive_upper: Mapped[bool | None] = mapped_column(
        nullable=True
    )

    # Abnormality status (from abnormality classification chain).
    abnormality_status: Mapped[AbnormalityStatus] = mapped_column(
        Enum(
            AbnormalityStatus,
            name="abnormality_status",
            native_enum=True,
        ),
        nullable=False,
        default=AbnormalityStatus.UNRESOLVED,
    )

    # --- Verification metadata ---
    # doctor_id: which doctor made the verification decision.
    # verified_at: when the verification decision was made.
    # correction_note: free-text note when status is CORRECTED.
    # These are populated by the future doctor-review workflow.
    doctor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=True,
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    correction_note: Mapped[str | None] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # --- Relationships ---
    candidate: Mapped["CandidateResult"] = relationship(
        back_populates="trusted_result"
    )
    extraction_run: Mapped["CandidateExtraction"] = relationship()
    canonical_test: Mapped["CanonicalTest | None"] = relationship()
    doctor: Mapped["User | None"] = relationship()


class ExtractionEvidence(Base):
    """Structured provenance record for one CandidateResult's evidence.

    Every CandidateResult carries a raw `evidence` string (the source
    text the AI identified as supporting its extraction). ExtractionEvidence
    stores the VERIFIED source text — matched against the actual report
    extraction text (native PDF or OCR) — alongside metadata about *where*
    in the original PDF it came from.

    source_text is populated by matching the AI's evidence hint against
    the actual extracted report text. When the hint cannot be reliably
    located in the actual text, source_text is NULL (evidence unavailable)
    rather than fabricated from AI-only output. Fields are derived from
    the extraction pipeline's own output — never from client input,
    never fabricated. When provenance information is unavailable
    (e.g. page number cannot be determined for extracted_text, or the
    AI's evidence hint does not match the actual report text), the
    corresponding field is NULL rather than guessed.

    This table is immutable once created during extraction — it is
    never silently overwritten during retries. A retry creates a new
    CandidateExtraction with its own ExtractionEvidence rows, so
    historical provenance is preserved.

    Ownership is always derived through the report/candidate chain:
    ExtractionEvidence -> CandidateResult -> CandidateExtraction ->
    Report -> patient_id. No direct patient_id is stored here.
    """
    __tablename__ = "extraction_evidence"
    __table_args__ = (
        # Each candidate result has exactly one evidence record.
        UniqueConstraint(
            "candidate_result_id",
            name="uq_extraction_evidence_candidate_result_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Which candidate result this evidence belongs to.
    candidate_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_results.id"),
        nullable=False,
        index=True,
    )

    # Which extraction run produced this evidence (for audit trail and
    # distinguishing between retry attempts).
    extraction_run_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_extractions.id"),
        nullable=False,
        index=True,
    )

    # Which report this evidence derives from (for ownership checks
    # and direct report-level querying).
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reports.id"),
        nullable=False,
        index=True,
    )

    # Which report text field was the source: extracted_text (native
    # PDF text) or ocr_text (OCR fallback). Matches the extraction
    # run's source_field.
    source_column: Mapped[ExtractionSourceField] = mapped_column(
        Enum(
            ExtractionSourceField,
            name="extraction_source_field",
            native_enum=True,
        ),
        nullable=False,
    )

    # Page number within the PDF where this evidence was found.
    # NULL when page information is unavailable (e.g. native text
    # extraction doesn't always provide page-level granularity).
    # Never guessed — NULL means unknown, not page 0 or page 1.
    page_number: Mapped[int | None] = mapped_column(nullable=True)

    # The exact supporting source text from the actual report extraction
    # (native PDF text or OCR output), matched/verified against the
    # AI's evidence hint. NULL when the AI's evidence hint could not be
    # reliably located in the actual report text — evidence is
    # unavailable rather than fabricated. When non-NULL, this text is
    # always derived from the authoritative report source, never from
    # AI output alone.
    source_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Bounding box information (x, y, width, height) within the PDF
    # page, when available from the extraction pipeline. NULL when
    # unavailable — never fabricated.
    bounding_box_x: Mapped[float | None] = mapped_column(Float, nullable=True)
    bounding_box_y: Mapped[float | None] = mapped_column(Float, nullable=True)
    bounding_box_width: Mapped[float | None] = mapped_column(Float, nullable=True)
    bounding_box_height: Mapped[float | None] = mapped_column(Float, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    # --- Relationships ---
    candidate_result: Mapped["CandidateResult"] = relationship(
        back_populates="evidence_record"
    )
    extraction_run: Mapped["CandidateExtraction"] = relationship()
    report: Mapped["Report"] = relationship()

