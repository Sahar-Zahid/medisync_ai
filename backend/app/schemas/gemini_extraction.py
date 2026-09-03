"""
Strict schema for Gemini's candidate lab-extraction output.

This is the ONLY shape a Gemini response is allowed to take. It is used
two ways:

1. Passed to the Gemini SDK as the structured-output response_schema, so
   the model is constrained at generation time (task rule 17).
2. Used again, independently, to validate whatever text comes back before
   anything touches the database (task rule 12) — the backend never
   trusts the model just because structured output was requested.

`extra="forbid"` on both models means any field Gemini invents beyond
this schema causes validation to fail rather than being silently
accepted.
"""
from pydantic import BaseModel, ConfigDict, Field, field_validator


class GeminiCandidateItem(BaseModel):
    """One candidate test/analyte result, exactly as Gemini extracted it
    from the supplied report text. Optional fields are None when the
    document doesn't state them — Gemini must never invent a value here
    (enforced by prompt instructions; this schema only enforces shape,
    not truthfulness)."""

    model_config = ConfigDict(extra="forbid")

    test_name: str
    value: str
    unit: str | None = None
    reference_range: str | None = None
    specimen: str | None = None
    result_date: str | None = None
    # Source text from the report supporting this candidate — required,
    # never absent, per task rule 6.
    evidence: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("test_name", "value", "evidence")
    @classmethod
    def _must_be_non_empty(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("must not be empty")
        return stripped

    @field_validator("unit", "reference_range", "specimen", "result_date")
    @classmethod
    def _blank_optional_becomes_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


class GeminiExtractionResponse(BaseModel):
    """The full structured response Gemini must return for one report.
    An empty `candidates` list is a legitimate outcome (the report simply
    contains no lab values Gemini could find) — it is not, by itself, a
    validation failure."""

    model_config = ConfigDict(extra="forbid")

    candidates: list[GeminiCandidateItem]
