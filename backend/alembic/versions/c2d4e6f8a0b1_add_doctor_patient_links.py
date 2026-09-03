"""add doctor_patient_links table

Revision ID: c2d4e6f8a0b1
Revises: b1e4a7d2f9c3
Create Date: 2026-09-01 00:00:00

Creates the doctor_patient_links table for managing which doctors are
authorized to access which patients. This is the secure authorization
foundation: a doctor can only access a patient's data when there exists
an ACTIVE link between them. A doctor's DOCTOR role alone is never
sufficient.

The lifecycle is explicit:
    PENDING -> ACTIVE (doctor accepts)
    PENDING -> DECLINED (doctor declines)
    ACTIVE -> REVOKED (patient revokes)

A partial unique index prevents duplicate active/pending relationships
between the same doctor and patient pair.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "c2d4e6f8a0b1"
down_revision: Union[str, None] = "b1e4a7d2f9c3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Create enum types for status and initiated_by
    doctor_patient_link_status = postgresql.ENUM(
        "pending", "active", "declined", "revoked",
        name="doctor_patient_link_status",
        create_type=False,
    )
    link_initiated_by = postgresql.ENUM(
        "patient", "doctor",
        name="link_initiated_by",
        create_type=False,
    )

    # Create the enum types if they don't exist
    doctor_patient_link_status.create(op.get_bind(), checkfirst=True)
    link_initiated_by.create(op.get_bind(), checkfirst=True)

    # Create the doctor_patient_links table
    op.create_table(
        "doctor_patient_links",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "patient_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "doctor_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id"),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending", "active", "declined", "revoked",
                name="doctor_patient_link_status",
                create_type=False,
            ),
            nullable=False,
            server_default="pending",
        ),
        sa.Column(
            "initiated_by",
            postgresql.ENUM(
                "patient", "doctor",
                name="link_initiated_by",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "initiated_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "accepted_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        # Partial unique index: prevent duplicate active/pending
        # relationships between the same doctor and patient pair.
        # Multiple REVOKED or DECLINED links are allowed (history).
        sa.UniqueConstraint(
            "doctor_id",
            "patient_id",
            name="uq_doctor_patient_links_active",
            postgresql_where="status IN ('active', 'pending')",
        ),
    )

    # Create indexes for efficient querying
    op.create_index(
        op.f("ix_doctor_patient_links_patient_id"),
        "doctor_patient_links",
        ["patient_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_doctor_patient_links_doctor_id"),
        "doctor_patient_links",
        ["doctor_id"],
        unique=False,
    )


def downgrade() -> None:
    # Drop indexes
    op.drop_index(
        op.f("ix_doctor_patient_links_doctor_id"),
        table_name="doctor_patient_links",
    )
    op.drop_index(
        op.f("ix_doctor_patient_links_patient_id"),
        table_name="doctor_patient_links",
    )

    # Drop table
    op.drop_table("doctor_patient_links")

    # Drop enum types
    op.execute("DROP TYPE IF EXISTS link_initiated_by")
    op.execute("DROP TYPE IF EXISTS doctor_patient_link_status")
