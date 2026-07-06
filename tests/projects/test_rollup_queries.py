from decimal import Decimal

import pytest
from sqlalchemy import event

from app.crm.models import Organization
from app.projects.models import BudgetType, Customer, CustomerType, Project
from app.projects.queries import project_rollup
from app.projects.services import create_section, create_task, create_work_package


@pytest.fixture()
def project(db, test_user):
    org = Organization(name="Rollup Org")
    db.session.add(org)
    db.session.flush()
    customer = Customer(organization_id=org.id, type=CustomerType.EXTERNAL)
    db.session.add(customer)
    db.session.flush()
    project = Project(
        name="Rollup Project",
        customer_id=customer.id,
        manager_id=test_user.id,
        budget_type=BudgetType.SOFT,
        budget_hours=Decimal("40"),
    )
    db.session.add(project)
    db.session.commit()
    return project


@pytest.fixture()
def tree(db, project):
    """2 sections / 3 WPs / 5 tasks, with NULL estimated_hours sprinkled in.

    S1 "Build":  WP1 (10h): T1 2h, T2 NULL, T3 3.5h
                 WP2 (NULL): T4 4h
    S2 "Deploy": WP3 (20h): T5 NULL
    """
    s1 = create_section(project, "Build")
    s2 = create_section(project, "Deploy")
    wp1 = create_work_package(s1, "WP One", estimated_hours=Decimal("10"))
    wp2 = create_work_package(s1, "WP Two")
    wp3 = create_work_package(s2, "WP Three", estimated_hours=Decimal("20"))
    create_task(wp1, "T1", estimated_hours=Decimal("2"))
    create_task(wp1, "T2")
    create_task(wp1, "T3", estimated_hours=Decimal("3.5"))
    create_task(wp2, "T4", estimated_hours=Decimal("4"))
    create_task(wp3, "T5")
    return {"s1": s1, "s2": s2}


def test_rollup_per_section_sums_both_levels_independently(db, project, tree):
    rollup = project_rollup(project)

    by_name = {entry["section"].name: entry for entry in rollup["sections"]}

    build = by_name["Build"]
    assert build["wp_hours"] == Decimal("10")
    assert build["task_hours"] == Decimal("9.5")
    assert build["unestimated_wps"] == 1
    assert build["unestimated_tasks"] == 1

    deploy = by_name["Deploy"]
    assert deploy["wp_hours"] == Decimal("20")
    assert deploy["task_hours"] == Decimal("0")
    assert deploy["unestimated_wps"] == 0
    assert deploy["unestimated_tasks"] == 1


def test_rollup_project_totals_and_budget_deltas(db, project, tree):
    rollup = project_rollup(project)

    totals = rollup["project"]
    assert totals["wp_hours"] == Decimal("30")
    assert totals["task_hours"] == Decimal("9.5")
    assert totals["unestimated_wps"] == 1
    assert totals["unestimated_tasks"] == 2
    assert totals["budget_hours"] == Decimal("40")
    assert totals["wp_delta"] == Decimal("10")
    assert totals["task_delta"] == Decimal("30.5")


def test_rollup_sections_come_back_in_position_order(db, project, tree):
    rollup = project_rollup(project)
    assert [entry["section"].name for entry in rollup["sections"]] == ["Build", "Deploy"]


def test_rollup_without_budget_hours_yields_none_deltas(db, project, tree):
    project.budget_hours = None
    db.session.commit()

    rollup = project_rollup(project)

    assert rollup["project"]["budget_hours"] is None
    assert rollup["project"]["wp_delta"] is None
    assert rollup["project"]["task_delta"] is None


def test_rollup_on_empty_project_is_all_zeros(db, project):
    rollup = project_rollup(project)

    assert rollup["sections"] == []
    totals = rollup["project"]
    assert totals["wp_hours"] == Decimal("0")
    assert totals["task_hours"] == Decimal("0")
    assert totals["unestimated_wps"] == 0
    assert totals["unestimated_tasks"] == 0


def test_rollup_query_count_is_constant(db, project, tree):
    # Warm the project instance (expired by the fixture's last commit) so the
    # counter measures the rollup itself, not the session's refresh-on-access.
    _ = project.budget_hours

    counter = {"n": 0}

    def count_query(conn, cursor, statement, parameters, context, executemany):
        counter["n"] += 1

    engine = db.session.get_bind()
    event.listen(engine, "before_cursor_execute", count_query)
    try:
        project_rollup(project)
    finally:
        event.remove(engine, "before_cursor_execute", count_query)

    # One query per level (sections, WP aggregates, task aggregates) —
    # never one per node. The 2/3/5 fixture would need ~8+ under N+1.
    assert counter["n"] <= 3
