"""add processing/completed/failed to report_status enum

Revision ID: d7a2f9c1e6b4
Revises: c4d9e1f7a3b2
Create Date: 2026-08-29 00:00:00

"""
from typing import Sequence, Union

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d7a2f9c1e6b4"
down_revision: Union[str, None] = "c4d9e1f7a3b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Existing rows only ever use 'uploaded' today, so this migration only
# needs to add the new values to the type — no data backfill required,
# and the new values are never referenced within this same migration
# (Postgres disallows using a freshly-added enum value in the same
# transaction that added it).
_NEW_VALUES = ["processing", "completed", "failed"]


def upgrade() -> None:
    for value in _NEW_VALUES:
        op.execute(f"ALTER TYPE report_status ADD VALUE IF NOT EXISTS '{value}'")


def downgrade() -> None:
    # Postgres has no ALTER TYPE ... DROP VALUE. Downgrading a value
    # removal would require rebuilding the enum type (create new type,
    # cast the column over, drop the old type) and is only safe if no row
    # currently uses the values being removed. Deliberately not
    # implemented here: this feature never causes any row to actually
    # take on 'processing'/'completed'/'failed' (nothing drives a
    # transition yet), so there is nothing for a downgrade to migrate
    # away from in practice.
    raise NotImplementedError(
        "Downgrading report_status enum values is not supported. "
        "See migration docstring."
    )
