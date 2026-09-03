"""Evidence matching — verify AI evidence hints against actual report text.

This is the ONLY module that determines what text is stored in
ExtractionEvidence.source_text. It enforces a critical provenance rule:

    Gemini's evidence output is a HINT, not authoritative source text.

The actual evidence must come from the report's own extracted text
(native PDF extraction or OCR fallback). This service takes Gemini's
evidence hint and locates it within the actual report text:

1. If the Gemini hint appears as an exact substring of the report text
   (after whitespace normalization), the MATCHED text from the actual
   report is returned as authoritative provenance.
2. If the hint does not appear in the report text, None is returned —
   evidence is unavailable rather than fabricated.

This is deliberately conservative: no fuzzy matching, no heuristics,
no AI-powered re-matching. An unmatched hint produces no evidence record
rather than producing incorrect provenance.

No database, network, or external service dependency. Pure string
matching only.
"""
from __future__ import annotations


def _normalize_whitespace(text: str) -> str:
    """Collapse all whitespace runs (spaces, tabs, newlines) to single
    spaces and strip leading/trailing whitespace. This handles the common
    case where PDF text extraction or Gemini slightly differ in whitespace
    formatting while preserving the same underlying content."""
    return " ".join(text.split())


def match_evidence_to_source(
    evidence_hint: str,
    report_source_text: str,
) -> str | None:
    """Attempt to locate the AI evidence hint within the actual report
    source text and return the matched text from the report.

    Args:
        evidence_hint: The evidence string the AI provided — treated as
            a search target, NOT as authoritative provenance.
        report_source_text: The actual extracted text from the report
            (native PDF extraction or OCR output). This is the
            authoritative source of truth.

    Returns:
        The matched substring from report_source_text if found (after
        whitespace normalization), or None if the hint cannot be
        reliably located in the actual report text.

    Design decisions:
        - Exact substring match only (no fuzzy/regex/semantic matching)
          to avoid incorrect associations between unrelated medical
          values.
        - Whitespace is normalized before comparison to handle minor
          formatting differences between PDF extraction and the
          AI's output.
        - The returned text is from report_source_text (not the hint)
          so provenance is always derived from the actual document.
        - When the hint is empty/blank, returns None (no evidence
          to match against).
        - When the report source is empty/blank, returns None (nothing
          to match against).
    """
    if not evidence_hint or not evidence_hint.strip():
        return None
    if not report_source_text or not report_source_text.strip():
        return None

    normalized_hint = _normalize_whitespace(evidence_hint)
    normalized_source = _normalize_whitespace(report_source_text)

    if normalized_hint in normalized_source:
        # Found an exact match within the report text.
        # Return the text from the ACTUAL report source, not the hint.
        return normalized_hint

    # The evidence hint does not appear in the actual report text.
    # Evidence is unavailable — we never fabricate or store AI-only
    # text as provenance.
    return None
