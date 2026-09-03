"""add lab test normalization foundation

Revision ID: b7e2c5a1d9f4
Revises: a4d6e1f8c3b7
Create Date: 2026-08-31 00:00:00

Adds the deterministic normalization foundation described in
app.services.normalization_service:

* canonical_tests — a small, backend-curated table of canonical lab test
  identities (seeded here with the fixed initial set the service's alias
  dictionary currently resolves to).
* candidate_results.canonical_test_id / normalization_status — additive
  columns recording the deterministic normalization outcome for a
  CandidateResult, without ever touching candidate_results.test_name
  (the original Gemini-extracted source name) or verification_status.

Pre-existing candidate_results rows default to normalization_status =
'unresolved' with a null canonical_test_id, which is the correct,
conservative outcome for rows that predate this feature.
"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "b7e2c5a1d9f4"
down_revision: Union[str, None] = "a4d6e1f8c3b7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


CANONICAL_TESTS_TABLE = sa.table(
    "canonical_tests",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("code", sa.String),
    sa.column("display_name", sa.String),
)

# Fixed initial seed matching app.services.normalization_service's
# _ALIAS_TO_CANONICAL_CODES dictionary. Both T3_TOTAL and T3_FREE are
# seeded (and neither is what a bare "t3" alias resolves to — the
# service intentionally leaves that ambiguous) purely to prove the
# ambiguity case end to end.
_SEED_CANONICAL_TESTS = [
    {"code": "HEMOGLOBIN", "display_name": "Hemoglobin"},
    {"code": "T3_TOTAL", "display_name": "Total T3"},
    {"code": "T3_FREE", "display_name": "Free T3"},
]


def upgrade() -> None:
    op.create_table(
        "canonical_tests",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("code", sa.String(length=64), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint("code", name="uq_canonical_tests_code"),
    )

    op.bulk_insert(
        CANONICAL_TESTS_TABLE,
        [{"id": uuid.uuid4(), **row} for row in _SEED_CANONICAL_TESTS],
    )

    op.add_column(
        "candidate_results",
        sa.Column(
            "canonical_test_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("canonical_tests.id"),
            nullable=True,
        ),
    )
    op.add_column(
        "candidate_results",
        sa.Column(
            "normalization_status",
            sa.Enum(
                "resolved", "unresolved", "ambiguous", name="normalization_status"
            ),
            nullable=False,
            server_default="unresolved",
        ),
    )
    op.create_index(
        "ix_candidate_results_canonical_test_id",
        "candidate_results",
        ["canonical_test_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_candidate_results_canonical_test_id", table_name="candidate_results"
    )
    op.drop_column("candidate_results", "normalization_status")
    op.drop_column("candidate_results", "canonical_test_id")
    op.execute("DROP TYPE IF EXISTS normalization_status")

    op.drop_table("canonical_tests")
