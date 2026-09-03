"""add unit normalization foundation

Revision ID: c8f4b2e6a1d3
Revises: b7e2c5a1d9f4
Create Date: 2026-08-30 00:00:00

Adds the deterministic unit-normalization foundation described in
app.services.unit_normalization_service:

* candidate_results.normalized_value / normalized_unit /
  unit_normalization_status — additive columns recording the
  deterministic unit-conversion outcome for a CandidateResult, without
  ever touching candidate_results.value or candidate_results.unit (the
  original Gemini-extracted raw source data) or verification_status.

normalized_value uses NUMERIC(24, 12), not a floating-point type, so a
converted value is stored as an exact decimal rather than a binary
approximation (see task rule 9), with scale generous enough that the
column itself never caps the deterministic source-precision policy in
app.services.unit_normalization_service (which currently only ever
needs a handful of decimal places, mirroring the raw source value's own
stated precision).

Pre-existing candidate_results rows default to
unit_normalization_status = 'unresolved' with null
normalized_value/normalized_unit, which is the correct, conservative
outcome for rows that predate this feature.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "c8f4b2e6a1d3"
down_revision: Union[str, None] = "b7e2c5a1d9f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "candidate_results",
        sa.Column("normalized_value", sa.Numeric(24, 12), nullable=True),
    )
    op.add_column(
        "candidate_results",
        sa.Column("normalized_unit", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "candidate_results",
        sa.Column(
            "unit_normalization_status",
            sa.Enum(
                "resolved",
                "unresolved",
                "unsupported",
                name="unit_normalization_status",
            ),
            nullable=False,
            server_default="unresolved",
        ),
    )


def downgrade() -> None:
    op.drop_column("candidate_results", "unit_normalization_status")
    op.drop_column("candidate_results", "normalized_unit")
    op.drop_column("candidate_results", "normalized_value")
    op.execute("DROP TYPE IF EXISTS unit_normalization_status")
