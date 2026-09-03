"""add reference range normalization and abnormality classification

Revision ID: f8a2c6b1d4e7
Revises: d4f8a2c6b1e9
Create Date: 2026-08-31 00:00:00

Adds the deterministic reference-range normalization and abnormality
classification foundation described in
app.services.reference_range_normalization_service and
app.services.abnormality_classification_service:

* candidate_results.normalized_reference_lower / normalized_reference_upper
  -- additive columns recording the parsed numeric reference bounds
  (Numeric, same scale as normalized_value)
* candidate_results.reference_range_inclusive_lower /
  reference_range_inclusive_upper
  -- whether each bound is inclusive (None when range is not RESOLVED)
* candidate_results.reference_range_normalization_status
  -- RESOLVED / UNRESOLVED / UNSUPPORTED outcome of range parsing
* candidate_results.abnormality_status
  -- NORMAL / LOW / HIGH / UNRESOLVED / NOT_APPLICABLE outcome of
  deterministic numeric comparison against the normalized range

Pre-existing candidate_results rows default to
reference_range_normalization_status = 'unresolved' and
abnormality_status = 'unresolved' with null normalized bounds, which
is the correct, conservative outcome for rows that predate this
feature.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "f8a2c6b1d4e7"
down_revision: Union[str, None] = "d4f8a2c6b1e9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "candidate_results",
        sa.Column("normalized_reference_lower", sa.Numeric(24, 12), nullable=True),
    )
    op.add_column(
        "candidate_results",
        sa.Column("normalized_reference_upper", sa.Numeric(24, 12), nullable=True),
    )
    op.add_column(
        "candidate_results",
        sa.Column("reference_range_inclusive_lower", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "candidate_results",
        sa.Column("reference_range_inclusive_upper", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "candidate_results",
        sa.Column(
            "reference_range_normalization_status",
            sa.Enum(
                "resolved",
                "unresolved",
                "unsupported",
                name="reference_range_normalization_status",
            ),
            nullable=False,
            server_default="unresolved",
        ),
    )
    op.add_column(
        "candidate_results",
        sa.Column(
            "abnormality_status",
            sa.Enum(
                "normal",
                "low",
                "high",
                "unresolved",
                "not_applicable",
                name="abnormality_status",
            ),
            nullable=False,
            server_default="unresolved",
        ),
    )


def downgrade() -> None:
    op.drop_column("candidate_results", "abnormality_status")
    op.drop_column("candidate_results", "reference_range_normalization_status")
    op.drop_column("candidate_results", "reference_range_inclusive_upper")
    op.drop_column("candidate_results", "reference_range_inclusive_lower")
    op.drop_column("candidate_results", "normalized_reference_upper")
    op.drop_column("candidate_results", "normalized_reference_lower")
    op.execute("DROP TYPE IF EXISTS abnormality_status")
    op.execute("DROP TYPE IF EXISTS reference_range_normalization_status")
