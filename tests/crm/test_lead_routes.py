from app.crm.models import Lead, LeadStatus, Person, QualificationStatus
from app.crm.services import promote_to_lead


def make_person(db, qualification_status=QualificationStatus.QUALIFIED, name="Quinn Qualified"):
    person = Person(name=name, qualification_status=qualification_status)
    db.session.add(person)
    db.session.commit()
    return person


# --- promote -----------------------------------------------------------


def test_promote_end_to_end_via_route(logged_in_client, db):
    person = make_person(db)

    response = logged_in_client.post(
        f"/crm/people/{person.id}/promote", follow_redirects=True
    )

    assert response.status_code == 200
    lead = Lead.query.filter_by(person_id=person.id).one()
    assert lead.status == LeadStatus.OPEN
    assert person.lead.id == lead.id


def test_promote_via_htmx_sends_hx_redirect_to_lead_detail(logged_in_client, db):
    person = make_person(db)

    response = logged_in_client.post(
        f"/crm/people/{person.id}/promote", headers={"HX-Request": "true"}
    )

    assert response.status_code == 200
    lead = Lead.query.filter_by(person_id=person.id).one()
    assert response.headers["HX-Redirect"] == f"/crm/leads/{lead.id}"


def test_non_qualified_person_shows_no_promote_button(logged_in_client, db):
    person = make_person(db, qualification_status=QualificationStatus.NEW)

    response = logged_in_client.get(f"/crm/people/{person.id}")

    assert b"Promote to Lead" not in response.data


def test_qualified_person_without_lead_shows_promote_button(logged_in_client, db):
    person = make_person(db)

    response = logged_in_client.get(f"/crm/people/{person.id}")

    assert b"Promote to Lead" in response.data


def test_direct_post_for_non_qualified_person_rejected_cleanly(logged_in_client, db):
    person = make_person(db, qualification_status=QualificationStatus.NEW)

    response = logged_in_client.post(
        f"/crm/people/{person.id}/promote", follow_redirects=True
    )

    assert response.status_code == 200
    assert Lead.query.filter_by(person_id=person.id).count() == 0


def test_direct_post_htmx_for_non_qualified_person_renders_inline_error(logged_in_client, db):
    person = make_person(db, qualification_status=QualificationStatus.NEW)

    response = logged_in_client.post(
        f"/crm/people/{person.id}/promote", headers={"HX-Request": "true"}
    )

    assert response.status_code == 200
    assert b"not QUALIFIED" in response.data
    assert Lead.query.filter_by(person_id=person.id).count() == 0


def test_direct_post_for_already_promoted_person_rejected_cleanly(logged_in_client, db):
    person = make_person(db)
    promote_to_lead(person)

    response = logged_in_client.post(
        f"/crm/people/{person.id}/promote", follow_redirects=True
    )

    assert response.status_code == 200
    assert Lead.query.filter_by(person_id=person.id).count() == 1


# --- lead list / detail -------------------------------------------------


def test_lead_list_renders(logged_in_client, db):
    person = make_person(db)
    promote_to_lead(person)

    response = logged_in_client.get("/crm/leads")

    assert response.status_code == 200
    assert person.name.encode() in response.data


def test_lead_list_filters_by_status(logged_in_client, db):
    open_person = make_person(db, name="Open Ollie")
    lost_person = make_person(db, name="Lost Larry")
    promote_to_lead(open_person)
    lost_lead = promote_to_lead(lost_person)
    lost_lead.status = LeadStatus.LOST  # test setup only, not via app code
    db.session.commit()

    response = logged_in_client.get(
        "/crm/leads", query_string={"status": "OPEN"}, headers={"HX-Request": "true"}
    )

    assert b"Open Ollie" in response.data
    assert b"Lost Larry" not in response.data


def test_lead_detail_renders_person_summary_without_contact_affordance(logged_in_client, db):
    person = make_person(db)
    lead = promote_to_lead(person)

    response = logged_in_client.get(f"/crm/leads/{lead.id}")

    assert response.status_code == 200
    assert b"No contact permission" in response.data
    assert b'name="permission_to_contact"' not in response.data


def test_lead_detail_discovery_form_updates_fields(logged_in_client, db):
    person = make_person(db)
    lead = promote_to_lead(person)

    response = logged_in_client.post(
        f"/crm/leads/{lead.id}",
        data={
            "source": "Referral",
            "timeline": "Q3",
            "budget_range": "10-20k",
            "pain_points": "Too much manual work",
            "discovery_notes": "Follow up next week",
        },
        follow_redirects=True,
    )

    assert response.status_code == 200
    db.session.expire_all()
    updated = db.session.get(Lead, lead.id)
    assert updated.source == "Referral"
    assert updated.timeline == "Q3"
    assert updated.budget_range == "10-20k"
    assert updated.pain_points == "Too much manual work"
    assert updated.discovery_notes == "Follow up next week"


# --- close ---------------------------------------------------------------


def test_close_lead_end_to_end_via_route(logged_in_client, db):
    person = make_person(db)
    lead = promote_to_lead(person)

    response = logged_in_client.post(f"/crm/leads/{lead.id}/close", follow_redirects=True)

    assert response.status_code == 200
    db.session.expire_all()
    assert db.session.get(Lead, lead.id).status == LeadStatus.LOST


def test_close_button_hidden_on_terminal_state(logged_in_client, db):
    person = make_person(db)
    lead = promote_to_lead(person)
    lead.status = LeadStatus.WON  # test setup only, not via app code
    db.session.commit()

    response = logged_in_client.get(f"/crm/leads/{lead.id}")

    assert b"Close Lead" not in response.data


def test_close_on_terminal_state_rejected_cleanly(logged_in_client, db):
    person = make_person(db)
    lead = promote_to_lead(person)
    lead.status = LeadStatus.WON  # test setup only, not via app code
    db.session.commit()

    response = logged_in_client.post(f"/crm/leads/{lead.id}/close", follow_redirects=True)

    assert response.status_code == 200
    db.session.expire_all()
    assert db.session.get(Lead, lead.id).status == LeadStatus.WON
