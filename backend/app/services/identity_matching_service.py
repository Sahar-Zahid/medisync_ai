"""
Deterministic patient identity matching — report vs account.

Compares identity information extracted from a medical report against
the authenticated patient account to determine whether the report
belongs to the claimed patient.

This module:
- Has NO Gemini/LLM dependency
- Makes no network calls
- Uses NO fuzzy matching
- Normalizes comparison values deterministically (casefold + strip)
- Treats missing evidence as UNRESOLVED, never as a match
"""
from dataclasses import dataclass

from app.models.report import IdentityCheckStatus


@dataclass(frozen=True)
class IdentityMatchResult:
    """Outcome of comparing extracted report identity against account."""
    status: IdentityCheckStatus
    reason: str


def _normalize_name(name: str) -> str:
    """Deterministic name normalization for comparison only.

    Lowercases, strips whitespace, removes extra spaces.
    Does NOT modify the original extracted value — used only for
    the comparison decision itself.
    """
    return " ".join(name.lower().strip().split())


def _normalize_dob(dob: str) -> str:
    """Deterministic DOB normalization for comparison.

    Strips whitespace and separators for comparison, normalizing
    to a consistent format. Does NOT modify the original value.
    """
    return dob.strip().replace("/", "-").replace(".", "-").lower()


def match_identity(
    extracted_name: str | None,
    extracted_dob: str | None,
    extracted_mrn: str | None,
    account_name: str,
) -> IdentityMatchResult:
    """
    Deterministically compare extracted report identity against
    the patient account identity.

    Rules:
    - If ALL identity fields are missing: UNRESOLVED (insufficient evidence)
    - If at least one field is present and it MATCHES: MATCH
    - If at least one field is present and it MISMATCHES: MISMATCH
    - If multiple fields present and some match/some mismatch: MISMATCH

    The patient account's full_name is the authoritative reference.
    The extracted values are compared against it using deterministic
    normalization (casefold + strip).

    Never treats missing identity information as a match. Never guesses.
    """
    has_name = extracted_name is not None and extracted_name.strip()
    has_dob = extracted_dob is not None and extracted_dob.strip()
    has_mrn = extracted_mrn is not None and extracted_mrn.strip()

    # No identity evidence at all
    if not has_name and not has_dob and not has_mrn:
        return IdentityMatchResult(
            status=IdentityCheckStatus.UNRESOLVED,
            reason="No patient identity information found in the report.",
        )

    # Compare name if extracted
    name_match = None
    if has_name:
        norm_extracted = _normalize_name(extracted_name)
        norm_account = _normalize_name(account_name)
        name_match = norm_extracted == norm_account

    # We don't have DOB/MRN from the account to compare against —
    # those are only useful if we had a verified DOB/MRN in the
    # account record. For now, name is the primary comparison.
    # If we only have DOB or MRN (no name), it's UNRESOLVED because
    # we can't compare those against the account.
    if has_name:
        if name_match:
            return IdentityMatchResult(
                status=IdentityCheckStatus.MATCH,
                reason="Extracted patient name matches the account.",
            )
        else:
            return IdentityMatchResult(
                status=IdentityCheckStatus.MISMATCH,
                reason="Extracted patient name does not match the account.",
            )

    # Only DOB or MRN found — insufficient for a definitive decision
    # because we have no account DOB/MRN to compare against
    return IdentityMatchResult(
        status=IdentityCheckStatus.UNRESOLVED,
        reason="Report contains DOB or MRN but no patient name — insufficient for comparison.",
    )
