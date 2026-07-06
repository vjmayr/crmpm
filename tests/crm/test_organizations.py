from app.crm.models import Organization, Person


def test_organization_list_renders(logged_in_client):
    response = logged_in_client.get("/crm/organizations")
    assert response.status_code == 200


def test_create_organization(logged_in_client, db):
    response = logged_in_client.post(
        "/crm/organizations/new",
        data={"name": "Acme Corp", "website": "", "notes": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert Organization.query.filter_by(name="Acme Corp").count() == 1


def test_edit_organization(logged_in_client, db):
    org = Organization(name="Old Name")
    db.session.add(org)
    db.session.commit()

    response = logged_in_client.post(
        f"/crm/organizations/{org.id}/edit",
        data={"name": "New Name", "website": "", "notes": ""},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert db.session.get(Organization, org.id).name == "New Name"


def test_delete_blocked_when_people_linked(logged_in_client, db):
    org = Organization(name="Has People")
    person = Person(name="Someone", organization=org)
    db.session.add_all([org, person])
    db.session.commit()
    org_id = org.id

    response = logged_in_client.post(f"/crm/organizations/{org_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert db.session.get(Organization, org_id) is not None


def test_delete_succeeds_without_people(logged_in_client, db):
    org = Organization(name="No People")
    db.session.add(org)
    db.session.commit()
    org_id = org.id

    response = logged_in_client.post(f"/crm/organizations/{org_id}/delete", follow_redirects=True)
    assert response.status_code == 200
    assert db.session.get(Organization, org_id) is None
