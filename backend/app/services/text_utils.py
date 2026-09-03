"""
Small shared helper for deciding whether extracted text counts as
"usable" — i.e. contains real content rather than being empty or
whitespace-only.

Deliberately dumb and deterministic: no medical-content heuristics, no
language detection, nothing that looks at *what* the text says — only
whether there's any non-whitespace content at all. Shared by both the
native pypdf extractor (app.services.pdf_extraction_service) and the OCR
fallback (app.services.ocr_extraction_service) so both apply exactly the
same rule, and a whitespace-only result is never mistaken for a
successful extraction in either path.
"""


def has_usable_text(text: str | None) -> bool:
    """Return True if `text` contains at least one non-whitespace
    character."""
    return bool(text) and bool(text.strip())
