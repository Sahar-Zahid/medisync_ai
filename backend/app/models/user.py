"""
User ORM model.

This defines the `users` table only. No table is created here (no
Base.metadata.create_all()) — that will happen via Alembic migrations in a
later step.
"""
import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class UserRole(str, enum.Enum):
    """The only roles a user may have. Backed by a Postgres enum type, so
    the database itself rejects any value outside this set."""
    PATIENT = "patient"
    DOCTOR = "doctor"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    full_name: Mapped[str] = mapped_column(String(255), nullable=False)

    # Stored lowercase (see the email validator in app/schemas/user.py) so
    # that "User@x.com" and "user@x.com" can't both register separately.
    # Unique + indexed for fast, duplicate-free lookup at login time.
    email: Mapped[str] = mapped_column(
        String(320), unique=True, index=True, nullable=False
    )

    # Never a plaintext password field. Populated by the hashing step in a
    # later authentication task — this model only defines where the hash
    # lives.
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)

    role: Mapped[UserRole] = mapped_column(
        Enum(UserRole, name="user_role", native_enum=True), nullable=False
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
