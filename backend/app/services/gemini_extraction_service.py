"""
Gemini extraction boundary.

This is the ONLY module in the codebase that talks to Gemini. It has one
job: given raw report text (already produced by the existing PDF/OCR
pipeline — see app.services.pdf_extraction_service /
app.services.ocr_extraction_service), ask Gemini to extract candidate lab
results in a strict, schema-locked shape, and return validated Python
objects.

Deliberately does NOT:
* parse PDFs or decide extracted_text vs ocr_text (that's
  app.services.candidate_extraction_service's job)
* persist anything to the database
* decide medical correctness, normalize test names, convert units, or
  classify anything as normal/abnormal
* accept a prompt, report text, or any other input from the client —
  callers must only ever pass server-sourced report text

API key handling: settings.gemini_api_key is read only here, only
server-side, and is never included in any exception message, log line,
or return value.
"""
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.gemini_extraction import GeminiExtractionResponse

# The google-genai SDK is imported lazily (inside _get_client(), not at
# module scope) so that importing this module — and therefore starting
# the app and importing every other module that (transitively) imports
# the app — never fails just because the SDK package isn't installed in
# a given environment. See GeminiNotConfiguredError below for the
# resulting failure mode when it truly isn't available.

# Minimal, deterministic extraction instruction (task rule 16). No agent
# framework, no chaining, no RAG — a single instruction + a single call.
_SYSTEM_INSTRUCTION = (
    "You extract laboratory test results from medical report text. "
    "Extract only information explicitly present in the supplied report "
    "text. Never infer, estimate, or invent a test name, value, unit, "
    "reference range, specimen, or date that is not stated in the text. "
    "If a field is not present in the text, omit it (leave it null) "
    "rather than guessing. For every extracted result, copy the exact "
    "source text from the report that supports it into the evidence "
    "field — never generate evidence from your own reasoning. Do not "
    "decide whether any value is normal or abnormal, do not diagnose "
    "any condition, do not recommend any treatment, and do not rename, "
    "normalize, or convert the units of any test. If the report contains "
    "no laboratory test results at all, return an empty candidates list. "
    "Return only the structured data requested — no additional prose."
)


class GeminiExtractionError(Exception):
    """Base class for every way this boundary can fail. Never carries a
    raw SDK exception, stack trace, or the API key — callers must only
    ever surface the safe `str(exc)` message to the caller/client."""
    pass


class GeminiNotConfiguredError(GeminiExtractionError):
    """Raised when GEMINI_API_KEY is unset, or the google-genai SDK isn't
    importable in this environment. Distinct from a request failure so
    callers/ops can tell "not set up" apart from "Gemini had an error."""
    pass


class GeminiRequestError(GeminiExtractionError):
    """Raised for any failure making or receiving the Gemini request
    itself: timeout, network error, or an API-level error response."""
    pass


class GeminiValidationError(GeminiExtractionError):
    """Raised when Gemini's response is missing/empty, isn't valid JSON,
    or doesn't conform to GeminiExtractionResponse (extra fields, missing
    required fields, wrong types, etc.)."""
    pass


def _get_client():
    if not settings.gemini_api_key:
        raise GeminiNotConfiguredError(
            "Gemini is not configured on this server."
        )

    try:
        from google import genai
    except ImportError:
        # Never leak the raw ImportError (path/environment details) —
        # this is operationally the same situation as "not configured"
        # from the caller's point of view.
        raise GeminiNotConfiguredError(
            "Gemini is not configured on this server."
        ) from None

    return genai.Client(api_key=settings.gemini_api_key)


def extract_candidates_from_text(report_text: str) -> list:
    """
    Send `report_text` to Gemini and return a validated list of
    GeminiCandidateItem.

    `report_text` must already be server-sourced raw text from a report
    the caller has verified ownership of (see
    app.services.candidate_extraction_service) — this function has no
    way to check that itself and trusts its caller entirely, so it must
    never be called with anything client-supplied.

    Raises GeminiNotConfiguredError, GeminiRequestError, or
    GeminiValidationError on any failure. Never returns a partially
    valid result — either the full response validates against
    GeminiExtractionResponse, or nothing is returned at all.
    """
    client = _get_client()
    from google.genai import types  # see the lazy-import note above

    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=report_text,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=GeminiExtractionResponse,
                temperature=0,
            ),
        )
    except GeminiExtractionError:
        raise
    except Exception:
        # Covers SDK/API errors, timeouts, and network failures alike —
        # the caller only needs to know the request didn't succeed, never
        # the raw exception type or message (which could echo request
        # details back).
        raise GeminiRequestError("Gemini request failed.") from None

    raw_text = getattr(response, "text", None)
    if not raw_text or not raw_text.strip():
        # An unexpectedly empty response body is a validation failure,
        # not the same thing as a validly-parsed empty candidates list
        # (see GeminiExtractionResponse docstring) — those are different
        # things and must not be conflated.
        raise GeminiValidationError("Gemini returned an empty response.")

    try:
        parsed = GeminiExtractionResponse.model_validate_json(raw_text)
    except ValidationError:
        raise GeminiValidationError(
            "Gemini's response did not match the required schema."
        ) from None

    return parsed.candidates
