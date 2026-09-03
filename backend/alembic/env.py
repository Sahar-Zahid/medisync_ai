"""
Alembic environment script.

Wires Alembic to:
- the app's DATABASE_URL (from app.core.config.settings), never a hardcoded
  connection string
- the existing SQLAlchemy Base from app.core.database (not a second Base)
- the existing User model, imported here so Alembic's autogenerate can see
  the users table when comparing metadata against the live database
"""
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# Make sure "app" is importable regardless of the working directory Alembic
# is invoked from (it's normally run from backend/, where this already
# works, but this keeps it robust).
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import settings
from app.core.database import Base

# Import model modules so their tables register on Base.metadata before
# Alembic reads it. Add future model modules here as they're created.
from app.models import report, user  # noqa: F401

# Alembic Config object, providing access to values in alembic.ini.
config = context.config

# Interpret the config file for logging (if a logging section is present).
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# The metadata Alembic compares against / generates migrations from.
target_metadata = Base.metadata

# Override alembic.ini's (empty) sqlalchemy.url with the real DATABASE_URL
# from the environment, via the app's own settings object — so there is
# exactly one source of truth for the connection string.
if not settings.database_url:
    raise RuntimeError(
        "DATABASE_URL is not set. Copy backend/.env.example to backend/.env "
        "and set DATABASE_URL before running Alembic."
    )
config.set_main_option("sqlalchemy.url", settings.database_url)


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL, no live DB connection)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connects to the database)."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
