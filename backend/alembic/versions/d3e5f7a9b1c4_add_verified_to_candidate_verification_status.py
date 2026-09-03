"""Add VERIFIED to candidate_verification_status enum.

Revision ID: d3e5f7a9b1c4
Revises: c2d4e6f8a0b1
Create Date: 2026-09-02 00:00:00.000000

"""
from typing import Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "d3e5f7a9b1c4"
down_revision: Union[str, None] = "c2d4e6f8a0b1"
branch_labels: Union[str, None] = None
depends_on: Union[str, None] = None


def upgrade() -> None:
    # Add the VERIFIED value to the candidate_verification_status enum type.
    # PostgreSQL supports adding values to existing enums without dropping data.
    op.execute(
        "ALTER TYPE candidate_verification_status ADD VALUE IF NOT EXISTS 'verified'"
    )


def downgrade() -> None:
    # PostgreSQL does not support removing values from an enum type.
    # To downgrade, we would need to recreate the enum type, which is
    # destructive. For safety, we leave this as a no-op and document
    # that the enum value cannot be removed once added.
    pass
