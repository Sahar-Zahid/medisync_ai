"""add extraction evidence table for source tracking

Revision ID: a9d2f5c8e3b1
Revises: e7b3c2d1a9f4
Create Date: 2026-09-01 00:00:00

Creates the extraction_evidence table providing structured provenance
for every CandidateResult's evidence. Each row links a candidate result
back to the exact location in the original PDF where its supporting
source text was found.

extraction_evidence table:
  * Links to candidate_result, extraction_run, and report via FKs
  * Stores source_column (extracted_text vs ocr_text)
  * Stores page_number when available (NULL when unknown)
  * Stores source_text (the exact evidence string)
  * Stores bounding box coordinates when available (all NULL today)
  * Unique on candidate_result_id (one evidence record per candidate)
  * Immutable once created — never overwritten during retries
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a9d2f5c8e3b1"
down_revision: Union[str, None] = "e7b3c2d1a9f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- Create extraction_evidence table ---
    op.create_table(
        "extraction_evidence",
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
            "report_id",
            sa.dialects.postgresql.UUID(as_uuid=True),
            sa.ForeignKey("reports.id"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "source_column",
            sa.Enum(
                "extracted_text",
                "ocr_text",
                name="extraction_source_field",
                native_enum=True,
            ),
            nullable=False,
        ),
        # Page number within the PDF. NULL when page info is unavailable
        # (native text extraction doesn't always provide page-level
        # granularity). Never guessed — NULL means unknown.
        sa.Column("page_number", sa.Integer(), nullable=True),
        # The exact supporting source text from the report. Immutable
        # provenance record matching CandidateResult.evidence.
        sa.Column("source_text", sa.Text(), nullable=False),
        # Bounding box coordinates within the PDF page. All NULL today
        # until the extraction pipeline provides page-level positioning.
        sa.Column("bounding_box_x", sa.Float(), nullable=True),
        sa.Column("bounding_box_y", sa.Float(), nullable=True),
        sa.Column("bounding_box_width", sa.Float(), nullable=True),
        sa.Column("bounding_box_height", sa.Float(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # Unique constraint: each candidate result has exactly one evidence record.
    op.create_unique_constraint(
        "uq_extraction_evidence_candidate_result_id",
        "extraction_evidence",
        ["candidate_result_id"],
    )


def downgrade() -> None:
    op.drop_table("extraction_evidence")
    # Note: extraction_source_field enum type is shared with
    # candidate_extractions, so we do NOT drop the type here.
