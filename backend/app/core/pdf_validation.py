"""
Server-side PDF validation.

The browser-supplied Content-Type/filename extension are never trusted on
their own (either can be spoofed) — this inspects the actual file bytes.

Deliberately lightweight: this only checks that the content looks like a
well-formed PDF (correct header and trailer), not that it's a semantically
"valid" one. No PDF parsing/extraction happens here or anywhere in this
feature.
"""

_PDF_HEADER = b"%PDF-"
_PDF_EOF_MARKER = b"%%EOF"

# How many trailing bytes to search for the end-of-file marker. Real PDFs
# may have some bytes after the last %%EOF (e.g. a newline), so this is a
# window, not an exact-suffix match.
_EOF_SEARCH_WINDOW = 2048


def is_valid_pdf(content: bytes) -> bool:
    """Return True if `content` looks like a well-formed PDF file."""
    if not content:
        return False

    if not content.startswith(_PDF_HEADER):
        return False

    tail = content[-_EOF_SEARCH_WINDOW:]
    if _PDF_EOF_MARKER not in tail:
        return False

    return True
