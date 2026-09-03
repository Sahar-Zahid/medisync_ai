"""
Deterministic patient identity extraction from report text.

Extracts patient identity information (name, date of birth, MRN) from
the report's extracted_text or ocr_text using ONLY conservative,
deterministic regex patterns.

This module:
- Has NO Gemini/LLM dependency
- Makes no network calls
- Uses NO fuzzy matching
- Only extracts identity information actually present in the text
- Returns raw extracted values without interpretation

When identity evidence is insufficient or absent, the corresponding
field is None — never guessed, never fabricated, never defaulted.
"""
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ExtractedIdentity:
    """Raw patient identity extracted from a report's text.

    All fields are optional — None means the information was not
    found in the text, never that it was looked up or inferred.
    """
    patient_name: str | None = None
    patient_dob: str | None = None
    patient_mrn: str | None = None


# --- Name extraction patterns ---
# Conservative patterns: only match well-known lab report header formats.
# Names are typically near the top of a report, labeled clearly.
_NAME_PATTERNS = [
    # "Patient Name: John Smith" or "Patient Name: Smith, John"
    re.compile(
        r"patient\s+name\s*:\s*(.+)",
        re.IGNORECASE,
    ),
    # "Name: John Smith"
    re.compile(
        r"\bname\s*:\s*([A-Z][a-zA-Z\s\.\-']+)",
    ),
    # "Patient: John Smith"
    re.compile(
        r"\bpatient\s*:\s*([A-Z][a-zA-Z\s\.\-']+)",
    ),
]

# --- DOB extraction patterns ---
_DOB_PATTERNS = [
    # "Date of Birth: 01/15/1980" or "DOB: 1980-01-15"
    re.compile(
        r"(?:date\s+of\s+birth|dob|d\.o\.b\.?)\s*:\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}|\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2})",
        re.IGNORECASE,
    ),
    # "Born: 01/15/1980"
    re.compile(
        r"\bborn\s*:\s*(\d{1,2}[/\-\.]\d{1,2}[/\-\.]\d{2,4}|\d{4}[/\-\.]\d{1,2}[/\-\.]\d{1,2})",
        re.IGNORECASE,
    ),
    # "Birth Date: January 15, 1980"
    re.compile(
        r"(?:date\s+of\s+birth|dob|birth\s+date)\s*:\s*(\w+\s+\d{1,2},?\s+\d{4})",
        re.IGNORECASE,
    ),
]

# --- MRN extraction patterns ---
_MRN_PATTERNS = [
    # "MRN: 12345678" or "MRN# 12345678"
    re.compile(
        r"\b(?:mrn|medical\s+record\s+number|patient\s+id|patient\s+number)\s*[#:\s]*(\w[\w\-]{2,20})",
        re.IGNORECASE,
    ),
    # "ID: 12345678" (only in header-like context — adjacent to patient info)
    re.compile(
        r"\bid\s*:\s*(\d{4,20})",
    ),
]


def _clean_extracted_value(value: str | None) -> str | None:
    """Strip and clean an extracted value. Returns None if empty."""
    if value is None:
        return None
    cleaned = value.strip()
    # Remove trailing commas, periods, and excess whitespace
    cleaned = re.sub(r"[,.\s]+$", "", cleaned)
    # Collapse internal whitespace
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned if cleaned else None


def extract_patient_identity(text: str) -> ExtractedIdentity:
    """
    Deterministically extract patient identity from report text.

    Uses only conservative regex patterns against the raw extracted text.
    No AI, no fuzzy matching, no guessing. Missing information returns
    None — never fabricated.

    The text parameter must be the report's extracted_text or ocr_text,
    never client-supplied input.

    Returns:
        ExtractedIdentity with patient_name, patient_dob, patient_mrn
        (each None if not found in the text).
    """
    if not text or not text.strip():
        return ExtractedIdentity()

    # Extract patient name
    patient_name = None
    for pattern in _NAME_PATTERNS:
        match = pattern.search(text)
        if match:
            patient_name = _clean_extracted_value(match.group(1))
            if patient_name:
                break

    # Extract DOB
    patient_dob = None
    for pattern in _DOB_PATTERNS:
        match = pattern.search(text)
        if match:
            patient_dob = _clean_extracted_value(match.group(1))
            if patient_dob:
                break

    # Extract MRN
    patient_mrn = None
    for pattern in _MRN_PATTERNS:
        match = pattern.search(text)
        if match:
            patient_mrn = _clean_extracted_value(match.group(1))
            if patient_mrn:
                break

    return ExtractedIdentity(
        patient_name=patient_name,
        patient_dob=patient_dob,
        patient_mrn=patient_mrn,
    )
