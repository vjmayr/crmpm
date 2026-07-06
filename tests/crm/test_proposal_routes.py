from decimal import Decimal

from app.crm.models import (
    Estimation,
    Lead,
    LeadStatus,
    Person,
    PricingModel,
    Proposal,
    QualificationStatus,
)
from app.crm.services import promote_to_lead, set_estimation


def make_lead(db, name="Prospect Pat"):
    person = Person(name=name, qualification_status=QualificationStatus.QUALIFIED)
    db.session.add(person)
    db.session.commit()
    return promote_to_lead(person)


# --- create proposal ---------------------------------------------------


def test_create_proposal_end_to_end_and_lead_status_badge_updates(logged_in_client, db):
    lead = make_lead(db)
    assert lead.status == LeadStatus.OPEN

    response = logged_in_client.post(
        f"/crm/leads/{lead.id}/proposals/new",
        data={"title": "Website revamp", "content": "the pitch"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    db.session.expire_all()
    assert db.session.get(Lead, lead.id).status == LeadStatus.PROPOSAL

    lead_page = logged_in_client.get(f"/crm/leads/{lead.id}")
    assert b"Proposal" in lead_page.data
    assert b"Website revamp" in lead_page.data


def test_create_proposal_via_htmx_sends_hx_redirect(logged_in_client, db):
    lead = make_lead(db)

    response = logged_in_client.post(
        f"/crm/leads/{lead.id}/proposals/new",
        data={"title": "Website revamp", "content": "the pitch"},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    proposal = Proposal.query.filter_by(lead_id=lead.id).one()
    assert response.headers["HX-Redirect"] == f"/crm/proposals/{proposal.id}"


def test_create_proposal_on_terminal_lead_surfaces_inline_error(logged_in_client, db):
    lead = make_lead(db)
    lead.status = LeadStatus.LOST  # test setup only
    db.session.commit()

    response = logged_in_client.post(
        f"/crm/leads/{lead.id}/proposals/new",
        data={"title": "Too late", "content": "content"},
    )

    assert response.status_code == 200
    assert b"terminal" in response.data
    assert Proposal.query.filter_by(lead_id=lead.id).count() == 0


def test_new_proposal_button_hidden_on_terminal_lead(logged_in_client, db):
    lead = make_lead(db)
    lead.status = LeadStatus.WON  # test setup only
    db.session.commit()

    response = logged_in_client.get(f"/crm/leads/{lead.id}")

    assert b"New Proposal" not in response.data


# --- version history / detail -------------------------------------------


def test_proposal_detail_shows_latest_version(logged_in_client, db):
    lead = make_lead(db)
    logged_in_client.post(
        f"/crm/leads/{lead.id}/proposals/new",
        data={"title": "Website revamp", "content": "v1 content here"},
    )
    proposal = Proposal.query.filter_by(lead_id=lead.id).one()

    response = logged_in_client.get(f"/crm/proposals/{proposal.id}")

    assert response.status_code == 200
    assert b"v1 content here" in response.data
    assert b"Version 1" in response.data


def test_old_version_marked_read_only_and_reachable(logged_in_client, db):
    lead = make_lead(db)
    logged_in_client.post(
        f"/crm/leads/{lead.id}/proposals/new",
        data={"title": "Website revamp", "content": "v1 content"},
    )
    proposal = Proposal.query.filter_by(lead_id=lead.id).one()
    logged_in_client.post(
        f"/crm/proposals/{proposal.id}/revise",
        query_string={"from": 1},
        data={"content": "v2 content", "copy_estimation": ""},
    )

    response = logged_in_client.get(
        f"/crm/proposals/{proposal.id}/versions/1", headers={"HX-Request": "true"}
    )

    assert response.status_code == 200
    assert b"v1 content" in response.data
    assert b"Read-only (historical)" in response.data


def test_latest_version_not_marked_read_only(logged_in_client, db):
    lead = make_lead(db)
    logged_in_client.post(
        f"/crm/leads/{lead.id}/proposals/new",
        data={"title": "Website revamp", "content": "v1 content"},
    )
    proposal = Proposal.query.filter_by(lead_id=lead.id).one()

    response = logged_in_client.get(
        f"/crm/proposals/{proposal.id}/versions/1", headers={"HX-Request": "true"}
    )

    assert b"Read-only (historical)" not in response.data


# --- revise ---------------------------------------------------------------


def test_revise_with_copy_estimation_copies_forward(logged_in_client, db):
    lead = make_lead(db)
    logged_in_client.post(
        f"/crm/leads/{lead.id}/proposals/new",
        data={"title": "Website revamp", "content": "v1 content"},
    )
    proposal = Proposal.query.filter_by(lead_id=lead.id).one()
    v1 = proposal.versions[0]
    set_estimation(v1, pricing_model=PricingModel.FIXED, fixed_price=Decimal("5000.00"))

    response = logged_in_client.post(
        f"/crm/proposals/{proposal.id}/revise",
        query_string={"from": 1},
        data={"content": "v2 content", "copy_estimation": "y"},
        follow_redirects=True,
    )

    assert response.status_code == 200
    db.session.expire_all()
    proposal = db.session.get(Proposal, proposal.id)
    v2 = proposal.versions[1]
    assert v2.estimation is not None
    assert v2.estimation.id != v1.estimation.id
    assert v2.estimation.fixed_price == Decimal("5000.00")


def test_revise_without_copy_estimation_yields_bare_version(logged_in_client, db):
    lead = make_lead(db)
    logged_in_client.post(
        f"/crm/leads/{lead.id}/proposals/new",
        data={"title": "Website revamp", "content": "v1 content"},
    )
    proposal = Proposal.query.filter_by(lead_id=lead.id).one()
    set_estimation(proposal.versions[0], pricing_model=PricingModel.FIXED, fixed_price=Decimal("5000.00"))

    response = logged_in_client.post(
        f"/crm/proposals/{proposal.id}/revise",
        query_string={"from": 1},
        data={"content": "v2 content"},  # copy_estimation checkbox omitted = unchecked
        follow_redirects=True,
    )

    assert response.status_code == 200
    db.session.expire_all()
    proposal = db.session.get(Proposal, proposal.id)
    v2 = proposal.versions[1]
    assert v2.estimation is None


def test_revise_via_htmx_sends_hx_redirect_to_new_version(logged_in_client, db):
    lead = make_lead(db)
    logged_in_client.post(
        f"/crm/leads/{lead.id}/proposals/new",
        data={"title": "Website revamp", "content": "v1 content"},
    )
    proposal = Proposal.query.filter_by(lead_id=lead.id).one()

    response = logged_in_client.post(
        f"/crm/proposals/{proposal.id}/revise",
        data={"content": "v2 content", "copy_estimation": ""},
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == f"/crm/proposals/{proposal.id}/versions/2"


def test_revise_on_terminal_lead_surfaces_inline_error(logged_in_client, db):
    lead = make_lead(db)
    logged_in_client.post(
        f"/crm/leads/{lead.id}/proposals/new",
        data={"title": "Website revamp", "content": "v1 content"},
    )
    proposal = Proposal.query.filter_by(lead_id=lead.id).one()
    lead.status = LeadStatus.LOST  # test setup only
    db.session.commit()

    response = logged_in_client.post(
        f"/crm/proposals/{proposal.id}/revise",
        data={"content": "v2 content", "copy_estimation": ""},
    )

    assert response.status_code == 200
    assert b"terminal" in response.data
    assert len(db.session.get(Proposal, proposal.id).versions) == 1


def test_revise_link_hidden_on_terminal_lead(logged_in_client, db):
    lead = make_lead(db)
    logged_in_client.post(
        f"/crm/leads/{lead.id}/proposals/new",
        data={"title": "Website revamp", "content": "v1 content"},
    )
    proposal = Proposal.query.filter_by(lead_id=lead.id).one()
    lead.status = LeadStatus.WON  # test setup only
    db.session.commit()

    response = logged_in_client.get(f"/crm/proposals/{proposal.id}")

    assert b"Revise from this version" not in response.data


# --- estimation form -------------------------------------------------------


def test_set_estimation_fixed_model(logged_in_client, db):
    lead = make_lead(db)
    logged_in_client.post(
        f"/crm/leads/{lead.id}/proposals/new",
        data={"title": "Website revamp", "content": "v1 content"},
    )
    proposal = Proposal.query.filter_by(lead_id=lead.id).one()
    version = proposal.versions[0]

    response = logged_in_client.post(
        f"/crm/proposal-versions/{version.id}/estimation",
        data={
            "pricing_model": "FIXED",
            "fixed_price": "5000.00",
            "rate_amount": "",
            "rate_unit": "",
            "estimated_units": "",
            "additional_rate": "",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    estimation = Estimation.query.filter_by(proposal_version_id=version.id).one()
    assert estimation.fixed_price == Decimal("5000.00")


def test_set_estimation_time_based_model(logged_in_client, db):
    lead = make_lead(db)
    logged_in_client.post(
        f"/crm/leads/{lead.id}/proposals/new",
        data={"title": "Website revamp", "content": "v1 content"},
    )
    proposal = Proposal.query.filter_by(lead_id=lead.id).one()
    version = proposal.versions[0]

    response = logged_in_client.post(
        f"/crm/proposal-versions/{version.id}/estimation",
        data={
            "pricing_model": "TIME_BASED",
            "fixed_price": "",
            "rate_amount": "120.00",
            "rate_unit": "HOURLY",
            "estimated_units": "80",
            "additional_rate": "",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    estimation = Estimation.query.filter_by(proposal_version_id=version.id).one()
    assert estimation.rate_amount == Decimal("120.00")
    assert estimation.estimated_units == Decimal("80")


def test_set_estimation_validation_error_surfaces_inline(logged_in_client, db):
    lead = make_lead(db)
    logged_in_client.post(
        f"/crm/leads/{lead.id}/proposals/new",
        data={"title": "Website revamp", "content": "v1 content"},
    )
    proposal = Proposal.query.filter_by(lead_id=lead.id).one()
    version = proposal.versions[0]

    response = logged_in_client.post(
        f"/crm/proposal-versions/{version.id}/estimation",
        data={
            "pricing_model": "FIXED",
            "fixed_price": "",
            "rate_amount": "",
            "rate_unit": "",
            "estimated_units": "",
            "additional_rate": "",
        },
        headers={"HX-Request": "true"},
    )

    assert response.status_code == 200
    assert b"fixed_price" in response.data
    assert Estimation.query.filter_by(proposal_version_id=version.id).count() == 0


def test_estimation_fields_partial_shows_time_based_fields(logged_in_client, db):
    lead = make_lead(db)
    logged_in_client.post(
        f"/crm/leads/{lead.id}/proposals/new",
        data={"title": "Website revamp", "content": "v1 content"},
    )
    proposal = Proposal.query.filter_by(lead_id=lead.id).one()
    version = proposal.versions[0]

    response = logged_in_client.get(
        f"/crm/proposal-versions/{version.id}/estimation-fields",
        query_string={"pricing_model": "TIME_BASED"},
    )

    assert response.status_code == 200
    assert b"rate_amount" in response.data
    assert b"fixed_price" not in response.data
