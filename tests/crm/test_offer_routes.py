from decimal import Decimal

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
from app.crm.services import add_version, create_proposal, promote_to_lead, set_estimation
from app.projects.models import Customer, Project


def make_estimated_version(db, test_user, name="Client Cara", organization=None, **estimation_fields):
    person = Person(
        name=name,
        qualification_status=QualificationStatus.QUALIFIED,
        organization=organization,
    )
    db.session.add(person)
    db.session.commit()
    lead = promote_to_lead(person)
    proposal = create_proposal(lead, f"Project for {name}", "scope", test_user)
    version = proposal.versions[0]
    set_estimation(version, **estimation_fields)
    return version


def make_organization(db, name):
    org = Organization(name=name)
    db.session.add(org)
    db.session.commit()
    return org


# --- full lifecycle ------------------------------------------------------


def test_full_lifecycle_create_send_accept_hard_budget(logged_in_client, db, test_user):
    organization = make_organization(db, "Acme HARD Co")
    version = make_estimated_version(
        db,
        test_user,
        "Hard Client",
        organization,
        pricing_model=PricingModel.TIME_BASED,
        rate_amount=Decimal("120.00"),
        rate_unit=RateUnit.HOURLY,
        estimated_units=Decimal("80"),
        additional_rate=Decimal("150.00"),
    )

    create_resp = logged_in_client.post(
        f"/crm/proposal-versions/{version.id}/offer", headers={"HX-Request": "true"}
    )
    assert create_resp.status_code == 200
    offer = Offer.query.filter_by(proposal_version_id=version.id).one()
    assert create_resp.headers["HX-Redirect"] == f"/crm/offers/{offer.id}"

    send_resp = logged_in_client.post(
        f"/crm/offers/{offer.id}/send", headers={"HX-Request": "true"}
    )
    assert send_resp.status_code == 200
    db.session.expire_all()
    assert offer.status == OfferStatus.SENT
    assert b"Accept" in send_resp.data
    assert b"Deny" in send_resp.data

    accept_resp = logged_in_client.post(
        f"/crm/offers/{offer.id}/accept", headers={"HX-Request": "true"}
    )
    assert accept_resp.status_code == 200
    db.session.expire_all()
    project = Project.query.filter_by(offer_id=offer.id).one()
    assert accept_resp.headers["HX-Redirect"] == f"/projects/{project.id}"
    assert offer.status == OfferStatus.ACCEPTED
    assert version.proposal.lead.status == LeadStatus.WON

    project_resp = logged_in_client.get(f"/projects/{project.id}")
    assert project_resp.status_code == 200
    assert b"Hard" in project_resp.data
    assert b"80" in project_resp.data
    assert b"Bill At Rate" in project_resp.data
    assert b"150" in project_resp.data
    assert b"Acme HARD Co" in project_resp.data


def test_fixed_offer_project_page_shows_manual_note(logged_in_client, db, test_user):
    organization = make_organization(db, "Fixed Note Co")
    version = make_estimated_version(
        db,
        test_user,
        "Fixed Note Client",
        organization,
        pricing_model=PricingModel.FIXED,
        fixed_price=Decimal("4000.00"),
    )
    logged_in_client.post(f"/crm/proposal-versions/{version.id}/offer")
    offer = Offer.query.filter_by(proposal_version_id=version.id).one()
    logged_in_client.post(f"/crm/offers/{offer.id}/send")
    logged_in_client.post(f"/crm/offers/{offer.id}/accept")

    project = Project.query.filter_by(offer_id=offer.id).one()
    response = logged_in_client.get(f"/projects/{project.id}")

    assert response.status_code == 200
    assert b"set manually" in response.data
    assert b"Soft" in response.data


# --- deny path -------------------------------------------------------------


def test_deny_path_reverts_lead_badge(logged_in_client, db, test_user):
    organization = make_organization(db, "Deny Co")
    version = make_estimated_version(
        db,
        test_user,
        "Deny Client",
        organization,
        pricing_model=PricingModel.FIXED,
        fixed_price=Decimal("5000.00"),
    )
    lead = version.proposal.lead

    logged_in_client.post(f"/crm/proposal-versions/{version.id}/offer")
    offer = Offer.query.filter_by(proposal_version_id=version.id).one()
    logged_in_client.post(f"/crm/offers/{offer.id}/send")

    db.session.expire_all()
    assert lead.status == LeadStatus.OFFER_SENT
    lead_page = logged_in_client.get(f"/crm/leads/{lead.id}")
    assert b"Offer Sent" in lead_page.data

    deny_resp = logged_in_client.post(
        f"/crm/offers/{offer.id}/deny", headers={"HX-Request": "true"}
    )
    assert deny_resp.status_code == 200
    db.session.expire_all()
    assert lead.status == LeadStatus.PROPOSAL

    lead_page2 = logged_in_client.get(f"/crm/leads/{lead.id}")
    assert b"Proposal" in lead_page2.data


# --- missing organization ----------------------------------------------------


def test_accept_without_organization_shows_inline_prompt_and_persists_nothing(
    logged_in_client, db, test_user
):
    version = make_estimated_version(
        db,
        test_user,
        "No Org Client",
        organization=None,
        pricing_model=PricingModel.FIXED,
        fixed_price=Decimal("3000.00"),
    )
    logged_in_client.post(f"/crm/proposal-versions/{version.id}/offer")
    offer = Offer.query.filter_by(proposal_version_id=version.id).one()
    logged_in_client.post(f"/crm/offers/{offer.id}/send")

    response = logged_in_client.post(
        f"/crm/offers/{offer.id}/accept", headers={"HX-Request": "true"}
    )

    assert response.status_code == 200
    assert b"Assign an organization first" in response.data

    db.session.expire_all()
    assert offer.status == OfferStatus.SENT
    assert Customer.query.count() == 0
    assert Project.query.count() == 0


# --- locked estimation -------------------------------------------------------


def test_estimation_form_locked_after_send_shows_locked_state(logged_in_client, db, test_user):
    organization = make_organization(db, "Locked Co")
    version = make_estimated_version(
        db,
        test_user,
        "Locked Client",
        organization,
        pricing_model=PricingModel.FIXED,
        fixed_price=Decimal("2000.00"),
    )
    logged_in_client.post(f"/crm/proposal-versions/{version.id}/offer")
    offer = Offer.query.filter_by(proposal_version_id=version.id).one()
    logged_in_client.post(f"/crm/offers/{offer.id}/send")

    response = logged_in_client.get(
        f"/crm/proposals/{version.proposal_id}/versions/{version.version_number}",
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert b"Locked" in response.data
    assert b'name="pricing_model"' not in response.data


# --- create-offer button visibility -----------------------------------------


def test_create_offer_button_hidden_without_estimation(logged_in_client, db, test_user):
    person = Person(name="Bare Person", qualification_status=QualificationStatus.QUALIFIED)
    db.session.add(person)
    db.session.commit()
    lead = promote_to_lead(person)
    proposal = create_proposal(lead, "Bare Proposal", "content", test_user)

    response = logged_in_client.get(f"/crm/proposals/{proposal.id}")

    assert b"Create Offer" not in response.data
    assert b"Add an estimation" in response.data


def test_create_offer_button_hidden_when_lead_has_open_offer(logged_in_client, db, test_user):
    organization = make_organization(db, "Open Offer Co")
    version = make_estimated_version(
        db,
        test_user,
        "Open Offer Client",
        organization,
        pricing_model=PricingModel.FIXED,
        fixed_price=Decimal("1000.00"),
    )
    logged_in_client.post(f"/crm/proposal-versions/{version.id}/offer")

    proposal = version.proposal
    v2 = add_version(proposal, "v2 content", test_user)  # copy_estimation=True by default

    response = logged_in_client.get(
        f"/crm/proposals/{proposal.id}/versions/{v2.version_number}"
    )

    assert b"Create Offer" not in response.data
    assert b"already open" in response.data


# --- offer detail button visibility per status ------------------------------


def test_offer_detail_shows_send_when_draft(logged_in_client, db, test_user):
    organization = make_organization(db, "Draft Btn Co")
    version = make_estimated_version(
        db,
        test_user,
        "Draft Btn Client",
        organization,
        pricing_model=PricingModel.FIXED,
        fixed_price=Decimal("1000.00"),
    )
    logged_in_client.post(f"/crm/proposal-versions/{version.id}/offer")
    offer = Offer.query.filter_by(proposal_version_id=version.id).one()

    response = logged_in_client.get(f"/crm/offers/{offer.id}")

    assert b">Send<" in response.data
    assert b">Accept<" not in response.data
    assert b">Deny<" not in response.data


def test_offer_detail_shows_accept_deny_when_sent(logged_in_client, db, test_user):
    organization = make_organization(db, "Sent Btn Co")
    version = make_estimated_version(
        db,
        test_user,
        "Sent Btn Client",
        organization,
        pricing_model=PricingModel.FIXED,
        fixed_price=Decimal("1000.00"),
    )
    logged_in_client.post(f"/crm/proposal-versions/{version.id}/offer")
    offer = Offer.query.filter_by(proposal_version_id=version.id).one()
    logged_in_client.post(f"/crm/offers/{offer.id}/send")

    response = logged_in_client.get(f"/crm/offers/{offer.id}")

    assert b">Accept<" in response.data
    assert b">Deny<" in response.data
    assert b">Send<" not in response.data


def test_offer_detail_shows_no_buttons_when_terminal(logged_in_client, db, test_user):
    organization = make_organization(db, "Terminal Btn Co")
    version = make_estimated_version(
        db,
        test_user,
        "Terminal Btn Client",
        organization,
        pricing_model=PricingModel.FIXED,
        fixed_price=Decimal("1000.00"),
    )
    logged_in_client.post(f"/crm/proposal-versions/{version.id}/offer")
    offer = Offer.query.filter_by(proposal_version_id=version.id).one()
    logged_in_client.post(f"/crm/offers/{offer.id}/send")
    logged_in_client.post(f"/crm/offers/{offer.id}/deny")

    response = logged_in_client.get(f"/crm/offers/{offer.id}")

    assert b">Send<" not in response.data
    assert b">Accept<" not in response.data
    assert b">Deny<" not in response.data


# --- WON lead -> project link -------------------------------------------------


def test_won_lead_shows_project_link(logged_in_client, db, test_user):
    organization = make_organization(db, "Won Link Co")
    version = make_estimated_version(
        db,
        test_user,
        "Won Link Client",
        organization,
        pricing_model=PricingModel.FIXED,
        fixed_price=Decimal("1000.00"),
    )
    lead = version.proposal.lead
    logged_in_client.post(f"/crm/proposal-versions/{version.id}/offer")
    offer = Offer.query.filter_by(proposal_version_id=version.id).one()
    logged_in_client.post(f"/crm/offers/{offer.id}/send")
    logged_in_client.post(f"/crm/offers/{offer.id}/accept")

    project = Project.query.filter_by(offer_id=offer.id).one()

    response = logged_in_client.get(f"/crm/leads/{lead.id}")
    assert response.status_code == 200
    assert f"/projects/{project.id}".encode() in response.data
