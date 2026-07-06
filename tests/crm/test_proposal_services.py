from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.crm import services
from app.crm.exceptions import (
    EstimationLockedError,
    EstimationValidationError,
    ImmutableVersionError,
    ProposalError,
)
from app.crm.models import (
    Estimation,
    LeadStatus,
    Person,
    PricingModel,
    Proposal,
    ProposalVersion,
    QualificationStatus,
    RateUnit,
)
from app.crm.services import (
    add_version,
    create_proposal,
    promote_to_lead,
    set_estimation,
)

FIXED_FIELDS = {"pricing_model": PricingModel.FIXED, "fixed_price": Decimal("5000.00")}
TIME_FIELDS = {
    "pricing_model": PricingModel.TIME_BASED,
    "rate_amount": Decimal("120.00"),
    "rate_unit": RateUnit.HOURLY,
    "estimated_units": Decimal("80"),
}


@pytest.fixture()
def lead(db):
    person = Person(
        name="Prospect Pat", qualification_status=QualificationStatus.QUALIFIED
    )
    db.session.add(person)
    db.session.commit()
    return promote_to_lead(person)


@pytest.fixture()
def proposal(db, lead, test_user):
    return create_proposal(lead, "Website revamp", "v1 content", test_user)


# --- create_proposal ---------------------------------------------------------


def test_create_proposal_creates_v1_and_transitions_open_to_proposal(db, lead, test_user):
    assert lead.status == LeadStatus.OPEN

    proposal = create_proposal(lead, "Website revamp", "the pitch", test_user)

    assert proposal.id is not None
    assert proposal.title == "Website revamp"
    assert lead.status == LeadStatus.PROPOSAL
    versions = proposal.versions
    assert len(versions) == 1
    assert versions[0].version_number == 1
    assert versions[0].content == "the pitch"
    assert versions[0].created_by == test_user.id


def test_second_proposal_does_not_refire_or_alter_status(db, lead, test_user):
    create_proposal(lead, "First", "content", test_user)
    assert lead.status == LeadStatus.PROPOSAL

    create_proposal(lead, "Second", "content", test_user)

    assert lead.status == LeadStatus.PROPOSAL
    assert Proposal.query.filter_by(lead_id=lead.id).count() == 2


def test_create_proposal_does_not_regress_later_status(db, lead, test_user):
    # Test setup only — app code must never assign status directly (CLAUDE.md #7).
    lead.status = LeadStatus.OFFER_SENT
    db.session.commit()

    create_proposal(lead, "Renegotiation", "content", test_user)

    assert lead.status == LeadStatus.OFFER_SENT


@pytest.mark.parametrize(
    "terminal", [LeadStatus.WON, LeadStatus.LOST], ids=lambda s: s.value
)
def test_create_proposal_on_terminal_lead_raises(db, lead, test_user, terminal):
    lead.status = terminal  # test setup only
    db.session.commit()

    with pytest.raises(ProposalError):
        create_proposal(lead, "Too late", "content", test_user)
    assert Proposal.query.count() == 0


# --- add_version -------------------------------------------------------------


def test_add_version_numbers_monotonically(db, proposal, test_user):
    v2 = add_version(proposal, "v2 content", test_user)
    v3 = add_version(proposal, "v3 content", test_user)

    assert v2.version_number == 2
    assert v3.version_number == 3
    assert [v.version_number for v in proposal.versions] == [1, 2, 3]


def test_add_version_on_terminal_lead_raises(db, proposal, test_user):
    proposal.lead.status = LeadStatus.LOST  # test setup only
    db.session.commit()

    with pytest.raises(ProposalError):
        add_version(proposal, "v2 content", test_user)


def test_db_constraint_rejects_duplicate_version_number(db, proposal, test_user):
    db.session.add(
        ProposalVersion(
            proposal_id=proposal.id,
            version_number=1,
            content="duplicate",
            created_by=test_user.id,
        )
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


# --- invariant #3: version immutability -------------------------------------


def test_version_rows_cannot_be_updated(db, proposal):
    version = proposal.versions[0]
    version.content = "sneaky edit"

    with pytest.raises(ImmutableVersionError):
        db.session.flush()
    db.session.rollback()

    db.session.expire_all()
    assert proposal.versions[0].content == "v1 content"


def test_version_rows_cannot_be_deleted(db, proposal):
    version = proposal.versions[0]
    db.session.delete(version)

    with pytest.raises(ImmutableVersionError):
        db.session.flush()
    db.session.rollback()

    assert ProposalVersion.query.count() == 1


# --- invariant #4: one estimation per version -------------------------------


def test_db_constraint_rejects_second_estimation_on_version(db, proposal):
    version = proposal.versions[0]
    set_estimation(version, **FIXED_FIELDS)

    db.session.add(
        Estimation(
            proposal_version_id=version.id,
            pricing_model=PricingModel.FIXED,
            fixed_price=Decimal("1.00"),
        )
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_set_estimation_updates_rather_than_duplicates(db, proposal):
    version = proposal.versions[0]
    first = set_estimation(version, **FIXED_FIELDS)
    second = set_estimation(version, fixed_price=Decimal("7500.00"))

    assert second.id == first.id
    assert Estimation.query.count() == 1
    assert version.estimation.fixed_price == Decimal("7500.00")


# --- copy-forward ------------------------------------------------------------


def test_copy_forward_creates_independent_estimation_row(db, proposal, test_user):
    v1 = proposal.versions[0]
    original = set_estimation(v1, **TIME_FIELDS)

    v2 = add_version(proposal, "v2 content", test_user)

    copy = v2.estimation
    assert copy is not None
    assert copy.id != original.id
    assert copy.pricing_model == PricingModel.TIME_BASED
    assert copy.rate_amount == Decimal("120.00")
    assert copy.rate_unit == RateUnit.HOURLY
    assert copy.estimated_units == Decimal("80")

    set_estimation(v2, rate_amount=Decimal("150.00"))
    db.session.expire_all()
    assert v1.estimation.rate_amount == Decimal("120.00")
    assert v2.estimation.rate_amount == Decimal("150.00")


def test_copy_estimation_false_yields_bare_version(db, proposal, test_user):
    set_estimation(proposal.versions[0], **FIXED_FIELDS)

    v2 = add_version(proposal, "v2 content", test_user, copy_estimation=False)

    assert v2.estimation is None


def test_copy_forward_with_no_source_estimation_yields_bare_version(db, proposal, test_user):
    v2 = add_version(proposal, "v2 content", test_user)

    assert v2.estimation is None


# --- estimation validation matrix --------------------------------------------


def test_fixed_with_price_is_valid(db, proposal):
    estimation = set_estimation(proposal.versions[0], **FIXED_FIELDS)
    assert estimation.pricing_model == PricingModel.FIXED
    assert estimation.fixed_price == Decimal("5000.00")


def test_fixed_missing_price_raises_and_names_field(db, proposal):
    with pytest.raises(EstimationValidationError) as exc:
        set_estimation(proposal.versions[0], pricing_model=PricingModel.FIXED)
    assert "fixed_price" in str(exc.value)


@pytest.mark.parametrize(
    "extra",
    [
        {"rate_amount": Decimal("120.00")},
        {"rate_unit": RateUnit.DAILY},
        {"estimated_units": Decimal("10")},
        {"additional_rate": Decimal("90.00")},
    ],
    ids=lambda d: next(iter(d)),
)
def test_fixed_forbids_time_based_fields(db, proposal, extra):
    with pytest.raises(EstimationValidationError) as exc:
        set_estimation(proposal.versions[0], **FIXED_FIELDS, **extra)
    assert next(iter(extra)) in str(exc.value)


def test_time_based_with_required_fields_is_valid(db, proposal):
    estimation = set_estimation(proposal.versions[0], **TIME_FIELDS)
    assert estimation.pricing_model == PricingModel.TIME_BASED


def test_time_based_additional_rate_is_optional_and_allowed(db, proposal):
    estimation = set_estimation(
        proposal.versions[0], **TIME_FIELDS, additional_rate=Decimal("150.00")
    )
    assert estimation.additional_rate == Decimal("150.00")


def test_time_based_missing_fields_raises_and_names_them(db, proposal):
    with pytest.raises(EstimationValidationError) as exc:
        set_estimation(
            proposal.versions[0],
            pricing_model=PricingModel.TIME_BASED,
            rate_amount=Decimal("120.00"),
        )
    message = str(exc.value)
    assert "rate_unit" in message
    assert "estimated_units" in message


def test_time_based_forbids_fixed_price(db, proposal):
    with pytest.raises(EstimationValidationError) as exc:
        set_estimation(proposal.versions[0], **TIME_FIELDS, fixed_price=Decimal("1.00"))
    assert "fixed_price" in str(exc.value)


def test_missing_pricing_model_raises(db, proposal):
    with pytest.raises(EstimationValidationError):
        set_estimation(proposal.versions[0], fixed_price=Decimal("5000.00"))


def test_unknown_field_raises(db, proposal):
    with pytest.raises(EstimationValidationError):
        set_estimation(proposal.versions[0], **FIXED_FIELDS, price="oops")


def test_switching_pricing_model_requires_clearing_old_fields(db, proposal):
    version = proposal.versions[0]
    set_estimation(version, **FIXED_FIELDS)

    # Old fixed_price still set -> forbidden under TIME_BASED.
    with pytest.raises(EstimationValidationError):
        set_estimation(version, **TIME_FIELDS)

    # Explicitly clearing it makes the switch legal.
    estimation = set_estimation(version, **TIME_FIELDS, fixed_price=None)
    assert estimation.pricing_model == PricingModel.TIME_BASED
    assert estimation.fixed_price is None


# --- lock hook (invariant #6 seam, real check arrives in phase 4) ------------


def test_set_estimation_respects_lock_hook(db, proposal, monkeypatch):
    version = proposal.versions[0]
    set_estimation(version, **FIXED_FIELDS)

    monkeypatch.setattr(services, "estimation_is_locked", lambda estimation: True)

    with pytest.raises(EstimationLockedError):
        set_estimation(version, fixed_price=Decimal("6000.00"))


def test_estimation_is_locked_placeholder_returns_false(db, proposal):
    version = proposal.versions[0]
    estimation = set_estimation(version, **FIXED_FIELDS)
    assert services.estimation_is_locked(estimation) is False
