"""create candidate extraction tables

Revision ID: f1c3a7d9b2e6
Revises: e5b8d2f4c9a1
Create Date: 2026-08-29 00:00:02

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "f1c3a7d9b2e6"
down_revision: Union[str, None] = "e5b8d2f4c9a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "candidate_extractions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "report_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reports.id"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("completed", "failed", name="extraction_run_status"),
            nullable=False,
        ),
        sa.Column(
            "source_field",
            sa.Enum(
                "extracted_text", "ocr_text", name="extraction_source_field"
            ),
            nullable=False,
        ),
        sa.Column("error_message", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_candidate_extractions_report_id",
        "candidate_extractions",
        ["report_id"],
    )

    op.create_table(
        "candidate_results",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "candidate_extraction_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("candidate_extractions.id"),
            nullable=False,
        ),
        sa.Column("test_name", sa.String(length=255), nullable=False),
        sa.Column("value", sa.String(length=255), nullable=False),
        sa.Column("unit", sa.String(length=64), nullable=True),
        sa.Column("reference_range", sa.String(length=255), nullable=True),
        sa.Column("specimen", sa.String(length=255), nullable=True),
        sa.Column("result_date", sa.String(length=64), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column(
            "verification_status",
            sa.Enum("pending", name="candidate_verification_status"),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "candidate_extraction_id",
            "test_name",
            "value",
            "evidence",
            name="uq_candidate_results_extraction_test_value_evidence",
        ),
    )
    op.create_index(
        "ix_candidate_results_candidate_extraction_id",
        "candidate_results",
        ["candidate_extraction_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_results_candidate_extraction_id",
        table_name="candidate_results",
    )
    op.drop_table("candidate_results")
    op.execute("DROP TYPE IF EXISTS candidate_verification_status")

    op.drop_index(
        "ix_candidate_extractions_report_id", table_name="candidate_extractions"
    )
    op.drop_table("candidate_extractions")
    op.execute("DROP TYPE IF EXISTS extraction_run_status")
    op.execute("DROP TYPE IF EXISTS extraction_source_field")
