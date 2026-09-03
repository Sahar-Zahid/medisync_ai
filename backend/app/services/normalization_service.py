"""
Deterministic lab test-name normalization — foundation layer.

Maps a CandidateResult's raw, Gemini-extracted `test_name` (e.g.
"Haemoglobin", "HGB", "Hb") to a backend-curated CanonicalTest (e.g.
"Hemoglobin"), or explicitly declines to when it can't do so safely.

This module has NO Gemini/LLM dependency and makes no network calls —
matching is a pure, deterministic lookup against a small, explicit
in-code alias dictionary (task rule 3: "do NOT attempt to build a giant
medical dictionary"; a future feature may move this dictionary into the
database for curation, but that's out of scope here). It is
independently testable with no database at all for the matching
decision itself (see resolve_alias()); a database is only needed to look
up the CanonicalTest row once a single canonical code has been decided.

Conservative by design (task rule 4): if a source name isn't a known
alias, or matches more than one canonical test, normalization leaves it
UNRESOLVED / AMBIGUOUS rather than guessing. Nothing here ever creates a
new CanonicalTest, infers disease, classifies a value as normal/abnormal,
or touches CandidateVerificationStatus — normalization status and
verification status are independent concerns (see NormalizationStatus's
docstring in app.models.extraction).
"""
from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.extraction import CanonicalTest, NormalizationStatus

# Small, explicit, deterministic alias dictionary (task rule 3). Keys are
# the normalized (stripped + casefolded) form of a source test name;
# values are the CanonicalTest.code(s) that name could mean. A key with
# more than one code is *intentionally* ambiguous (task rule 12) — for
# example "t3" alone doesn't say whether a report means Total T3 or Free
# T3, and guessing which one would be a silent medical interpretation
# (task rule 4). Extending this dictionary carefully is the expected way
# to grow coverage; it is not meant to become a comprehensive medical
# dictionary in this foundation.
_ALIAS_TO_CANONICAL_CODES: dict[str, list[str]] = {
    "hemoglobin": ["HEMOGLOBIN"],
    "haemoglobin": ["HEMOGLOBIN"],
    "hgb": ["HEMOGLOBIN"],
    "hb": ["HEMOGLOBIN"],
    # Deliberately ambiguous: "T3" alone doesn't specify Total vs Free.
    "t3": ["T3_TOTAL", "T3_FREE"],
}


@dataclass(frozen=True)
class NormalizationResult:
    """Outcome of normalizing one source test name. `canonical_test` is
    populated only when `status` is RESOLVED; always None otherwise —
    callers should never need to check status before trusting that."""
    status: NormalizationStatus
    canonical_test: CanonicalTest | None = None


def resolve_alias(source_test_name: str) -> list[str]:
    """Pure lookup: normalized source name -> matching canonical
    code(s), with NO database access. Returns an empty list for an
    unknown name, exactly one code for an unambiguous match, or more
    than one code for a deliberately ambiguous alias. Split out from
    normalize_test_name() so the matching decision itself can be tested
    without any database at all (task rule 8: "independently
    testable")."""
    key = source_test_name.strip().casefold()
    return list(_ALIAS_TO_CANONICAL_CODES.get(key, []))


def normalize_test_name(db: Session, source_test_name: str) -> NormalizationResult:
    """
    Deterministically resolve `source_test_name` to a CanonicalTest.

    Never calls Gemini or any other LLM/network service, never uses
    fuzzy matching, and never invents a CanonicalTest row that doesn't
    already exist in the database — an alias resolving to a code with no
    matching seeded CanonicalTest is treated as UNRESOLVED rather than
    raising or fabricating one, since a missing seed is a backend
    configuration gap, not something to guess around.
    """
    codes = resolve_alias(source_test_name)

    if len(codes) == 0:
        return NormalizationResult(status=NormalizationStatus.UNRESOLVED)

    if len(codes) > 1:
        return NormalizationResult(status=NormalizationStatus.AMBIGUOUS)

    canonical_test = (
        db.query(CanonicalTest).filter(CanonicalTest.code == codes[0]).first()
    )
    if canonical_test is None:
        return NormalizationResult(status=NormalizationStatus.UNRESOLVED)

    return NormalizationResult(
        status=NormalizationStatus.RESOLVED, canonical_test=canonical_test
    )
