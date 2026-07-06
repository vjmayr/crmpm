import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from app.crm.models import Organization, Person, QualificationStatus


def test_permission_to_contact_defaults_false_without_specifying(db):
    person = Person(name="Jane Doe")
    db.session.add(person)
    db.session.commit()

    db.session.expire_all()
    fetched = Person.query.filter_by(name="Jane Doe").one()
    assert fetched.permission_to_contact is False


def test_permission_to_contact_defaults_false_at_database_level(db):
    # Insert via raw SQL, bypassing the ORM's Python-side default entirely,
    # to prove the column's server_default enforces the opt-in — not just app code.
    db.session.execute(
        text(
            "INSERT INTO people (name, qualification_status, created_at) "
            "VALUES (:name, :qs, :created_at)"
        ),
        {"name": "Raw SQL Person", "qs": "NEW", "created_at": "2026-01-01 00:00:00"},
    )
    db.session.commit()

    fetched = Person.query.filter_by(name="Raw SQL Person").one()
    assert fetched.permission_to_contact is False


def test_qualification_status_defaults_to_new(db):
    person = Person(name="Default Status")
    db.session.add(person)
    db.session.commit()
    assert person.qualification_status == QualificationStatus.NEW


def test_contactable_excludes_flag_false_persons(db):
    db.session.add(Person(name="Yes Contact", permission_to_contact=True))
    db.session.add(Person(name="No Contact", permission_to_contact=False))
    db.session.commit()

    names = {p.name for p in Person.contactable().all()}
    assert "Yes Contact" in names
    assert "No Contact" not in names


def test_organization_duplicate_name_raises_integrity_error(db):
    db.session.add(Organization(name="Acme"))
    db.session.commit()

    db.session.add(Organization(name="Acme"))
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_person_organization_relationship_round_trip(db):
    org = Organization(name="Globex")
    person = Person(name="Homer", organization=org)
    db.session.add(person)
    db.session.commit()

    db.session.expire_all()
    fetched_person = Person.query.filter_by(name="Homer").one()
    assert fetched_person.organization.name == "Globex"

    fetched_org = Organization.query.filter_by(name="Globex").one()
    assert fetched_org.people[0].name == "Homer"
