from decimal import Decimal

import pytest
from sqlalchemy.exc import IntegrityError

from app.crm.models import Organization
from app.projects.models import (
    BudgetType,
    Customer,
    CustomerType,
    OverHoursPolicy,
    Project,
)


@pytest.fixture()
def customer(db):
    org = Organization(name="Check Constraint Org")
    db.session.add(org)
    db.session.flush()
    customer = Customer(organization_id=org.id, type=CustomerType.EXTERNAL)
    db.session.add(customer)
    db.session.commit()
    return customer


def test_hard_budget_without_hours_rejected_at_db_level(db, customer, test_user):
    db.session.add(
        Project(
            name="Bad Hard Project",
            customer_id=customer.id,
            manager_id=test_user.id,
            budget_type=BudgetType.HARD,
            budget_hours=None,
            over_hours_policy=OverHoursPolicy.BLOCK,
        )
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_hard_budget_without_policy_rejected_at_db_level(db, customer, test_user):
    db.session.add(
        Project(
            name="Bad Hard Project",
            customer_id=customer.id,
            manager_id=test_user.id,
            budget_type=BudgetType.HARD,
            budget_hours=Decimal("80"),
            over_hours_policy=None,
        )
    )
    with pytest.raises(IntegrityError):
        db.session.commit()
    db.session.rollback()


def test_hard_budget_with_required_fields_accepted(db, customer, test_user):
    project = Project(
        name="Good Hard Project",
        customer_id=customer.id,
        manager_id=test_user.id,
        budget_type=BudgetType.HARD,
        budget_hours=Decimal("80"),
        over_hours_policy=OverHoursPolicy.BLOCK,
    )
    db.session.add(project)
    db.session.commit()
    assert project.id is not None


def test_soft_budget_allows_null_hours(db, customer, test_user):
    project = Project(
        name="Soft Project",
        customer_id=customer.id,
        manager_id=test_user.id,
        budget_type=BudgetType.SOFT,
    )
    db.session.add(project)
    db.session.commit()
    assert project.id is not None
