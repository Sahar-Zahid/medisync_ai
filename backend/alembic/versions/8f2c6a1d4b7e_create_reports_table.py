"""create reports table

Revision ID: 8f2c6a1d4b7e
Revises: 3ac145d7e552
Create Date: 2026-08-28 00:00:00

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "8f2c6a1d4b7e"
down_revision: Union[str, None] = "3ac145d7e552"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    report_status = postgresql.ENUM("uploaded", name="report_status")
    report_status.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "reports",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("patient_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM("uploaded", name="report_status", create_type=False),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reports")),
        sa.ForeignKeyConstraint(
            ["patient_id"], ["users.id"], name=op.f("fk_reports_patient_id_users")
        ),
    )
    op.create_index(
        op.f("ix_reports_patient_id"), "reports", ["patient_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_reports_patient_id"), table_name="reports")
    op.drop_table("reports")

    report_status = postgresql.ENUM("uploaded", name="report_status")
    report_status.drop(op.get_bind(), checkfirst=True)
