from decimal import Decimal

import pytest

from app.crm import services
from app.crm.exceptions import (
    EstimationLockedError,
    OfferConflictError,
    OfferStateError,
)
from app.crm.models import (
    LeadStatus,
    Offer,
    OfferStatus,
    Person,
    PricingModel,
    QualificationStatus,
    RateUnit,
)
from app.crm.services import (
    close_lead,
    create_offer,
    create_proposal,
    deny_offer,
    promote_to_lead,
    send_offer,
    set_estimation,
)

TIME_FIELDS = {
    "pricing_model": PricingModel.TIME_BASED,
    "rate_amount": Decimal("120.00"),
    "rate_unit": RateUnit.HOURLY,
    "estimated_units": Decimal("80"),
}


@pytest.fixture()
def lead(db):
    person = Person(
        name="Offer Prospect", qualification_status=QualificationStatus.QUALIFIED
    )
    db.session.add(person)
    db.session.commit()
    return promote_to_lead(person)


@pytest.fixture()
def proposal(db, lead, test_user):
    return create_proposal(lead, "Offer proposal", "v1 content", test_user)


@pytest.fixture()
def estimated_version(db, proposal):
    version = proposal.versions[0]
    set_estimation(version, **TIME_FIELDS)
    return version


# --- create_offer ------------------------------------------------------------


def test_create_offer_starts_draft_with_no_timestamps(db, estimated_version):
    offer = create_offer(estimated_version)

    assert offer.id is not None
    assert offer.status == OfferStatus.DRAFT
    assert offer.sent_at is None
    assert offer.decided_at is None


@pytest.mark.parametrize(
    "terminal", [LeadStatus.WON, LeadStatus.LOST], ids=lambda s: s.value
)
def test_create_offer_on_terminal_lead_raises(db, lead, estimated_version, terminal):
    lead.status = terminal  # test setup only
    db.session.commit()

    with pytest.raises(OfferStateError):
        create_offer(estimated_version)
    assert Offer.query.count() == 0


def test_create_offer_conflicts_with_open_draft(db, estimated_version):
    create_offer(estimated_version)

    with pytest.raises(OfferConflictError):
        create_offer(estimated_version)
    assert Offer.query.count() == 1


def test_create_offer_conflicts_with_open_sent(db, estimated_version):
    offer = create_offer(estimated_version)
    send_offer(offer)

    with pytest.raises(OfferConflictError):
        create_offer(estimated_version)
    assert Offer.query.count() == 1


def test_create_offer_allowed_after_denied(db, estimated_version):
    first = create_offer(estimated_version)
    send_offer(first)
    deny_offer(first)

    second = create_offer(estimated_version)

    assert second.id != first.id
    assert second.status == OfferStatus.DRAFT
    assert Offer.query.count() == 2


# --- send_offer --------------------------------------------------------------


def test_send_offer_stamps_sent_at_and_moves_lead(db, lead, estimated_version):
    offer = create_offer(estimated_version)

    sent = send_offer(offer)

    assert sent.status == OfferStatus.SENT
    assert sent.sent_at is not None
    assert sent.decided_at is None
    assert lead.status == LeadStatus.OFFER_SENT


def test_send_offer_without_estimation_raises(db, proposal):
    # invariant #5: an Offer cannot reach SENT without an Estimation.
    bare_version = proposal.versions[0]
    offer = create_offer(bare_version)

    with pytest.raises(OfferStateError):
        send_offer(offer)
    assert offer.status == OfferStatus.DRAFT
    assert offer.sent_at is None


@pytest.mark.parametrize(
    "status",
    [OfferStatus.SENT, OfferStatus.ACCEPTED, OfferStatus.DENIED],
    ids=lambda s: s.value,
)
def test_send_offer_invalid_from(db, estimated_version, status):
    offer = create_offer(estimated_version)
    offer.status = status  # test setup only — app code goes through services
    db.session.commit()

    with pytest.raises(OfferStateError):
        send_offer(offer)
    assert offer.status == status


# --- deny_offer --------------------------------------------------------------


def test_deny_offer_stamps_decided_and_reverts_lead_to_proposal(db, lead, estimated_version):
    offer = create_offer(estimated_version)
    send_offer(offer)
    assert lead.status == LeadStatus.OFFER_SENT

    denied = deny_offer(offer)

    assert denied.status == OfferStatus.DENIED
    assert denied.decided_at is not None
    assert lead.status == LeadStatus.PROPOSAL


@pytest.mark.parametrize(
    "status",
    [OfferStatus.DRAFT, OfferStatus.ACCEPTED, OfferStatus.DENIED],
    ids=lambda s: s.value,
)
def test_deny_offer_invalid_from(db, estimated_version, status):
    offer = create_offer(estimated_version)
    offer.status = status  # test setup only
    db.session.commit()

    with pytest.raises(OfferStateError):
        deny_offer(offer)
    assert offer.status == status


def test_deny_offer_never_resurrects_lost_lead(db, lead, estimated_version):
    # Lead can be closed (LOST) while an offer is out — OFFER_SENT is not terminal.
    offer = create_offer(estimated_version)
    send_offer(offer)
    close_lead(lead)
    assert lead.status == LeadStatus.LOST

    deny_offer(offer)

    assert offer.status == OfferStatus.DENIED
    assert lead.status == LeadStatus.LOST  # invariant #10: deny never touches LOST


# --- invariant #6: estimation lock ------------------------------------------


def test_estimation_unlocked_under_draft_offer(db, estimated_version):
    create_offer(estimated_version)

    estimation = set_estimation(estimated_version, rate_amount=Decimal("150.00"))

    assert estimation.rate_amount == Decimal("150.00")


def test_estimation_locked_after_send(db, estimated_version):
    offer = create_offer(estimated_version)
    send_offer(offer)

    with pytest.raises(EstimationLockedError):
        set_estimation(estimated_version, rate_amount=Decimal("150.00"))
    assert estimated_version.estimation.rate_amount == Decimal("120.00")


def test_estimation_stays_locked_after_deny(db, estimated_version):
    # A DENIED offer is a historical record; its version's pricing stays frozen.
    offer = create_offer(estimated_version)
    send_offer(offer)
    deny_offer(offer)

    with pytest.raises(EstimationLockedError):
        set_estimation(estimated_version, rate_amount=Decimal("150.00"))


def test_estimation_is_locked_reflects_offer_history(db, estimated_version):
    estimation = estimated_version.estimation
    assert services.estimation_is_locked(estimation) is False

    offer = create_offer(estimated_version)
    assert services.estimation_is_locked(estimation) is False  # DRAFT does not lock

    send_offer(offer)
    assert services.estimation_is_locked(estimation) is True

    deny_offer(offer)
    assert services.estimation_is_locked(estimation) is True  # locked forever
