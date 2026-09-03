"""Add rejection_reason to candidate_results

Stores the doctor's stated reason for rejecting a candidate. The column
is nullable — only populated when verification_status == REJECTED. Part
of the same transaction as the status change for data integrity.

Revision ID: d1f9e3a7b4c5
Down revision: f5c3b2a8d9e6
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = "d1f9e3a7b4c5"
down_revision: str = "f5c3b2a8d9e6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "candidate_results",
        sa.Column("rejection_reason", sa.Text(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("candidate_results", "rejection_reason")
