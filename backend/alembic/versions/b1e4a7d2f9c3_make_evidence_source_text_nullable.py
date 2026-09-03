"""make extraction_evidence.source_text nullable

Revision ID: b1e4a7d2f9c3
Revises: a9d2f5c8e3b1
Create Date: 2026-09-01 00:00:00

Makes extraction_evidence.source_text nullable so that when the AI's
evidence hint cannot be reliably matched against the actual report
text, the evidence record is created with source_text=NULL (evidence
unavailable) rather than being fabricated from AI-only output.

Previously source_text was NOT NULL and was populated directly from
the AI's output. Now it is populated only when the AI's hint is
verified as present in the actual extracted/OCR report text.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b1e4a7d2f9c3"
down_revision: Union[str, None] = "a9d2f5c8e3b1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Allow source_text to be NULL for cases where the AI's evidence
    # hint could not be matched against the actual report text.
    op.alter_column(
        "extraction_evidence",
        "source_text",
        existing_type=sa.Text(),
        nullable=True,
    )


def downgrade() -> None:
    # Before making source_text NOT NULL again, delete any existing
    # rows with NULL source_text (these represent unmatched evidence
    # that was never authoritative provenance anyway).
    op.execute(
        "DELETE FROM extraction_evidence WHERE source_text IS NULL"
    )
    op.alter_column(
        "extraction_evidence",
        "source_text",
        existing_type=sa.Text(),
        nullable=False,
    )
