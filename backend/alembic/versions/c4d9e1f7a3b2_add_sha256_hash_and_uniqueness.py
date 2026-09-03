"""add sha256_hash and per-patient uniqueness to reports

Revision ID: c4d9e1f7a3b2
Revises: 8f2c6a1d4b7e
Create Date: 2026-08-29 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c4d9e1f7a3b2"
down_revision: Union[str, None] = "8f2c6a1d4b7e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column("sha256_hash", sa.String(length=64), nullable=False),
    )
    # Enforces "same patient + same file bytes = duplicate" at the
    # database level (not just in application code), so two simultaneous
    # uploads of the same PDF by the same patient can't both succeed.
    # Also serves as the index used by the (patient_id, sha256_hash)
    # duplicate lookup.
    op.create_unique_constraint(
        op.f("uq_reports_patient_id_sha256_hash"),
        "reports",
        ["patient_id", "sha256_hash"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_reports_patient_id_sha256_hash"), "reports", type_="unique"
    )
    op.drop_column("reports", "sha256_hash")
