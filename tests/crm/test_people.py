from app.crm.models import Person, QualificationStatus


def test_person_list_renders(logged_in_client):
    response = logged_in_client.get("/crm/people")
    assert response.status_code == 200


def test_create_person_without_touching_toggle_defaults_false(logged_in_client, db):
    response = logged_in_client.post(
        "/crm/people/new",
        data={
            "name": "Grace Hopper",
            "email": "",
            "phone": "",
            "organization_id": "",
            "qualification_status": "NEW",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    person = Person.query.filter_by(name="Grace Hopper").one()
    assert person.permission_to_contact is False


def test_create_person_with_toggle_checked(logged_in_client, db):
    response = logged_in_client.post(
        "/crm/people/new",
        data={
            "name": "Ada Lovelace",
            "permission_to_contact": "y",
            "email": "ada@example.com",
            "phone": "",
            "organization_id": "",
            "qualification_status": "NEW",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    person = Person.query.filter_by(name="Ada Lovelace").one()
    assert person.permission_to_contact is True


def test_edit_person(logged_in_client, db):
    person = Person(name="Old Name")
    db.session.add(person)
    db.session.commit()

    response = logged_in_client.post(
        f"/crm/people/{person.id}/edit",
        data={
            "name": "New Name",
            "email": "",
            "phone": "",
            "organization_id": "",
            "qualification_status": "CONTACTED",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    updated = db.session.get(Person, person.id)
    assert updated.name == "New Name"
    assert updated.qualification_status == QualificationStatus.CONTACTED


def test_inline_qualification_update(logged_in_client, db):
    person = Person(name="Inline Edit")
    db.session.add(person)
    db.session.commit()

    response = logged_in_client.post(
        f"/crm/people/{person.id}/qualification",
        data={"qualification_status": "QUALIFIED"},
    )
    assert response.status_code == 200
    assert db.session.get(Person, person.id).qualification_status == QualificationStatus.QUALIFIED


def test_contactable_only_filter_excludes_flagged_off(logged_in_client, db):
    db.session.add(Person(name="Contactable Carl", permission_to_contact=True))
    db.session.add(Person(name="Silent Sam", permission_to_contact=False))
    db.session.commit()

    response = logged_in_client.get(
        "/crm/people", query_string={"contactable_only": "1"}, headers={"HX-Request": "true"}
    )
    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Contactable Carl" in body
    assert "Silent Sam" not in body


def test_invalid_email_is_rejected(logged_in_client, db):
    response = logged_in_client.post(
        "/crm/people/new",
        data={
            "name": "Bad Email",
            "email": "not-an-email",
            "phone": "",
            "organization_id": "",
            "qualification_status": "NEW",
        },
    )
    assert response.status_code == 200
    assert Person.query.filter_by(name="Bad Email").count() == 0
