"""add partial unique index on candidate_extractions for completed status

Revision ID: a4d6e1f8c3b7
Revises: f1c3a7d9b2e6
Create Date: 2026-08-30 00:00:00

Closes a concurrent-duplicate-extraction race: without this, two
simultaneous requests for the same report could both pass the
application-level "no existing completed extraction" check and both
insert a COMPLETED CandidateExtraction row. This partial unique index
makes that impossible at the database level — only one COMPLETED row per
report_id can ever exist. FAILED rows are intentionally excluded so
retries after a failure can still insert additional rows.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a4d6e1f8c3b7"
down_revision: Union[str, None] = "f1c3a7d9b2e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_index(
        "uq_candidate_extractions_report_completed",
        "candidate_extractions",
        ["report_id"],
        unique=True,
        postgresql_where=sa.text("status = 'completed'"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_candidate_extractions_report_completed",
        table_name="candidate_extractions",
    )
