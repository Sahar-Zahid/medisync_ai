"""
Database connection infrastructure (SQLAlchemy 2.x style).

This module only sets up the engine, session factory, and declarative base.
No models are defined here and no tables are created here — that comes in a
later step, along with real migrations. We deliberately do NOT call
Base.metadata.create_all() anywhere, since that pattern doesn't belong in a
project that will use migrations.
"""
from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.core.config import settings

# The engine and session factory are only created when DATABASE_URL is set,
# so the app can still start (and GET / / GET /health still work) before a
# database is configured. Anything that actually needs a session will get a
# clear error instead of the app failing to boot.
engine = create_engine(settings.database_url, pool_pre_ping=True) if settings.database_url else None

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False) if engine else None


class Base(DeclarativeBase):
    """Shared declarative base for all future ORM models."""
    pass


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency that yields a DB session and always closes it."""
    if SessionLocal is None:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy backend/.env.example to backend/.env "
            "and set DATABASE_URL to a valid PostgreSQL connection string."
        )
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
