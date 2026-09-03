"""Add 'corrected' to candidate_verification_status enum

Adds the CORRECTED value to the candidate_verification_status PostgreSQL
enum so that the doctor correction workflow can mark candidates as
corrected while preserving the original extracted data.

The CORRECTED state is terminal — a corrected candidate cannot be
re-corrected or re-verified.

Revision ID: e4b2a1d7c8f5
Down revision: d3e5f7a9b1c4
"""
from alembic import op
import sqlalchemy as sa


from typing import Union

# revision identifiers
revision: str = "e4b2a1d7c8f5"
down_revision: Union[str, None] = "d3e5f7a9b1c4"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Add the 'corrected' value to the enum type.
    # ALTER TYPE ... ADD VALUE IF NOT EXISTS is PostgreSQL-specific
    # and idempotent — safe to re-run if the value already exists.
    op.execute(
        "ALTER TYPE candidate_verification_status "
        "ADD VALUE IF NOT EXISTS 'corrected'"
    )


def downgrade() -> None:
    # PostgreSQL does not support removing individual enum values.
    # A full downgrade would require recreating the enum type and
    # rewriting the column, which is risky and not necessary for
    # a dev migration. This is intentionally a no-op.
    #
    # To fully downgrade, you would need to:
    # 1. Create a temporary column with the old enum type
    # 2. Copy data, excluding 'corrected' rows
    # 3. Drop and recreate the enum type without 'corrected'
    # 4. Rename columns
    pass
