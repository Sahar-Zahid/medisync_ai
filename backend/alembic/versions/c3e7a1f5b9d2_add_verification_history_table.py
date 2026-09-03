"""add immutable verification history table

Revision ID: c3e7a1f5b9d2
Revises: b2c8e4a6d7f1
Create Date: 2026-09-03 00:00:00

Creates the verification_history table — an append-only, immutable audit
log of successful doctor actions (VERIFY / CORRECT / REJECT) on
candidate results.

verification_history table:
  * Links to candidate_result, report, and users (patient + doctor) via FKs
  * action: verification_action enum (verify, correct, reject)
  * old_* columns: original candidate snapshot captured before the action
  * new_* columns: final backend-derived snapshot where applicable (NULL
    for REJECT — no meaningful new state)
  * reason: validated correction / rejection reason (NULL for VERIFY)
  * created_at: server-generated timestamp
  * Append-only by construction — no update/delete endpoints exist, and
    each successful action inserts a NEW row in the same transaction as
    the action itself
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c3e7a1f5b9d2"
down_revision: Union[str, None] = "b2c8e4a6d7f1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Create verification_action enum type ---
    verification_action = sa.Enum(
        "verify",
        "correct",
        "reject",
        name="verification_action",
    )
    verification_action.create(op.get_bind(), checkfirst=True)

    # --- Create verification_history table ---
    op.create_table(
        "verification_history",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "candidate_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidate_results.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "report_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reports.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "patient_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "doctor_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "action",
            verification_action,
            nullable=False,
        ),
        # --- Original candidate snapshot (before the action) ---
        sa.Column("old_test_name", sa.String(255), nullable=True),
        sa.Column("old_value", sa.String(255), nullable=True),
        sa.Column("old_unit", sa.String(64), nullable=True),
        sa.Column("old_normalized_value", sa.Numeric(24, 12), nullable=True),
        sa.Column("old_normalized_unit", sa.String(64), nullable=True),
        sa.Column(
            "old_canonical_test_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("canonical_tests.id"),
            nullable=True,
        ),
        sa.Column("old_reference_range", sa.String(255), nullable=True),
        sa.Column("old_result_date", sa.String(64), nullable=True),
        sa.Column(
            "old_abnormality_status",
            sa.Enum(
                "normal",
                "low",
                "high",
                "unresolved",
                "not_applicable",
                name="abnormality_status",
            ),
            nullable=True,
        ),
        # --- New/final snapshot (where applicable; NULL for REJECT) ---
        sa.Column("new_test_name", sa.String(255), nullable=True),
        sa.Column("new_value", sa.String(255), nullable=True),
        sa.Column("new_unit", sa.String(64), nullable=True),
        sa.Column("new_normalized_value", sa.Numeric(24, 12), nullable=True),
        sa.Column("new_normalized_unit", sa.String(64), nullable=True),
        sa.Column(
            "new_canonical_test_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("canonical_tests.id"),
            nullable=True,
        ),
        sa.Column("new_reference_range", sa.String(255), nullable=True),
        sa.Column("new_result_date", sa.String(64), nullable=True),
        sa.Column(
            "new_abnormality_status",
            sa.Enum(
                "normal",
                "low",
                "high",
                "unresolved",
                "not_applicable",
                name="abnormality_status",
            ),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Chronological read path: list a report's history in order.
    op.create_index(
        "ix_verification_history_report_created",
        "verification_history",
        ["report_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_verification_history_report_created",
        table_name="verification_history",
    )
    op.drop_table("verification_history")
    op.execute("DROP TYPE IF EXISTS verification_action")
    # NOTE: abnormality_status enum type is shared with candidate_results
    # and test_results, so we do NOT drop it here.