"""add extraction run version tracking and test result architecture

Revision ID: e7b3c2d1a9f4
Revises: f8a2c6b1d4e7
Create Date: 2026-09-01 00:00:00

Adds version-tracking columns to candidate_extractions (the ExtractionRun)
for auditability of which extraction configuration produced candidates,
and creates the test_results table as the ONLY representation of trusted
medical data — never populated automatically by the extraction pipeline.

candidate_extractions changes:
  * model_version, prompt_version, schema_version — version metadata
  * started_at, completed_at — lifecycle timestamps

test_results table:
  * Stores doctor-verified/corrected/rejected trusted medical results
  * Linked to candidate_result and extraction_run for full provenance
  * Includes verification metadata (doctor_id, verified_at, correction_note)
  * Carries the medically relevant normalized data from the normalization chain
  * Each candidate can have at most one trusted result
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e7b3c2d1a9f4"
down_revision: Union[str, None] = "f8a2c6b1d4e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Add version-tracking columns to candidate_extractions ---
    op.add_column(
        "candidate_extractions",
        sa.Column("model_version", sa.String(64), nullable=True),
    )
    op.add_column(
        "candidate_extractions",
        sa.Column("prompt_version", sa.String(64), nullable=True),
    )
    op.add_column(
        "candidate_extractions",
        sa.Column("schema_version", sa.String(64), nullable=True),
    )

    # --- Add lifecycle timestamps to candidate_extractions ---
    # started_at is NOT nullable at the application level but we add it
    # as nullable first so existing rows don't fail. The application code
    # always sets it for new rows.
    op.add_column(
        "candidate_extractions",
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "candidate_extractions",
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # --- Create test_result_status enum type ---
    test_result_status = sa.Enum(
        "pending",
        "verified",
        "corrected",
        "rejected",
        name="test_result_status",
    )
    test_result_status.create(op.get_bind(), checkfirst=True)

    # --- Create test_results table ---
    op.create_table(
        "test_results",
        sa.Column(
            "id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            primary_key=True,
        ),
        sa.Column(
            "candidate_result_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidate_results.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "extraction_run_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidate_extractions.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "status",
            test_result_status,
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "canonical_test_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("canonical_tests.id"),
            nullable=True,
        ),
        sa.Column("test_name", sa.String(255), nullable=False),
        sa.Column("raw_value", sa.String(255), nullable=False),
        sa.Column("normalized_value", sa.Numeric(24, 12), nullable=True),
        sa.Column("normalized_unit", sa.String(64), nullable=True),
        sa.Column("result_date", sa.Date(), nullable=True),
        sa.Column("reference_range_lower", sa.Numeric(24, 12), nullable=True),
        sa.Column("reference_range_upper", sa.Numeric(24, 12), nullable=True),
        sa.Column("reference_range_inclusive_lower", sa.Boolean(), nullable=True),
        sa.Column("reference_range_inclusive_upper", sa.Boolean(), nullable=True),
        sa.Column(
            "abnormality_status",
            sa.Enum(
                "normal",
                "low",
                "high",
                "unresolved",
                "not_applicable",
                name="abnormality_status",
            ),
            nullable=False,
            server_default="unresolved",
        ),
        sa.Column(
            "doctor_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=True,
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("correction_note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Unique constraint: each candidate can have at most one trusted result.
    op.create_unique_constraint(
        "uq_test_results_candidate_result_id",
        "test_results",
        ["candidate_result_id"],
    )


def downgrade() -> None:
    op.drop_table("test_results")
    op.execute("DROP TYPE IF EXISTS test_result_status")
    op.drop_column("candidate_extractions", "completed_at")
    op.drop_column("candidate_extractions", "started_at")
    op.drop_column("candidate_extractions", "schema_version")
    op.drop_column("candidate_extractions", "prompt_version")
    op.drop_column("candidate_extractions", "model_version")
