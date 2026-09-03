"""
Report ORM model.

Minimal foundation for the patient report-upload feature only. No
AI-extraction, verification, or test-result fields belong here yet — this
defines just enough to record that a patient uploaded a file and where it
lives in private storage.

No table is created here (no Base.metadata.create_all()) — see the Alembic
migration that accompanies this model.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IdentityCheckStatus(str, enum.Enum):
    """Result of the deterministic patient-identity checkpoint.

    This is a PURELY SERVER-SIDE concept — the frontend never decides
    whether identity matches. The states are:

    * NOT_CHECKED  — identity has not yet been compared
    * MATCH        — extracted report identity matches the patient account
    * MISMATCH     — extracted report identity does NOT match the account
    * UNRESOLVED   — insufficient evidence to decide (missing fields)

    A MISMATCH is a HARD BLOCK: it can never become trusted data, even
    with doctor confirmation. An UNRESOLVED result may be explicitly
    confirmed by an authorized doctor via the dedicated endpoint — never
    silently. A MATCH does not require doctor confirmation.
    """
    NOT_CHECKED = "not_checked"
    MATCH = "match"
    MISMATCH = "mismatch"
    UNRESOLVED = "unresolved"


class ReportStatus(str, enum.Enum):
    """Report lifecycle status.

    Only the states needed for today's system: a report starts UPLOADED,
    may later move to PROCESSING, and from there to COMPLETED or FAILED.
    No AI-specific states (OCR, extraction, verification, etc.) belong
    here yet — see app.services.report_service.ALLOWED_STATUS_TRANSITIONS
    for the transition rules, and note that nothing in this codebase
    currently moves a report out of UPLOADED — that's for a future
    processing-pipeline feature to drive.
    """
    UPLOADED = "uploaded"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"


class Report(Base):
    __tablename__ = "reports"
    __table_args__ = (
        # Enforces "same patient + same file bytes = duplicate" at the
        # database level, not just in application code, so two
        # simultaneous uploads of the same PDF by the same patient can't
        # both succeed. Scoped to patient_id, so the same PDF uploaded by
        # two different patients is never treated as a duplicate.
        UniqueConstraint(
            "patient_id", "sha256_hash", name="uq_reports_patient_id_sha256_hash"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # Ownership always comes from the authenticated session at upload time
    # (see app/routers/reports.py) — never from a client-supplied value.
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True
    )

    # The filename the patient's browser reported. Display-only — never
    # used to construct a filesystem path (see app/core/storage.py).
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)

    # Server-generated private storage identifier (relative path under the
    # private storage root). Never returned by the API — see
    # app/schemas/report.py.
    storage_path: Mapped[str] = mapped_column(String(512), nullable=False)

    # SHA-256 of the actual uploaded PDF bytes (hex digest, always 64
    # chars), used only for per-patient exact-duplicate detection — never
    # filename/size/timestamp. The raw file itself is never stored here,
    # only this hash. Combined with patient_id in the unique constraint
    # above.
    sha256_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    status: Mapped[ReportStatus] = mapped_column(
        Enum(ReportStatus, name="report_status", native_enum=True),
        nullable=False,
        default=ReportStatus.UPLOADED,
    )

    # Raw text pulled directly out of the PDF's own machine-readable
    # content by app.services.pdf_extraction_service — nothing more. No
    # OCR output, no AI/Gemini output, no structured lab values, no
    # normalization, and no medical interpretation belongs in this
    # column; those are separate future fields so this stays a faithful
    # record of "what the PDF itself contained", untouched. Null until a
    # report has been (successfully) processed.
    extracted_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Raw text produced by app.services.ocr_extraction_service's local
    # OCR fallback, used only when extracted_text above could not be
    # populated (an image-only/scanned PDF with no machine-readable
    # text). Kept strictly separate from extracted_text — never merges
    # into or overwrites it — so it stays clear which text came directly
    # from the PDF versus from OCR. No AI/Gemini output, no structured
    # lab values, no normalization, and no medical interpretation belongs
    # in this column either. Null unless the OCR fallback ran and
    # succeeded.
    ocr_text: Mapped[str | None] = mapped_column(Text, nullable=True)

    # --- Patient identity checkpoint fields ---
    # These store the identity information extracted from the report
    # text (patient name, DOB, MRN) and the outcome of comparing
    # that against the authenticated patient account. The identity
    # check is the ONLY gate that protects against a report from one
    # patient silently becoming trusted data under another account.
   #
    # Extracted identity values: raw strings parsed from the report's
    # extracted_text or ocr_text by the deterministic identity
    # extraction service. NULL when not found. These are preserved
    # as-is for auditability — never overwritten, never normalized
    # beyond the comparison itself.
    patient_name_extracted: Mapped[str | None] = mapped_column(
        String(255), nullable=True
    )
    patient_dob_extracted: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    patient_mrn_extracted: Mapped[str | None] = mapped_column(
        String(128), nullable=True
    )

    # Identity check outcome: deterministic comparison of extracted
    # identity against the authenticated patient account.
    identity_check_status: Mapped[IdentityCheckStatus] = mapped_column(
        Enum(
            IdentityCheckStatus,
            name="identity_check_status",
            native_enum=True,
        ),
        nullable=False,
        default=IdentityCheckStatus.NOT_CHECKED,
    )

    # Doctor confirmation: explicit acknowledgment of the identity
    # checkpoint. Only set via the dedicated doctor confirmation endpoint.
    # NULL until a doctor acts. Only UNRESOLVED results are confirmable —
    # a deterministic MISMATCH can never be confirmed as a trust override.
    identity_confirmed_by_doctor: Mapped[bool] = mapped_column(
        nullable=False, default=False
    )
    identity_confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    identity_confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
