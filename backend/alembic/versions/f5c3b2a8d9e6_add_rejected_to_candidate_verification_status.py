"""Add 'rejected' to candidate_verification_status enum

Adds the REJECTED value to the candidate_verification_status PostgreSQL
enum so that the doctor rejection workflow can mark candidates as
rejected without creating trusted medical data.

The REJECTED state is terminal — a rejected candidate cannot be
re-rejected, re-verified, or re-corrected.

Revision ID: f5c3b2a8d9e6
Down revision: e4b2a1d7c8f5
"""
from alembic import op
import sqlalchemy as sa


# revision identifiers
revision: str = "f5c3b2a8d9e6"
down_revision: str = "e4b2a1d7c8f5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the 'rejected' value to the enum type.
    # ALTER TYPE ... ADD VALUE IF NOT EXISTS is PostgreSQL-specific
    # and idempotent — safe to re-run if the value already exists.
    op.execute(
        "ALTER TYPE candidate_verification_status "
        "ADD VALUE IF NOT EXISTS 'rejected'"
    )


def downgrade() -> None:
    # PostgreSQL does not support removing individual enum values.
    # Intentionally a no-op — see e4b2a1d7c8f5 for the rationale.
    pass
