from decimal import Decimal

import pytest

from app.crm.exceptions import OfferStateError
from app.crm.models import (
    LeadStatus,
    Offer,
    OfferStatus,
    Organization,
    Person,
    PricingModel,
    QualificationStatus,
    RateUnit,
)
from app.crm.services import (
    create_offer,
    create_proposal,
    promote_to_lead,
    send_offer,
    set_estimation,
)
from app.projects.models import (
    BudgetType,
    Customer,
    CustomerType,
    OverHoursPolicy,
    Project,
)
from app.services import offer_acceptance
from app.services.offer_acceptance import (
    HOURS_PER_DAY,
    OrganizationRequiredError,
    accept_offer,
)

TIME_HOURLY_WITH_ADDITIONAL = {
    "pricing_model": PricingModel.TIME_BASED,
    "rate_amount": Decimal("120.00"),
    "rate_unit": RateUnit.HOURLY,
    "estimated_units": Decimal("80"),
    "additional_rate": Decimal("150.00"),
}
TIME_DAILY_WITH_ADDITIONAL = {
    "pricing_model": PricingModel.TIME_BASED,
    "rate_amount": Decimal("960.00"),
    "rate_unit": RateUnit.DAILY,
    "estimated_units": Decimal("10"),
    "additional_rate": Decimal("150.00"),
}
TIME_HOURLY_NO_ADDITIONAL = {
    "pricing_model": PricingModel.TIME_BASED,
    "rate_amount": Decimal("120.00"),
    "rate_unit": RateUnit.HOURLY,
    "estimated_units": Decimal("80"),
}
FIXED = {
    "pricing_model": PricingModel.FIXED,
    "fixed_price": Decimal("15000.00"),
}


def make_sent_offer(db, user, estimation_fields, organization=None, person_name="Client Carla"):
    person = Person(
        name=person_name,
        qualification_status=QualificationStatus.QUALIFIED,
        organization=organization,
    )
    db.session.add(person)
    db.session.commit()
    lead = promote_to_lead(person)
    proposal = create_proposal(lead, f"Project for {person_name}", "scope", user)
    version = proposal.versions[0]
    set_estimation(version, **estimation_fields)
    offer = create_offer(version)
    return send_offer(offer)


@pytest.fixture()
def organization(db):
    org = Organization(name="Acme GmbH")
    db.session.add(org)
    db.session.commit()
    return org


# --- §4.4 budget mapping (happy paths) ---------------------------------------


def test_accept_time_based_with_additional_rate_maps_hard_bill_at_rate(
    db, test_user, organization
):
    offer = make_sent_offer(db, test_user, TIME_HOURLY_WITH_ADDITIONAL, organization)
    lead = offer.version.proposal.lead

    project = accept_offer(offer, test_user)

    assert project.id is not None
    assert project.name == "Project for Client Carla"
    assert project.manager_id == test_user.id
    assert project.offer_id == offer.id
    assert project.budget_type == BudgetType.HARD
    assert project.budget_hours == Decimal("80")
    assert project.over_hours_policy == OverHoursPolicy.BILL_AT_RATE
    assert project.over_rate == Decimal("150.00")

    customer = Customer.query.one()
    assert customer.organization_id == organization.id
    assert customer.type == CustomerType.EXTERNAL
    assert project.customer_id == customer.id

    assert offer.status == OfferStatus.ACCEPTED
    assert offer.decided_at is not None
    assert lead.status == LeadStatus.WON


def test_accept_daily_units_normalize_to_hours(db, test_user, organization):
    offer = make_sent_offer(db, test_user, TIME_DAILY_WITH_ADDITIONAL, organization)

    project = accept_offer(offer, test_user)

    assert HOURS_PER_DAY == Decimal("8")
    assert project.budget_hours == Decimal("80")  # 10 days x 8 — Decimal-exact
    assert project.budget_type == BudgetType.HARD


def test_accept_time_based_without_additional_rate_maps_soft(db, test_user, organization):
    offer = make_sent_offer(db, test_user, TIME_HOURLY_NO_ADDITIONAL, organization)

    project = accept_offer(offer, test_user)

    assert project.budget_type == BudgetType.SOFT
    assert project.budget_hours == Decimal("80")  # reference value
    assert project.over_hours_policy is None
    assert project.over_rate is None


def test_accept_fixed_maps_soft_with_null_hours(db, test_user, organization):
    offer = make_sent_offer(db, test_user, FIXED, organization)

    project = accept_offer(offer, test_user)

    assert project.budget_type == BudgetType.SOFT
    assert project.budget_hours is None  # entered manually later (DECISIONS.md)
    assert project.over_hours_policy is None
    assert project.over_rate is None


# --- halt-and-prompt: missing organization -----------------------------------


def test_missing_organization_halts_and_persists_nothing(db, test_user):
    offer = make_sent_offer(db, test_user, FIXED, organization=None)
    lead = offer.version.proposal.lead

    with pytest.raises(OrganizationRequiredError):
        accept_offer(offer, test_user)

    assert Customer.query.count() == 0
    assert Project.query.count() == 0
    db.session.expire_all()
    assert offer.status == OfferStatus.SENT
    assert lead.status == LeadStatus.OFFER_SENT


# --- invariant #8: atomicity under forced failure -----------------------------


def test_forced_late_failure_rolls_back_customer_and_project(
    db, test_user, organization, monkeypatch
):
    offer = make_sent_offer(db, test_user, FIXED, organization)
    lead = offer.version.proposal.lead

    def boom(lead):
        raise RuntimeError("forced mid-transaction failure")

    monkeypatch.setattr(offer_acceptance, "_mark_lead_won", boom)

    with pytest.raises(RuntimeError):
        accept_offer(offer, test_user)

    assert Customer.query.count() == 0
    assert Project.query.count() == 0
    db.session.expire_all()
    assert offer.status == OfferStatus.SENT
    assert lead.status == LeadStatus.OFFER_SENT


# --- invariant #9: customer reuse ---------------------------------------------


def test_second_acceptance_from_same_organization_reuses_customer(
    db, test_user, organization
):
    first = make_sent_offer(
        db, test_user, FIXED, organization, person_name="First Contact"
    )
    accept_offer(first, test_user)

    second = make_sent_offer(
        db, test_user, TIME_HOURLY_NO_ADDITIONAL, organization, person_name="Second Contact"
    )
    accept_offer(second, test_user)

    assert Customer.query.count() == 1
    customer = Customer.query.one()
    projects = Project.query.order_by(Project.id).all()
    assert len(projects) == 2
    assert all(p.customer_id == customer.id for p in projects)


# --- invariant #7: only SENT can be accepted ----------------------------------


def test_accept_requires_sent_draft_raises(db, test_user, organization):
    person = Person(
        name="Draft Dana",
        qualification_status=QualificationStatus.QUALIFIED,
        organization=organization,
    )
    db.session.add(person)
    db.session.commit()
    lead = promote_to_lead(person)
    proposal = create_proposal(lead, "Draft proposal", "scope", test_user)
    version = proposal.versions[0]
    set_estimation(version, **FIXED)
    offer = create_offer(version)  # DRAFT, never sent

    with pytest.raises(OfferStateError):
        accept_offer(offer, test_user)
    assert Project.query.count() == 0


def test_reaccepting_accepted_offer_raises(db, test_user, organization):
    offer = make_sent_offer(db, test_user, FIXED, organization)
    accept_offer(offer, test_user)

    with pytest.raises(OfferStateError):
        accept_offer(offer, test_user)
    assert Project.query.count() == 1
    assert offer.status == OfferStatus.ACCEPTED
