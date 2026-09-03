"""
Gemini boundary for the read-only AI trusted-results summary feature.

This module — together with app.services.gemini_extraction_service, which
handles the separate candidate-extraction feature — is one of only two
places in the codebase that talk to Gemini. Its one job: given a
server-built list of SAFE, TRUSTED TestResult data, ask Gemini to write a
short, factual, schema-locked set of observations, and return validated
Python objects.

Deliberately does NOT:
* query the database or decide which TestResult rows are trusted (that's
  app.services.patient_summary_service's job)
* persist anything
* accept a prompt, patient ID, report text, OCR text, raw Gemini
  extraction output, or any other client-controlled input — callers must
  only ever pass a list of app.schemas.summary.SummaryInputResult built
  server-side from already-trusted TestResult rows
* write to TestResult, CandidateResult, or VerificationHistory — this
  module has no database session at all
* diagnose, recommend treatment, or recommend medication — the fixed
  system instruction below explicitly forbids this, and the caller
  additionally treats the output as a derived explanation, never as
  medical-record data (see app.schemas.summary.PatientResultSummaryResponse)

API key handling: settings.gemini_api_key is read only here and in
gemini_extraction_service, only server-side, and is never included in any
exception message, log line, or return value.
"""
import json

from pydantic import ValidationError

from app.core.config import settings
from app.schemas.summary import GeminiSummaryResponse, SummaryInputResult

# The google-genai SDK is imported lazily (inside _get_client(), not at
# module scope) — same reasoning as gemini_extraction_service.py: importing
# this module must never fail just because the SDK package isn't installed
# in a given environment.

# Fixed, deterministic summary instruction. No agent framework, no
# chaining, no RAG, no user-controlled prompt text ever reaches this
# call — the only variable input is the server-built JSON list of
# already-trusted, already-safe result data appended below.
_SYSTEM_INSTRUCTION = (
    "You summarize a patient's own laboratory test results. You will "
    "receive a JSON list of trusted, doctor-reviewed lab results — each "
    "one has already been verified or corrected by a licensed doctor. "
    "Write a short list of plain-language, factual observations based "
    "strictly on the data provided. You may describe which tests are "
    "present, which results fall outside their stated reference range, "
    "and broad patterns visible in the supplied data only. Never invent, "
    "estimate, or infer a value, test, or date that is not present in "
    "the supplied data. Never diagnose any disease or condition. Never "
    "recommend, suggest, or imply any treatment, medication, dosage, or "
    "action the patient should take. Never state or imply certainty "
    "beyond what the supplied data shows. Do not address the patient "
    "directly with instructions or advice. If the supplied list is "
    "empty, return an empty observations list. Return only the "
    "structured data requested — no additional prose, no headers, no "
    "disclaimers (a disclaimer is added separately by the application)."
)


class GeminiSummaryError(Exception):
    """Base class for every way this boundary can fail. Never carries a
    raw SDK exception, stack trace, or the API key — callers must only
    ever surface the safe `str(exc)` message to the caller/client."""
    pass


class GeminiSummaryNotConfiguredError(GeminiSummaryError):
    """Raised when GEMINI_API_KEY is unset, or the google-genai SDK isn't
    importable in this environment."""
    pass


class GeminiSummaryRequestError(GeminiSummaryError):
    """Raised for any failure making or receiving the Gemini request
    itself: timeout, network error, or an API-level error response."""
    pass


class GeminiSummaryValidationError(GeminiSummaryError):
    """Raised when Gemini's response is missing/empty, isn't valid JSON,
    or doesn't conform to GeminiSummaryResponse (extra fields, missing
    required fields, wrong types, etc.)."""
    pass


def _get_client():
    if not settings.gemini_api_key:
        raise GeminiSummaryNotConfiguredError(
            "Gemini is not configured on this server."
        )

    try:
        from google import genai
    except ImportError:
        # Never leak the raw ImportError (path/environment details) —
        # this is operationally the same situation as "not configured"
        # from the caller's point of view.
        raise GeminiSummaryNotConfiguredError(
            "Gemini is not configured on this server."
        ) from None

    return genai.Client(api_key=settings.gemini_api_key)


def generate_summary_from_trusted_results(
    results: list[SummaryInputResult],
) -> GeminiSummaryResponse:
    """
    Send `results` (already-trusted, already-safe data) to Gemini and
    return a validated GeminiSummaryResponse.

    `results` must already be server-built from TestResult rows the
    caller has verified belong to the authenticated patient and are
    VERIFIED/CORRECTED (see app.services.patient_summary_service) — this
    function has no database access and no way to check that itself, so
    it must never be called with anything else, and never with an empty
    list (callers should short-circuit the empty-state deterministically
    before ever reaching this function — see
    app.services.patient_summary_service.get_patient_result_summary).

    Raises GeminiSummaryNotConfiguredError, GeminiSummaryRequestError, or
    GeminiSummaryValidationError on any failure. Never returns a
    partially valid result — either the full response validates against
    GeminiSummaryResponse, or nothing is returned at all.
    """
    client = _get_client()
    from google.genai import types  # see the lazy-import note above

    # Serialize only the safe, already-validated Pydantic input models —
    # never raw report/OCR/candidate data, and never anything client-
    # supplied. mode="json" keeps Decimal/date values as plain JSON
    # scalars rather than Python repr strings.
    payload = json.dumps(
        [item.model_dump(mode="json") for item in results],
        ensure_ascii=True,
    )

    try:
        response = client.models.generate_content(
            model=settings.gemini_model,
            contents=payload,
            config=types.GenerateContentConfig(
                system_instruction=_SYSTEM_INSTRUCTION,
                response_mime_type="application/json",
                response_schema=GeminiSummaryResponse,
                temperature=0,
            ),
        )
    except GeminiSummaryError:
        raise
    except Exception:
        # Covers SDK/API errors, timeouts, and network failures alike —
        # the caller only needs to know the request didn't succeed, never
        # the raw exception type or message (which could echo request
        # details back).
        raise GeminiSummaryRequestError("Gemini request failed.") from None

    raw_text = getattr(response, "text", None)
    if not raw_text or not raw_text.strip():
        raise GeminiSummaryValidationError("Gemini returned an empty response.")

    try:
        parsed = GeminiSummaryResponse.model_validate_json(raw_text)
    except ValidationError:
        raise GeminiSummaryValidationError(
            "Gemini's response did not match the required schema."
        ) from None

    return parsed
