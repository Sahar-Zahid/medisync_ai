"""add ocr_text to reports

Revision ID: e5b8d2f4c9a1
Revises: b3f1a9c8d2e7
Create Date: 2026-08-29 00:00:01

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e5b8d2f4c9a1"
down_revision: Union[str, None] = "b3f1a9c8d2e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable: only populated when the OCR fallback runs (a
    # scanned/image-only PDF with no machine-readable text) and
    # succeeds. Existing rows have no OCR output, so no backfill is
    # needed. Kept as a separate column from extracted_text on purpose —
    # see the model docstring.
    op.add_column(
        "reports",
        sa.Column("ocr_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reports", "ocr_text")
