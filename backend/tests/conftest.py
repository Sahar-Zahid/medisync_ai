import os
import sys

# Ensure "app" is importable regardless of the directory pytest is invoked
# from (mirrors the same guard in alembic/env.py).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# app.core.config reads JWT_SECRET_KEY at import time. Tests never talk to
# a real deployment, so a fixed test-only secret here is fine — this must
# never be a real secret and must never be reused outside pytest. Only set
# if unset, so a developer's real .env (if loaded) still wins.
os.environ.setdefault("JWT_SECRET_KEY", "test-only-secret-do-not-use-outside-pytest")

# All of these tests use a MagicMock in place of a real DB session, so no
# query ever actually reaches PostgreSQL — but app.core.database.get_db()
# still requires DATABASE_URL to be set just to construct a (never-used)
# SQLAlchemy engine/session at import/request time. This placeholder is
# syntactically valid but points at nothing; only set if unset, so a
# developer's real .env still wins.
os.environ.setdefault(
    "DATABASE_URL", "postgresql+psycopg://test:test@localhost:5432/test"
)
