"""Add patient identity checkpoint to reports

Adds deterministic patient-identity extraction and matching fields to the
reports table. This is the backend guard that prevents a medical report
belonging to one patient from silently becoming trusted data under
another patient account.

Fields added:
- patient_name_extracted, patient_dob_extracted, patient_mrn_extracted:
  raw identity strings parsed from the report text
- identity_check_status: enum (not_checked, match, mismatch, unresolved)
- identity_confirmed_by_doctor: explicit doctor acknowledgment flag
- identity_confirmed_by: doctor who confirmed (FK to users)
- identity_confirmed_at: server-generated confirmation timestamp

Revision ID: b2c8e4a6d7f1
Down revision: d1f9e3a7b4c5
"""
import sqlalchemy as sa
from alembic import op

# revision identifiers
revision: str = "b2c8e4a6d7f1"
down_revision: str = "d1f9e3a7b4c5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Create the identity_check_status enum type
    op.execute(
        "CREATE TYPE identity_check_status AS ENUM "
        "('not_checked', 'match', 'mismatch', 'unresolved')"
    )

    # Add extracted identity fields
    op.add_column(
        "reports",
        sa.Column("patient_name_extracted", sa.String(255), nullable=True),
    )
    op.add_column(
        "reports",
        sa.Column("patient_dob_extracted", sa.String(64), nullable=True),
    )
    op.add_column(
        "reports",
        sa.Column("patient_mrn_extracted", sa.String(128), nullable=True),
    )

    # Add identity check status
    op.add_column(
        "reports",
        sa.Column(
            "identity_check_status",
            sa.Enum(
                "not_checked", "match", "mismatch", "unresolved",
                name="identity_check_status",
            ),
            nullable=False,
            server_default="not_checked",
        ),
    )

    # Add doctor confirmation fields
    op.add_column(
        "reports",
        sa.Column("identity_confirmed_by_doctor", sa.Boolean(), nullable=False, server_default="false"),
    )
    op.add_column(
        "reports",
        sa.Column("identity_confirmed_by", sa.dialects.postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column(
        "reports",
        sa.Column("identity_confirmed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Add FK constraint for identity_confirmed_by
    op.create_foreign_key(
        "fk_reports_identity_confirmed_by",
        "reports",
        "users",
        ["identity_confirmed_by"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_reports_identity_confirmed_by", "reports", type_="foreignkey")
    op.drop_column("reports", "identity_confirmed_at")
    op.drop_column("reports", "identity_confirmed_by")
    op.drop_column("reports", "identity_confirmed_by_doctor")
    op.drop_column("reports", "identity_check_status")
    op.drop_column("reports", "patient_mrn_extracted")
    op.drop_column("reports", "patient_dob_extracted")
    op.drop_column("reports", "patient_name_extracted")
    op.execute("DROP TYPE identity_check_status")
