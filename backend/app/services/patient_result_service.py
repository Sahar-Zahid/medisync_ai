"""
Patient trusted-results read service.

Single responsibility: let an authenticated patient read their own
trusted `TestResult` rows. This is the patient-side counterpart to the
doctor verification workflow (verify/correct/reject_candidate_service) —
it never creates, mutates, or deletes anything.

Trust boundary (see app/models/extraction.py:TestResult docstring):
    Gemini -> CandidateResult (PENDING) -> Doctor review
        -> VERIFY / CORRECT -> TestResult -> patient trusted results
        -> REJECT -> no TestResult ever created

`TestResult` never stores patient_id directly — ownership is always
derived through the existing chain: TestResult.extraction_run_id ->
CandidateExtraction.report_id -> Report.patient_id. This mirrors the
same join doctor_report_service/verification_history_service use to
scope by patient; there is no new authorization mechanism here, and
patient_id always comes from the authenticated caller (current_user.id
in the router), never from a client-supplied value.

Only TestResultStatus.VERIFIED and TestResultStatus.CORRECTED rows are
ever returned. PENDING is a transitional state that should not surface
to a patient, and REJECTED candidates never produce a TestResult in the
first place (reject_candidate_service creates no TestResult row at
all) — the explicit status filter is a defense-in-depth belt-and-braces
check, not a workaround for something the reject path is expected to
do.
"""
import uuid

from sqlalchemy.orm import Session, joinedload

from app.models.extraction import CandidateExtraction, TestResult, TestResultStatus
from app.models.report import Report

# The only statuses that represent trusted, doctor-reviewed data. PENDING
# and REJECTED are deliberately excluded — see module docstring.
_TRUSTED_STATUSES = (TestResultStatus.VERIFIED, TestResultStatus.CORRECTED)


def get_patient_trusted_results(
    db: Session, patient_id: uuid.UUID
) -> list[TestResult]:
    """Return the authenticated patient's own trusted TestResult rows.

    Strictly read-only: no db.add/commit/delete, and neither
    CandidateResult nor TestResult is mutated. patient_id must come from
    the authenticated session (see routers/results.py) — this function
    performs no authentication or role check of its own, matching the
    existing pattern where routers resolve identity via
    require_patient/get_current_user and services take the resolved ID.

    Ordering is deterministic: newest result_date first (NULLs last,
    since a result without a normalized date has no meaningful recency),
    then verified_at, then id as a final tiebreaker so ties never
    reorder between requests.
    """
    return (
        db.query(TestResult)
        .join(
            CandidateExtraction,
            TestResult.extraction_run_id == CandidateExtraction.id,
        )
        .join(Report, CandidateExtraction.report_id == Report.id)
        .options(joinedload(TestResult.canonical_test))
        .filter(
            Report.patient_id == patient_id,
            TestResult.status.in_(_TRUSTED_STATUSES),
        )
        .order_by(
            TestResult.result_date.is_(None),
            TestResult.result_date.desc(),
            TestResult.verified_at.desc(),
            TestResult.id.asc(),
        )
        .all()
    )


def get_patient_trusted_results_history(
    db: Session, patient_id: uuid.UUID
) -> list[TestResult]:
    """Return the authenticated patient's own trusted TestResult rows for
    the read-only history/timeline view (GET /patient/results/history).

    Deliberately a thin wrapper around get_patient_trusted_results rather
    than a second query: the history view needs exactly the same rows,
    the same VERIFIED/CORRECTED-only trust filter, and — per the task
    spec ("ordered deterministically by result date, newest first ...
    use a deterministic secondary ordering already supported by the
    schema") — the exact same ordering (result_date desc, NULLs last,
    then verified_at desc, then id asc as the final tiebreaker) that
    get_patient_trusted_results already provides. Having one function
    own "what counts as trusted, in what order" means the results page
    and the history page can never silently drift apart or disagree.
    """
    return get_patient_trusted_results(db, patient_id)
