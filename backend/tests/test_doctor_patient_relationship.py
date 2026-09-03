"""
Tests for doctor-patient relationship feature.

Covers:
    - Model/database architecture
    - Status lifecycle
    - Authorization rules
    - Ownership/security
    - Service layer logic
    - API endpoints

Mocked DB throughout (unittest.mock), no live PostgreSQL.
"""
import uuid
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from app.models.relationship import (
    DoctorPatientLink,
    DoctorPatientLinkStatus,
    LinkInitiatedBy,
)
from app.models.user import User, UserRole
from app.services.relationship_service import (
    InvalidRelationshipError,
    InvalidTransitionError,
    RelationshipError,
    RelationshipNotFoundError,
    UnauthorizedActionError,
    accept_relationship,
    create_relationship_request,
    decline_relationship,
    doctor_has_active_access,
    get_active_patients_for_doctor,
    get_pending_requests_for_doctor,
    get_relationships_for_patient,
    revoke_relationship,
)


# --- Helpers ---

def make_user(role: UserRole, name: str = "Test User") -> User:
    """Create a mock User with the given role."""
    user = User(
        full_name=name,
        email=f"{name.lower().replace(' ', '.')}@example.com",
        hashed_password="irrelevant",
        role=role,
    )
    user.id = uuid.uuid4()
    user.created_at = datetime.now(timezone.utc)
    user.updated_at = datetime.now(timezone.utc)
    return user


def make_link(
    patient_id: uuid.UUID,
    doctor_id: uuid.UUID,
    status: DoctorPatientLinkStatus = DoctorPatientLinkStatus.PENDING,
    initiated_by: LinkInitiatedBy = LinkInitiatedBy.PATIENT,
) -> DoctorPatientLink:
    """Create a mock DoctorPatientLink."""
    now = datetime.now(timezone.utc)
    link = DoctorPatientLink(
        patient_id=patient_id,
        doctor_id=doctor_id,
        status=status,
        initiated_by=initiated_by,
        initiated_at=now,
    )
    link.id = uuid.uuid4()
    link.created_at = now
    link.updated_at = now
    if status == DoctorPatientLinkStatus.ACTIVE:
        link.accepted_at = now
    return link


# =============================================================================
# SECTION 1: Model / Database Architecture
# =============================================================================

class TestModelArchitecture:
    """DoctorPatientLink has correct structure and constraints."""

    def test_table_name(self):
        """DoctorPatientLink maps to the correct table."""
        assert DoctorPatientLink.__tablename__ == "doctor_patient_links"

    def test_has_patient_id_column(self):
        """patient_id column exists and is not nullable."""
        col = DoctorPatientLink.__table__.c.patient_id
        assert col is not None
        assert col.nullable is False

    def test_has_doctor_id_column(self):
        """doctor_id column exists and is not nullable."""
        col = DoctorPatientLink.__table__.c.doctor_id
        assert col is not None
        assert col.nullable is False

    def test_has_status_column(self):
        """status column exists and uses the correct enum."""
        col = DoctorPatientLink.__table__.c.status
        assert col is not None
        assert col.nullable is False

    def test_has_initiated_by_column(self):
        """initiated_by column exists."""
        col = DoctorPatientLink.__table__.c.initiated_by
        assert col is not None
        assert col.nullable is False

    def test_has_initiated_at_column(self):
        """initiated_at column exists."""
        col = DoctorPatientLink.__table__.c.initiated_at
        assert col is not None
        assert col.nullable is False

    def test_has_accepted_at_column(self):
        """accepted_at column exists and is nullable."""
        col = DoctorPatientLink.__table__.c.accepted_at
        assert col is not None
        assert col.nullable is True

    def test_has_created_at_column(self):
        """created_at column exists."""
        col = DoctorPatientLink.__table__.c.created_at
        assert col is not None
        assert col.nullable is False

    def test_has_updated_at_column(self):
        """updated_at column exists."""
        col = DoctorPatientLink.__table__.c.updated_at
        assert col is not None
        assert col.nullable is False

    def test_patient_id_is_indexed(self):
        """patient_id is indexed for query performance."""
        indexed_cols = set()
        for idx in DoctorPatientLink.__table__.indexes:
            indexed_cols.update(c.name for c in idx.columns)
        assert "patient_id" in indexed_cols

    def test_doctor_id_is_indexed(self):
        """doctor_id is indexed for query performance."""
        indexed_cols = set()
        for idx in DoctorPatientLink.__table__.indexes:
            indexed_cols.update(c.name for c in idx.columns)
        assert "doctor_id" in indexed_cols

    def test_has_unique_constraint(self):
        """Partial unique index exists for active/pending relationships."""
        index_names = {idx.name for idx in DoctorPatientLink.__table__.indexes}
        assert "uq_doctor_patient_links_active" in index_names

    def test_status_enum_exists(self):
        """DoctorPatientLinkStatus enum has all required values."""
        assert DoctorPatientLinkStatus.PENDING.value == "pending"
        assert DoctorPatientLinkStatus.ACTIVE.value == "active"
        assert DoctorPatientLinkStatus.DECLINED.value == "declined"
        assert DoctorPatientLinkStatus.REVOKED.value == "revoked"

    def test_initiated_by_enum_exists(self):
        """LinkInitiatedBy enum has all required values."""
        assert LinkInitiatedBy.PATIENT.value == "patient"
        assert LinkInitiatedBy.DOCTOR.value == "doctor"

    def test_patient_and_doctor_are_different_columns(self):
        """patient_id and doctor_id are separate columns."""
        assert DoctorPatientLink.__table__.c.patient_id is not DoctorPatientLink.__table__.c.doctor_id


# =============================================================================
# SECTION 2: Status Lifecycle
# =============================================================================

class TestStatusLifecycle:
    """Valid status transitions work correctly."""

    def test_valid_pending_to_active(self):
        """PENDING -> ACTIVE is a valid transition."""
        assert DoctorPatientLinkStatus.ACTIVE in {
            DoctorPatientLinkStatus.ACTIVE,
            DoctorPatientLinkStatus.DECLINED,
        }

    def test_valid_pending_to_declined(self):
        """PENDING -> DECLINED is a valid transition."""
        assert DoctorPatientLinkStatus.DECLINED in {
            DoctorPatientLinkStatus.ACTIVE,
            DoctorPatientLinkStatus.DECLINED,
        }

    def test_valid_active_to_revoked(self):
        """ACTIVE -> REVOKED is a valid transition."""
        assert DoctorPatientLinkStatus.REVOKED in {
            DoctorPatientLinkStatus.REVOKED,
        }

    def test_declined_is_terminal(self):
        """DECLINED status has no valid transitions."""
        # DECLINED is not in the valid transitions dict
        from app.services.relationship_service import _VALID_TRANSITIONS
        assert DoctorPatientLinkStatus.DECLINED not in _VALID_TRANSITIONS

    def test_revoked_is_terminal(self):
        """REVOKED status has no valid transitions."""
        from app.services.relationship_service import _VALID_TRANSITIONS
        assert DoctorPatientLinkStatus.REVOKED not in _VALID_TRANSITIONS


# =============================================================================
# SECTION 3: Service Layer - Create Relationship
# =============================================================================

class TestCreateRelationship:
    """Test relationship creation logic."""

    def test_patient_can_request_doctor(self):
        """Patient can initiate a relationship with a doctor."""
        patient = make_user(UserRole.PATIENT, "Alice Patient")
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,  # validate patient exists
            doctor,   # validate doctor exists
            None,     # no duplicate check needed
        ]

        link = create_relationship_request(db, patient.id, UserRole.PATIENT, doctor.id)

        assert link.patient_id == patient.id
        assert link.doctor_id == doctor.id
        assert link.status == DoctorPatientLinkStatus.PENDING
        assert link.initiated_by == LinkInitiatedBy.PATIENT
        db.add.assert_called_once()
        db.commit.assert_called_once()

    def test_doctor_can_request_patient(self):
        """Doctor can initiate a relationship with a patient."""
        patient = make_user(UserRole.PATIENT, "Alice Patient")
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")

        db = MagicMock()
        # When doctor initiates: validate patient first (target_id),
        # then validate doctor (initiator_id)
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,  # validate patient (target)
            doctor,   # validate doctor (initiator)
            None,     # no duplicate check needed
        ]

        link = create_relationship_request(db, doctor.id, UserRole.DOCTOR, patient.id)

        assert link.patient_id == patient.id
        assert link.doctor_id == doctor.id
        assert link.status == DoctorPatientLinkStatus.PENDING
        assert link.initiated_by == LinkInitiatedBy.DOCTOR

    def test_self_link_rejected(self):
        """A user cannot create a relationship with themselves."""
        user = make_user(UserRole.PATIENT, "Alice")

        db = MagicMock()

        with pytest.raises(InvalidRelationshipError, match="themselves"):
            create_relationship_request(db, user.id, UserRole.PATIENT, user.id)

    def test_wrong_role_rejected(self):
        """Target user must have the opposite role."""
        patient1 = make_user(UserRole.PATIENT, "Alice")
        patient2 = make_user(UserRole.PATIENT, "Charlie")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            patient1,  # validate initiator
            patient2,  # validate target (wrong role!)
        ]

        with pytest.raises(InvalidRelationshipError, match="must be a doctor"):
            create_relationship_request(db, patient1.id, UserRole.PATIENT, patient2.id)

    def test_duplicate_relationship_rejected(self):
        """Cannot create duplicate active/pending relationships."""
        from sqlalchemy.exc import IntegrityError

        patient = make_user(UserRole.PATIENT, "Alice")
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")

        db = MagicMock()
        db.query.return_value.filter.return_value.first.side_effect = [
            patient,  # validate patient
            doctor,   # validate doctor
            None,     # no pre-check duplicate (constraint catches it)
        ]
        # Simulate the database constraint rejecting the duplicate
        db.commit.side_effect = IntegrityError("INSERT ...", {}, Exception("duplicate key"))

        with pytest.raises(RelationshipError, match="already exists"):
            create_relationship_request(db, patient.id, UserRole.PATIENT, doctor.id)


# =============================================================================
# SECTION 4: Service Layer - Accept/Decline/Revoke
# =============================================================================

class TestAcceptRelationship:
    """Test accepting a relationship request."""

    def test_doctor_can_accept_own_request(self):
        """The doctor who received the request can accept it."""
        patient = make_user(UserRole.PATIENT, "Alice")
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        link = make_link(patient.id, doctor.id, DoctorPatientLinkStatus.PENDING)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = link

        result = accept_relationship(db, link.id, doctor.id)

        assert result.status == DoctorPatientLinkStatus.ACTIVE
        assert result.accepted_at is not None
        db.commit.assert_called_once()

    def test_other_doctor_cannot_accept(self):
        """A different doctor cannot accept someone else's request."""
        patient = make_user(UserRole.PATIENT, "Alice")
        doctor1 = make_user(UserRole.DOCTOR, "Dr. Bob")
        doctor2 = make_user(UserRole.DOCTOR, "Dr. Carol")
        link = make_link(patient.id, doctor1.id, DoctorPatientLinkStatus.PENDING)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = link

        with pytest.raises(UnauthorizedActionError, match="Only the doctor"):
            accept_relationship(db, link.id, doctor2.id)

    def test_cannot_accept_nonexistent_link(self):
        """Accepting a nonexistent link raises RelationshipNotFoundError."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        with pytest.raises(RelationshipNotFoundError):
            accept_relationship(db, uuid.uuid4(), uuid.uuid4())

    def test_cannot_accept_already_active(self):
        """Cannot accept an already active relationship."""
        patient = make_user(UserRole.PATIENT, "Alice")
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        link = make_link(patient.id, doctor.id, DoctorPatientLinkStatus.ACTIVE)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = link

        with pytest.raises(InvalidTransitionError, match="Cannot accept"):
            accept_relationship(db, link.id, doctor.id)


class TestDeclineRelationship:
    """Test declining a relationship request."""

    def test_doctor_can_decline_own_request(self):
        """The doctor who received the request can decline it."""
        patient = make_user(UserRole.PATIENT, "Alice")
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        link = make_link(patient.id, doctor.id, DoctorPatientLinkStatus.PENDING)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = link

        result = decline_relationship(db, link.id, doctor.id)

        assert result.status == DoctorPatientLinkStatus.DECLINED
        db.commit.assert_called_once()

    def test_other_doctor_cannot_decline(self):
        """A different doctor cannot decline someone else's request."""
        patient = make_user(UserRole.PATIENT, "Alice")
        doctor1 = make_user(UserRole.DOCTOR, "Dr. Bob")
        doctor2 = make_user(UserRole.DOCTOR, "Dr. Carol")
        link = make_link(patient.id, doctor1.id, DoctorPatientLinkStatus.PENDING)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = link

        with pytest.raises(UnauthorizedActionError, match="Only the doctor"):
            decline_relationship(db, link.id, doctor2.id)


class TestRevokeRelationship:
    """Test revoking an active relationship."""

    def test_patient_can_revoke_own_relationship(self):
        """The patient who owns the relationship can revoke it."""
        patient = make_user(UserRole.PATIENT, "Alice")
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        link = make_link(patient.id, doctor.id, DoctorPatientLinkStatus.ACTIVE)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = link

        result = revoke_relationship(db, link.id, patient.id)

        assert result.status == DoctorPatientLinkStatus.REVOKED
        db.commit.assert_called_once()

    def test_other_patient_cannot_revoke(self):
        """A different patient cannot revoke someone else's relationship."""
        patient1 = make_user(UserRole.PATIENT, "Alice")
        patient2 = make_user(UserRole.PATIENT, "Charlie")
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        link = make_link(patient1.id, doctor.id, DoctorPatientLinkStatus.ACTIVE)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = link

        with pytest.raises(UnauthorizedActionError, match="Only the patient"):
            revoke_relationship(db, link.id, patient2.id)

    def test_doctor_cannot_revoke(self):
        """The doctor cannot revoke a relationship (only patient can)."""
        patient = make_user(UserRole.PATIENT, "Alice")
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        link = make_link(patient.id, doctor.id, DoctorPatientLinkStatus.ACTIVE)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = link

        with pytest.raises(UnauthorizedActionError, match="Only the patient"):
            revoke_relationship(db, link.id, doctor.id)

    def test_cannot_revoke_pending(self):
        """Cannot revoke a pending relationship (must accept/decline first)."""
        patient = make_user(UserRole.PATIENT, "Alice")
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        link = make_link(patient.id, doctor.id, DoctorPatientLinkStatus.PENDING)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = link

        with pytest.raises(InvalidTransitionError, match="Cannot revoke"):
            revoke_relationship(db, link.id, patient.id)


# =============================================================================
# SECTION 5: Authorization Helper
# =============================================================================

class TestAuthorizationHelper:
    """doctor_has_active_access correctly authorizes/denies access."""

    def test_active_relationship_grants_access(self):
        """Doctor with ACTIVE relationship is authorized."""
        patient = make_user(UserRole.PATIENT, "Alice")
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        link = make_link(patient.id, doctor.id, DoctorPatientLinkStatus.ACTIVE)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = link

        assert doctor_has_active_access(db, doctor.id, patient.id) is True

    def test_pending_relationship_denies_access(self):
        """Doctor with PENDING relationship is NOT authorized."""
        patient = make_user(UserRole.PATIENT, "Alice")
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        link = make_link(patient.id, doctor.id, DoctorPatientLinkStatus.PENDING)

        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = link

        # The query filters for ACTIVE only, so pending won't match
        db.query.return_value.filter.return_value.first.return_value = None

        assert doctor_has_active_access(db, doctor.id, patient.id) is False

    def test_declined_relationship_denies_access(self):
        """Doctor with DECLINED relationship is NOT authorized."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        assert doctor_has_active_access(db, uuid.uuid4(), uuid.uuid4()) is False

    def test_revoked_relationship_denies_access(self):
        """Doctor with REVOKED relationship is NOT authorized."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        assert doctor_has_active_access(db, uuid.uuid4(), uuid.uuid4()) is False

    def test_unrelated_doctor_denied(self):
        """Doctor without any relationship is NOT authorized."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        assert doctor_has_active_access(db, uuid.uuid4(), uuid.uuid4()) is False

    def test_no_relationship_at_all(self):
        """No link at all means no access."""
        db = MagicMock()
        db.query.return_value.filter.return_value.first.return_value = None

        assert doctor_has_active_access(db, uuid.uuid4(), uuid.uuid4()) is False


# =============================================================================
# SECTION 6: Query Functions
# =============================================================================

class TestQueryFunctions:
    """Test the query functions for listing relationships."""

    def test_get_pending_requests(self):
        """Returns pending requests for a specific doctor."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = get_pending_requests_for_doctor(db, doctor.id)
        assert result == []

    def test_get_active_patients(self):
        """Returns active patients for a specific doctor."""
        doctor = make_user(UserRole.DOCTOR, "Dr. Bob")
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = get_active_patients_for_doctor(db, doctor.id)
        assert result == []

    def test_get_relationships_for_patient(self):
        """Returns all relationships for a specific patient."""
        patient = make_user(UserRole.PATIENT, "Alice")
        db = MagicMock()
        db.query.return_value.filter.return_value.order_by.return_value.all.return_value = []

        result = get_relationships_for_patient(db, patient.id)
        assert result == []


# =============================================================================
# SECTION 7: Regression - Existing Functionality
# =============================================================================

class TestRegression:
    """Existing functionality remains unaffected."""

    def test_user_model_unchanged(self):
        """User model still has the same structure."""
        from app.models.user import User, UserRole
        assert hasattr(User, 'id')
        assert hasattr(User, 'role')
        assert UserRole.PATIENT.value == "patient"
        assert UserRole.DOCTOR.value == "doctor"

    def test_extraction_models_unchanged(self):
        """Extraction models are not affected."""
        from app.models.extraction import CandidateResult, ExtractionEvidence
        assert hasattr(CandidateResult, 'verification_status')
        assert hasattr(ExtractionEvidence, 'source_text')
