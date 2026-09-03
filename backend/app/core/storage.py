"""
Private storage for uploaded report files.

This is the single centralized place report files get written to or
removed from disk. Nothing else in the app should open() a report path
directly.

The storage root (settings.report_storage_dir) is a backend-only
directory, never the frontend's public/static folder — files here are
never served directly by a static file route.

The client-supplied filename is never used to build a path: every stored
file gets a fresh server-generated UUID name, which by construction
contains no path separators or traversal sequences, so a malicious
filename (e.g. "../../etc/passwd" or "report.pdf/../../x") cannot escape
the storage root.
"""
import uuid
from pathlib import Path

from app.core.config import settings


class StorageError(Exception):
    """Raised when a file could not be written to or removed from private
    storage. Never carries raw OS/filesystem internals — callers turn
    this into a generic client-safe error."""
    pass


def _storage_root() -> Path:
    root = settings.report_storage_dir
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_report_file(content: bytes) -> str:
    """
    Write `content` to a new, server-generated path under the private
    storage root and return the storage identifier (a path relative to
    the storage root — safe to store in the database, never an absolute
    filesystem path and never derived from client input).

    Raises StorageError if the file could not be written.
    """
    root = _storage_root()
    storage_name = f"{uuid.uuid4()}.pdf"
    destination = root / storage_name

    try:
        destination.write_bytes(content)
    except OSError:
        raise StorageError("Could not save the uploaded file.") from None

    return storage_name


def resolve_report_path(storage_path: str) -> Path:
    """
    Resolve a server-generated storage_path to its full filesystem path
    under the private storage root, for read-only access (e.g. text
    extraction).

    storage_path must always come from a Report row already looked up
    via the authenticated database record — never from a client-supplied
    value — but this still refuses to resolve anything outside the
    storage root as defense in depth.

    Raises StorageError (never a raw filesystem path or OS error) if the
    resolved path would escape the storage root or the file doesn't
    exist.
    """
    root = _storage_root().resolve()
    target = (root / storage_path).resolve()

    if root not in target.parents and target != root:
        raise StorageError("Invalid report storage path.")

    if not target.is_file():
        raise StorageError("Report file not found in storage.")

    return target


def delete_report_file(storage_path: str) -> None:
    """
    Best-effort cleanup of a previously saved file, used when a later step
    (e.g. the database insert) fails after the file was already written.

    Resolves storage_path against the storage root and refuses to delete
    anything outside it, as a defense-in-depth check even though
    storage_path values are always server-generated. Never raises — a
    failed cleanup should not mask the original error that triggered it.
    """
    root = _storage_root().resolve()
    target = (root / storage_path).resolve()

    if root not in target.parents and target != root:
        return

    try:
        target.unlink(missing_ok=True)
    except OSError:
        pass
