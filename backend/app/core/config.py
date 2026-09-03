"""
Application configuration, loaded from environment variables.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# Load backend/.env explicitly, using a path derived from this file's own
# location (backend/app/core/config.py -> backend/.env) rather than the
# current working directory. This makes env loading work the same way
# regardless of where the app/tests are launched from (project root,
# backend/, a CI runner, etc.).
_BACKEND_DIR = Path(__file__).resolve().parent.parent.parent
_ENV_PATH = _BACKEND_DIR / ".env"

# override=False: variables already present in the real environment (set
# by the shell, a process manager, or a container) take precedence over
# .env - this only fills in values that aren't already set. Does nothing
# (no error) if backend/.env doesn't exist, e.g. in production where
# config comes from real environment variables instead.
load_dotenv(dotenv_path=_ENV_PATH, override=False)


class Settings:
    # Comma-separated list of allowed origins for CORS.
    # Defaults to the local Vite dev server origin.
    cors_origins: list[str] = [
        origin.strip()
        for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",")
        if origin.strip()
    ]

    # PostgreSQL connection string, e.g.
    # postgresql+psycopg://user:password@host:5432/dbname
    # No default is provided here on purpose — see app/core/database.py,
    # which raises a clear error at engine-creation time if this is unset.
    database_url: str | None = os.getenv("DATABASE_URL")

    # JWT signing secret. No default on purpose — see app/core/security.py,
    # which raises a clear error at token-creation time if this is unset,
    # rather than silently signing tokens with a predictable value.
    jwt_secret_key: str | None = os.getenv("JWT_SECRET_KEY")

    # Signing algorithm for JWTs. HS256 (symmetric) is the standard default
    # for a single-backend setup like this one.
    jwt_algorithm: str = os.getenv("JWT_ALGORITHM", "HS256")

    # How long an issued access token (and its matching auth cookie) stays
    # valid, in minutes.
    jwt_access_token_expire_minutes: int = int(
        os.getenv("JWT_ACCESS_TOKEN_EXPIRE_MINUTES", "30")
    )

    # Directory where uploaded report files are stored. Private,
    # backend-controlled, and deliberately NOT under the frontend's
    # public/static directory — nothing under here is ever served
    # directly. Defaults to backend/storage/reports (resolved relative to
    # this file, not the process's working directory).
    report_storage_dir: Path = Path(
        os.getenv(
            "REPORT_STORAGE_DIR",
            str(_BACKEND_DIR / "storage" / "reports"),
        )
    )

    # Maximum accepted upload size, in megabytes.
    max_report_upload_mb: int = int(os.getenv("MAX_REPORT_UPLOAD_MB", "20"))

    # Gemini API key, server-side only. Never returned in an API response,
    # never accepted from the client, never stored in the database, never
    # logged. See app/services/gemini_extraction_service.py, the only
    # module allowed to read this. No default on purpose — that module
    # raises a clear, generic (non-leaking) error at call time if this is
    # unset, the same pattern used above for jwt_secret_key.
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")

    # Gemini model name used for candidate lab-result extraction. Kept
    # configurable rather than hardcoded so it can be updated without a
    # code change as model names change; confirm the current model name
    # against Gemini API docs before relying on this default in
    # production.
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")


settings = Settings()
