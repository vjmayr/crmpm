import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError

from app.crm.exceptions import LeadStateError, PromotionError
from app.crm.models import Lead, LeadStatus, Person, QualificationStatus
from app.crm.services import close_lead, promote_to_lead


@pytest.fixture()
def qualified_person(db):
    person = Person(
        name="Qualified Quinn", qualification_status=QualificationStatus.QUALIFIED
    )
    db.session.add(person)
    db.session.commit()
    return person


# --- promote_to_lead --------------------------------------------------------


def test_promoting_qualified_person_creates_open_lead(db, qualified_person):
    lead = promote_to_lead(qualified_person)

    assert lead.id is not None
    assert lead.person_id == qualified_person.id
    assert lead.status == LeadStatus.OPEN


@pytest.mark.parametrize(
    "status",
    [s for s in QualificationStatus if s != QualificationStatus.QUALIFIED],
    ids=lambda s: s.value,
)
def test_promoting_non_qualified_person_raises(db, status):
    person = Person(name=f"Not Ready {status.value}", qualification_status=status)
    db.session.add(person)
    db.session.commit()

    with pytest.raises(PromotionError):
        promote_to_lead(person)
    assert Lead.query.count() == 0


def test_promoting_twice_raises_readable_service_error(db, qualified_person):
    promote_to_lead(qualified_person)

    with pytest.raises(PromotionError):
        promote_to_lead(qualified_person)
    assert Lead.query.count() == 1


def test_db_unique_constraint_independently_rejects_second_lead(db, qualified_person):
    # Bypass the service on purpose: the constraint is the backstop layer.
    promote_to_lead(qualified_person)
    db.session.add(Lead(person_id=qualified_person.id))

    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


@pytest.mark.parametrize("flag", [True, False], ids=["opt-in", "no-permission"])
def test_promotion_works_regardless_of_permission_to_contact(db, flag):
    # Design note (DECISIONS.md): the flag governs contact affordances,
    # not pipeline membership — it is deliberately not a promotion guard.
    person = Person(
        name=f"Permission {flag}",
        qualification_status=QualificationStatus.QUALIFIED,
        permission_to_contact=flag,
    )
    db.session.add(person)
    db.session.commit()

    lead = promote_to_lead(person)
    assert lead.status == LeadStatus.OPEN


# --- close_lead -------------------------------------------------------------


def test_close_lead_sets_lost_from_open(db, qualified_person):
    lead = promote_to_lead(qualified_person)

    closed = close_lead(lead)

    assert closed.status == LeadStatus.LOST
    db.session.expire_all()
    assert db.session.get(Lead, lead.id).status == LeadStatus.LOST


@pytest.mark.parametrize(
    "terminal", [LeadStatus.WON, LeadStatus.LOST], ids=lambda s: s.value
)
def test_close_lead_raises_from_terminal_states(db, qualified_person, terminal):
    lead = promote_to_lead(qualified_person)
    # Test setup only — app code must never assign status directly (CLAUDE.md #7).
    lead.status = terminal
    db.session.commit()

    with pytest.raises(LeadStateError):
        close_lead(lead)
    assert lead.status == terminal


# --- migration chain --------------------------------------------------------


def test_migration_upgrade_and_downgrade_run_cleanly(tmp_path):
    db_path = tmp_path / "migration_check.db"
    env = {
        **os.environ,
        "FLASK_APP": "wsgi.py",
        "FLASK_CONFIG": "development",
        "DATABASE_URL": f"sqlite:///{db_path}",
    }
    root = Path(__file__).resolve().parents[2]

    def run_db(*args):
        result = subprocess.run(
            [sys.executable, "-m", "flask", "db", *args],
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, result.stderr
        return result

    def table_names():
        with sqlite3.connect(db_path) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        return {row[0] for row in rows}

    domain_tables = {
        "leads",
        "proposals",
        "proposal_versions",
        "estimations",
        "offers",
        "customers",
        "projects",
        "sections",
        "work_packages",
        "tasks",
    }

    run_db("upgrade")
    assert domain_tables <= table_names()

    run_db("downgrade", "base")
    assert table_names() <= {"alembic_version"}

    run_db("upgrade")
    assert domain_tables <= table_names()
