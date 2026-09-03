"""add extracted_text to reports

Revision ID: b3f1a9c8d2e7
Revises: d7a2f9c1e6b4
Create Date: 2026-08-29 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "b3f1a9c8d2e7"
down_revision: Union[str, None] = "d7a2f9c1e6b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Nullable: only populated once a report has gone through machine-
    # readable text extraction (PROCESSING -> COMPLETED). Existing rows
    # are all UPLOADED today, so no backfill is needed.
    op.add_column(
        "reports",
        sa.Column("extracted_text", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("reports", "extracted_text")
