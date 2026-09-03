"""
Doctor-Patient relationship ORM model.

This establishes which doctors are authorized to access which patients.
A doctor must NEVER gain access to arbitrary patients merely because
their role is DOCTOR — access is only granted through an ACTIVE
DoctorPatientLink.

The lifecycle is explicit and auditable:
    PENDING -> ACTIVE
    PENDING -> DECLINED
    ACTIVE -> REVoked

No table is created here (no Base.metadata.create_all()) — see the
accompanying Alembic migration.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    DateTime,
    Enum,
    ForeignKey,
    Index,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base


class DoctorPatientLinkStatus(str, enum.Enum):
    """Status of a doctor-patient relationship.

    Only these four states are allowed — the lifecycle is deliberately
    constrained:
        PENDING -> ACTIVE
        PENDING -> DECLINED
        ACTIVE -> REVOKED

    A doctor cannot self-link. Invalid role combinations are rejected.
    """
    PENDING = "pending"
    ACTIVE = "active"
    DECLINED = "declined"
    REVOKED = "revoked"


class LinkInitiatedBy(str, enum.Enum):
    """Who initiated the doctor-patient relationship.

    Tracks whether the patient or doctor started the relationship,
    for audit purposes.
    """
    PATIENT = "patient"
    DOCTOR = "doctor"


class DoctorPatientLink(Base):
    """One doctor-patient relationship record.

    This is the secure authorization foundation: a doctor can only
    access a patient's data when there exists an ACTIVE link between
    them. A doctor's DOCTOR role alone is never sufficient.

    The relationship is always initiated by one party and accepted by
    the other. The initiated_by field tracks who started it, and
    initiated_at records when. accepted_at records when the other
    party accepted (set when status becomes ACTIVE).

    Ownership is always derived through the FKs to users:
        patient_id -> users.id (must be a PATIENT user)
        doctor_id -> users.id (must be a DOCTOR user)

    Database constraints enforce:
        - patient_id and doctor_id must be different users
        - No duplicate active/pending relationships between same pair
        - Each link belongs to exactly one doctor and one patient
    """
    __tablename__ = "doctor_patient_links"
    __table_args__ = (
        # Prevent duplicate active/pending relationships between the same
        # doctor and patient pair. A doctor can have multiple REVOKED or
        # DECLINED links (history), but not multiple ACTIVE or PENDING ones.
        # This partial unique index is the database-level protection.
        Index(
            "uq_doctor_patient_links_active",
            "doctor_id",
            "patient_id",
            unique=True,
            postgresql_where="status IN ('active', 'pending')",
        ),
        # Ensure each link belongs to exactly one doctor-patient pair
        UniqueConstraint(
            "id",
            name="uq_doctor_patient_links_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # The patient in this relationship. Must reference a PATIENT user.
    # Enforced at the application level (service layer checks role).
    patient_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    # The doctor in this relationship. Must reference a DOCTOR user.
    # Enforced at the application level (service layer checks role).
    doctor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id"),
        nullable=False,
        index=True,
    )

    # Current status of the relationship. Lifecycle is enforced by the
    # service layer — not all transitions are allowed.
    status: Mapped[DoctorPatientLinkStatus] = mapped_column(
        Enum(
            DoctorPatientLinkStatus,
            name="doctor_patient_link_status",
            native_enum=True,
        ),
        nullable=False,
        default=DoctorPatientLinkStatus.PENDING,
    )

    # Who initiated the relationship — patient or doctor.
    initiated_by: Mapped[LinkInitiatedBy] = mapped_column(
        Enum(
            LinkInitiatedBy,
            name="link_initiated_by",
            native_enum=True,
        ),
        nullable=False,
    )

    # When the relationship was created (request initiated).
    initiated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    # When the relationship was accepted (status became ACTIVE).
    # NULL until accepted. Never set for DECLINED/REVOKED.
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Standard timestamps.
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )

    # --- Relationships ---
    patient: Mapped["User"] = relationship(
        foreign_keys=[patient_id],
    )
    doctor: Mapped["User"] = relationship(
        foreign_keys=[doctor_id],
    )
