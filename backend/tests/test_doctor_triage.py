"""
Tests for the doctor PENDING / abnormal-results triage view
(app.services.doctor_report_service.get_pending_triage_results and
GET /doctor/triage).

This feature reuses existing authorization (ACTIVE DoctorPatientLink),
existing persisted candidate fields, and existing schemas — these tests
cover only the genuinely new behavior:
    - Only PENDING candidates are returned (shape check; the ACTIVE-link
      and PENDING filters themselves are SQL-level and exercised by the
      same mocked-query approach already used elsewhere in this suite)
    - Abnormal (HIGH/LOW) results are surfaced ahead of normal ones
    - The returned entries carry the patient/report context alongside
      the existing safe candidate fields
    - The endpoint performs no mutation and no new authorization system

Service-layer tests run without PostgreSQL (mocked DB), matching the
rest of this suite. API endpoint (TestClient) tests are BLOCKED here
for the same reason as the rest of the doctor-router suite —
PostgreSQL is not available in this sandbox (see test_doctor_review_
workspace.py's identical note).
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock

from app.models.extraction import AbnormalityStatus, CandidateVerificationStatus
from app.models.user import UserRole
from app.services.doctor_report_service import get_pending_triage_results
from tests.test_doctor_review_workspace import make_candidate_result, make_report, make_user


def setup_triage_db_mocks(db: MagicMock, rows: list):
    """Mock db.query for get_pending_triage_results's two query calls:
    1. the ACTIVE-patient-id subquery
    2. the CandidateResult/Report/User join, ending in .all() -> rows
    """
    call_count = [0]

    def side_effect(*args, **kwargs):
        call_count[0] += 1
        if call_count[0] == 1:
            subquery_mock = MagicMock()
            subquery_mock.filter.return_value.subquery.return_value = MagicMock()
            return subquery_mock
        main_mock = MagicMock()
        chain = main_mock.join.return_value.join.return_value.join.return_value
        chain.filter.return_value.options.return_value.order_by.return_value.all.return_value = rows
        return main_mock

    db.query.side_effect = side_effect


def make_row(patient, report, candidate):
    return (candidate, report, patient)


class TestTriagePendingOnly:
    def test_returns_shaped_entries_for_pending_candidates(self):
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "blood_test.pdf")
        candidate = make_candidate_result(
            verification=CandidateVerificationStatus.PENDING
        )

        db = MagicMock()
        setup_triage_db_mocks(db, [make_row(patient, report, candidate)])

        entries = get_pending_triage_results(db, doctor.id)

        assert len(entries) == 1
        entry = entries[0]
        assert entry["patient_id"] == patient.id
        assert entry["patient_name"] == "Alice"
        assert entry["report_id"] == report.id
        assert entry["report_original_filename"] == "blood_test.pdf"
        assert entry["candidate"] is candidate
        assert entry["candidate"].verification_status == CandidateVerificationStatus.PENDING


class TestTriageAbnormalitySurfacing:
    def test_abnormal_results_surfaced_before_normal_results(self):
        """HIGH/LOW candidates should come before NORMAL/UNRESOLVED/
        NOT_APPLICABLE ones, using only the already-persisted
        abnormality_status — no new classification logic is run."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "blood_test.pdf")

        normal_candidate = make_candidate_result(
            test_name="Sodium", abnormality=AbnormalityStatus.NORMAL
        )
        high_candidate = make_candidate_result(
            test_name="Potassium", abnormality=AbnormalityStatus.HIGH
        )
        unresolved_candidate = make_candidate_result(
            test_name="Glucose", abnormality=AbnormalityStatus.UNRESOLVED
        )
        low_candidate = make_candidate_result(
            test_name="Calcium", abnormality=AbnormalityStatus.LOW
        )

        # Deliberately queried back in a non-abnormal-first order (as a
        # created_at-desc query would return them if the most recent
        # result happens to be normal).
        rows = [
            make_row(patient, report, normal_candidate),
            make_row(patient, report, high_candidate),
            make_row(patient, report, unresolved_candidate),
            make_row(patient, report, low_candidate),
        ]

        db = MagicMock()
        setup_triage_db_mocks(db, rows)

        entries = get_pending_triage_results(db, doctor.id)

        statuses = [e["candidate"].abnormality_status for e in entries]
        high_low_positions = [
            i for i, s in enumerate(statuses)
            if s in (AbnormalityStatus.HIGH, AbnormalityStatus.LOW)
        ]
        other_positions = [
            i for i, s in enumerate(statuses)
            if s not in (AbnormalityStatus.HIGH, AbnormalityStatus.LOW)
        ]
        assert max(high_low_positions) < min(other_positions)

    def test_no_candidates_returns_empty_list(self):
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        db = MagicMock()
        setup_triage_db_mocks(db, [])

        entries = get_pending_triage_results(db, doctor.id)

        assert entries == []


class TestTriageReadOnly:
    def test_triage_function_never_calls_commit_or_add(self):
        """This is a pure read view: it must never write to the DB."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        patient = make_user(UserRole.PATIENT, "Alice")
        report = make_report(patient.id, "blood_test.pdf")
        candidate = make_candidate_result()

        db = MagicMock()
        setup_triage_db_mocks(db, [make_row(patient, report, candidate)])

        get_pending_triage_results(db, doctor.id)

        db.commit.assert_not_called()
        db.add.assert_not_called()
        db.delete.assert_not_called()

    def test_triage_endpoint_route_exists_and_is_get_only(self):
        """No verify/correct/reject/mutation route was accidentally
        introduced alongside this read-only view."""
        from app.routers.doctor_reports import router

        triage_routes = [r for r in router.routes if r.path == "/doctor/triage"]
        assert len(triage_routes) == 1
        assert triage_routes[0].methods == {"GET"}
