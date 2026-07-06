import re
from decimal import Decimal

import pytest
from sqlalchemy import event

from app.crm.models import Organization, Person, PricingModel, QualificationStatus, RateUnit
from app.crm.services import (
    close_lead,
    create_offer,
    create_proposal,
    promote_to_lead,
    send_offer,
    set_estimation,
)
from app.projects.services import create_section, create_task, create_work_package
from app.services.offer_acceptance import accept_offer


def make_person(db, name, qualification=QualificationStatus.QUALIFIED, organization=None, contactable=False):
    person = Person(
        name=name,
        qualification_status=qualification,
        organization=organization,
        permission_to_contact=contactable,
    )
    db.session.add(person)
    db.session.commit()
    return person


@pytest.fixture()
def seeded_pipeline(db, test_user):
    """One realistic fixture spanning both domains:

    People: 1 NEW, 1 CONTACTED (contactable), 1 QUALIFIED unpromoted.
    Leads: 2 OPEN, 1 PROPOSAL, 1 OFFER_SENT (the in-flight offer),
    1 WON (accepted offer -> HARD project with structure), 1 LOST.
    """
    make_person(db, "Newbie Nora", qualification=QualificationStatus.NEW)
    make_person(db, "Contact Carl", qualification=QualificationStatus.CONTACTED, contactable=True)
    make_person(db, "Candidate Kim")  # QUALIFIED, never promoted

    promote_to_lead(make_person(db, "Open Oliver"))
    promote_to_lead(make_person(db, "Open Owen"))

    proposal_lead = promote_to_lead(make_person(db, "Proposal Petra"))
    create_proposal(proposal_lead, "Petra Website", "scope", test_user)

    sent_org = Organization(name="Sent GmbH")
    db.session.add(sent_org)
    db.session.commit()
    sent_lead = promote_to_lead(make_person(db, "Sent Sam", organization=sent_org))
    sent_proposal = create_proposal(sent_lead, "Sam Platform", "scope", test_user)
    set_estimation(
        sent_proposal.versions[0],
        pricing_model=PricingModel.FIXED,
        fixed_price=Decimal("8000.00"),
    )
    send_offer(create_offer(sent_proposal.versions[0]))

    won_org = Organization(name="Won AG")
    db.session.add(won_org)
    db.session.commit()
    won_lead = promote_to_lead(make_person(db, "Won Wanda", organization=won_org))
    won_proposal = create_proposal(won_lead, "Wanda Rollout", "scope", test_user)
    set_estimation(
        won_proposal.versions[0],
        pricing_model=PricingModel.TIME_BASED,
        rate_amount=Decimal("100.00"),
        rate_unit=RateUnit.HOURLY,
        estimated_units=Decimal("80"),
        additional_rate=Decimal("120.00"),
    )
    won_offer = create_offer(won_proposal.versions[0])
    send_offer(won_offer)
    project = accept_offer(won_offer, test_user)
    section = create_section(project, "Delivery")
    wp = create_work_package(section, "Phase One", estimated_hours=Decimal("30"))
    create_task(wp, "Kickoff", estimated_hours=Decimal("5"))

    lost_lead = promote_to_lead(make_person(db, "Lost Lars"))
    close_lead(lost_lead)

    return {"project": project}


def tile_count(body, label):
    match = re.search(r"(\d+)</p>\s*<p class=\"text-xs text-gray-500\">" + label + "</p>", body)
    assert match, f"tile {label!r} not found"
    return int(match.group(1))


def column_slice(body, status_value):
    """The HTML between this column's marker and the next column (or end)."""
    marker = f'id="col-{status_value}"'
    assert marker in body, f"column {status_value} not rendered"
    start = body.index(marker)
    later_markers = [
        match.start()
        for match in re.finditer(r'id="col-\w+"', body)
        if match.start() > start
    ]
    end = min(later_markers) if later_markers else len(body)
    return body[start:end]


# --- dashboard -----------------------------------------------------------


def test_dashboard_funnel_counts(logged_in_client, seeded_pipeline):
    body = logged_in_client.get("/").data.decode()

    assert tile_count(body, "Open") == 2
    assert tile_count(body, "Proposal") == 1
    assert tile_count(body, "Offer Sent") == 1
    assert tile_count(body, "Won") == 1
    assert tile_count(body, "Lost") == 1


def test_dashboard_crm_stats(logged_in_client, seeded_pipeline):
    body = logged_in_client.get("/").data.decode()

    # 3 non-lead people + 6 promoted persons = 9.
    assert tile_count(body, "People") == 9
    assert tile_count(body, "Contactable") == 1
    assert tile_count(body, "Qualified, not yet promoted") == 1


def test_dashboard_offers_in_flight(logged_in_client, seeded_pipeline):
    body = logged_in_client.get("/").data.decode()

    assert "Sent Sam" in body
    assert "Sent GmbH" in body
    assert "Sam Platform" in body


def test_dashboard_portfolio_strip_shows_active_project_health(logged_in_client, seeded_pipeline):
    body = logged_in_client.get("/").data.decode()

    assert "Wanda Rollout" in body
    # HARD 80h budget vs max(WP 30, tasks 5) -> within.
    assert "Within budget" in body
    assert "30.00h of 80.00h" in body


def test_dashboard_empty_states(logged_in_client, db):
    body = logged_in_client.get("/").data.decode()

    assert "No offers out right now" in body
    assert "No active projects" in body


# --- pipeline board --------------------------------------------------------


def test_pipeline_columns_place_leads_correctly(logged_in_client, seeded_pipeline):
    body = logged_in_client.get("/crm/pipeline?show_closed=1").data.decode()

    assert "Open Oliver" in column_slice(body, "OPEN")
    assert "Open Owen" in column_slice(body, "OPEN")
    assert "Proposal Petra" in column_slice(body, "PROPOSAL")
    assert "Sent Sam" in column_slice(body, "OFFER_SENT")
    assert "Won Wanda" in column_slice(body, "WON")
    assert "Lost Lars" in column_slice(body, "LOST")


def test_pipeline_cards_carry_proposal_title_offer_badge_and_age(logged_in_client, seeded_pipeline):
    body = logged_in_client.get("/crm/pipeline?show_closed=1").data.decode()

    proposal_col = column_slice(body, "PROPOSAL")
    assert "Petra Website" in proposal_col

    sent_col = column_slice(body, "OFFER_SENT")
    assert "Offer Sent</span>" in sent_col  # open-offer badge
    assert "0d" in sent_col  # age from created_at


def test_pipeline_hides_closed_columns_by_default(logged_in_client, seeded_pipeline):
    body = logged_in_client.get("/crm/pipeline").data.decode()

    assert 'id="col-OPEN"' in body
    assert 'id="col-WON"' not in body
    assert 'id="col-LOST"' not in body


def test_pipeline_status_filter_shows_single_column(logged_in_client, seeded_pipeline):
    body = logged_in_client.get("/crm/pipeline?status=WON").data.decode()

    assert 'id="col-WON"' in body
    assert 'id="col-OPEN"' not in body
    assert "Show all columns" in body


def test_pipeline_board_is_read_only(logged_in_client, seeded_pipeline):
    body = logged_in_client.get("/crm/pipeline?show_closed=1").data.decode()

    # A window, not a control surface: no forms, no POSTs, no drag handles.
    assert "<form" not in body
    assert "hx-post" not in body
    assert "draggable" not in body


def test_pipeline_query_count_is_constant(logged_in_client, db, seeded_pipeline):
    counter = {"n": 0}

    def count_query(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    engine = db.session.get_bind()
    event.listen(engine, "before_cursor_execute", count_query)
    try:
        logged_in_client.get("/crm/pipeline?show_closed=1")
    finally:
        event.remove(engine, "before_cursor_execute", count_query)

    # 1 session-user load + 3 board queries (leads, proposal titles, open
    # offers) — never one per card. Six leads under N+1 would need 10+.
    assert counter["n"] <= 5


def test_pipeline_empty_state(logged_in_client, db):
    body = logged_in_client.get("/crm/pipeline").data.decode()

    assert "Nothing here." in body
    assert "Promote a qualified person" in body
