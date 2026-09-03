"""add result date normalization foundation

Revision ID: d4f8a2c6b1e9
Revises: c8f4b2e6a1d3
Create Date: 2026-08-30 00:00:00

Adds the deterministic result-date normalization foundation described in
app.services.date_normalization_service:

* candidate_results.normalized_result_date / date_normalization_status
  -- additive columns recording the deterministic date-parsing outcome
  for a CandidateResult, without ever touching
  candidate_results.result_date (the original Gemini-extracted raw
  source string) or verification_status.

normalized_result_date uses a plain PostgreSQL DATE (no time-of-day, no
timezone) since a normalized date represents only the calendar date
explicitly stated by the source -- never a timestamp, never inferred
from report/upload metadata.

Pre-existing candidate_results rows default to
date_normalization_status = 'unresolved' with a null
normalized_result_date, which is the correct, conservative outcome for
rows that predate this feature.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "d4f8a2c6b1e9"
down_revision: Union[str, None] = "c8f4b2e6a1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "candidate_results",
        sa.Column("normalized_result_date", sa.Date(), nullable=True),
    )
    op.add_column(
        "candidate_results",
        sa.Column(
            "date_normalization_status",
            sa.Enum(
                "resolved",
                "unresolved",
                "unsupported",
                name="date_normalization_status",
            ),
            nullable=False,
            server_default="unresolved",
        ),
    )


def downgrade() -> None:
    op.drop_column("candidate_results", "date_normalization_status")
    op.drop_column("candidate_results", "normalized_result_date")
    op.execute("DROP TYPE IF EXISTS date_normalization_status")
