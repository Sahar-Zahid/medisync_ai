"""
Immutable verification-history ORM model.

Records every successful doctor action (VERIFY / CORRECT / REJECT) on a
candidate result. This table is STRICTLY APPEND-ONLY:

* There is no update endpoint, no delete endpoint, no edit endpoint.
* The application never modifies an existing row after creation.
* Each successful action appends one new row in the SAME database
  transaction as the action itself — if the action rolls back, its
  history row rolls back with it.

Verification history is NOT trusted medical data:
* Creating a history row never creates a TestResult.
* It never changes medical values.
* It never bypasses the identity checkpoint or the VERIFY/CORRECT/REJECT
  state machine.

Each row snapshots the candidate's state BEFORE the action (old_*,
captured before any mutation) and, where applicable, the final
backend-derived state AFTER the action (new_*). For REJECT there is no
meaningful new state, so all new_* columns are NULL. All ownership and
audit fields (patient_id, report_id, candidate_id, doctor_id, action,
reason, created_at) are derived server-side — never client-supplied.

No AI-generated diagnoses or medical advice are ever stored here.
"""
import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.extraction import AbnormalityStatus


class VerificationAction(str, enum.Enum):
    """Which doctor action produced this history row.

    Stored lowercase to match project enum conventions; the enum members
    are the action identities VERIFY / CORRECT / REJECT.
    """
    VERIFY = "verify"
    CORRECT = "correct"
    REJECT = "reject"


class VerificationHistory(Base):
    __tablename__ = "verification_history"
    __table_args__ = (
        # Chronological read path: list a report's history in order.
        Index(
            "ix_verification_history_report_created",
            "report_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # --- Ownership/audit metadata (server-derived, never client input) ---
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_results.id"),
        nullable=False,
        index=True,
    )
    report_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("reports.id"),
        nullable=False,
        index=True,
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    action: Mapped[VerificationAction] = mapped_column(
        Enum(VerificationAction, name="verification_action", native_enum=True),
        nullable=False,
    )

    # --- Original candidate snapshot (captured BEFORE the action mutated
    # anything). Raw strings mirror the CandidateResult source fields;
    # normalized values mirror the deterministic normalization columns. ---
    old_test_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    old_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    old_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    old_normalized_value: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 12), nullable=True
    )
    old_normalized_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    old_canonical_test_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("canonical_tests.id"),
        nullable=True,
    )
    old_reference_range: Mapped[str | None] = mapped_column(String(255), nullable=True)
    old_result_date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    old_abnormality_status: Mapped[AbnormalityStatus | None] = mapped_column(
        Enum(
            AbnormalityStatus,
            name="abnormality_status",
            native_enum=True,
        ),
        nullable=True,
    )

    # --- New/final snapshot where applicable. NULL when the action has
    # no meaningful new value (e.g. REJECT). Values are the actual final
    # backend-derived values, never client-supplied normalized/derived
    # data. ---
    new_test_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    new_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    new_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_normalized_value: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 12), nullable=True
    )
    new_normalized_unit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_canonical_test_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("canonical_tests.id"),
        nullable=True,
    )
    new_reference_range: Mapped[str | None] = mapped_column(String(255), nullable=True)
    new_result_date: Mapped[str | None] = mapped_column(String(64), nullable=True)
    new_abnormality_status: Mapped[AbnormalityStatus | None] = mapped_column(
        Enum(
            AbnormalityStatus,
            name="abnormality_status",
            native_enum=True,
        ),
        nullable=True,
    )

    # Validated correction reason (CORRECT) or persisted rejection reason
    # (REJECT). NULL for VERIFY, which requires no reason.
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Server-generated timestamp — the application never sets this; the
    # database populates it at insert time.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )