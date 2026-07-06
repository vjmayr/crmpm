import pytest

from app.crm.models import Organization
from app.projects.exceptions import StructureError
from app.projects.models import (
    BudgetType,
    Customer,
    CustomerType,
    Section,
    Task,
    TaskStatus,
    WorkPackage,
)
from app.projects.services import (
    create_section,
    create_task,
    create_work_package,
    delete_section,
    delete_task,
    delete_work_package,
    move_item,
    move_to_parent,
    rename_section,
    rename_task,
    rename_work_package,
)


@pytest.fixture()
def project(db, test_user):
    org = Organization(name="Structure Org")
    db.session.add(org)
    db.session.flush()
    customer = Customer(organization_id=org.id, type=CustomerType.EXTERNAL)
    db.session.add(customer)
    db.session.flush()
    from app.projects.models import Project

    project = Project(
        name="Structure Project",
        customer_id=customer.id,
        manager_id=test_user.id,
        budget_type=BudgetType.SOFT,
    )
    db.session.add(project)
    db.session.commit()
    return project


def positions_of(model, parent_attr, parent_id):
    rows = (
        model.query.filter(getattr(model, parent_attr) == parent_id)
        .order_by(model.position)
        .all()
    )
    return [(row.position, row.name if hasattr(row, "name") else row.title) for row in rows]


def assert_contiguous(model, parent_attr, parent_id):
    rows = positions_of(model, parent_attr, parent_id)
    assert [p for p, _ in rows] == list(range(len(rows)))


# --- invariant #12: strict 3-tier hierarchy, structurally -------------------


def test_task_has_no_project_or_section_columns():
    column_names = {column.name for column in Task.__table__.columns}
    assert "project_id" not in column_names
    assert "section_id" not in column_names
    assert "work_package_id" in column_names


def test_task_cannot_be_constructed_with_section_or_project():
    with pytest.raises(TypeError):
        Task(title="Rogue", section_id=1)
    with pytest.raises(TypeError):
        Task(title="Rogue", project_id=1)


# --- creation appends at the end ---------------------------------------------


def test_creation_appends_positions_contiguously(db, project):
    a = create_section(project, "Alpha")
    b = create_section(project, "Beta")
    c = create_section(project, "Gamma")

    assert (a.position, b.position, c.position) == (0, 1, 2)
    assert_contiguous(Section, "project_id", project.id)

    wp1 = create_work_package(a, "WP One")
    wp2 = create_work_package(a, "WP Two")
    assert (wp1.position, wp2.position) == (0, 1)

    t1 = create_task(wp1, "Task One")
    t2 = create_task(wp1, "Task Two")
    assert (t1.position, t2.position) == (0, 1)
    assert t1.status == TaskStatus.TODO


def test_position_scopes_are_per_parent(db, project):
    a = create_section(project, "Alpha")
    b = create_section(project, "Beta")

    wp_a = create_work_package(a, "In Alpha")
    wp_b = create_work_package(b, "In Beta")

    # Each parent scope numbers independently from 0.
    assert wp_a.position == 0
    assert wp_b.position == 0


# --- move_item ----------------------------------------------------------------


def test_move_up_swaps_with_previous_sibling(db, project):
    create_section(project, "Alpha")
    create_section(project, "Beta")
    c = create_section(project, "Gamma")

    move_item(c, "up")

    assert positions_of(Section, "project_id", project.id) == [
        (0, "Alpha"),
        (1, "Gamma"),
        (2, "Beta"),
    ]
    assert_contiguous(Section, "project_id", project.id)


def test_move_down_swaps_with_next_sibling(db, project):
    a = create_section(project, "Alpha")
    create_section(project, "Beta")

    move_item(a, "down")

    assert positions_of(Section, "project_id", project.id) == [
        (0, "Beta"),
        (1, "Alpha"),
    ]


def test_move_up_at_top_is_noop(db, project):
    a = create_section(project, "Alpha")
    create_section(project, "Beta")

    move_item(a, "up")

    assert positions_of(Section, "project_id", project.id) == [
        (0, "Alpha"),
        (1, "Beta"),
    ]


def test_move_down_at_bottom_is_noop(db, project):
    create_section(project, "Alpha")
    b = create_section(project, "Beta")

    move_item(b, "down")

    assert positions_of(Section, "project_id", project.id) == [
        (0, "Alpha"),
        (1, "Beta"),
    ]


def test_move_never_crosses_parents(db, project):
    a = create_section(project, "Alpha")
    b = create_section(project, "Beta")
    create_work_package(a, "A WP")
    wp_b = create_work_package(b, "B WP")

    # wp_b is at position 0 in Beta; "up" must be a no-op, not a hop into Alpha.
    move_item(wp_b, "up")

    assert wp_b.section_id == b.id
    assert wp_b.position == 0


def test_move_item_works_for_tasks(db, project):
    section = create_section(project, "Alpha")
    wp = create_work_package(section, "WP")
    create_task(wp, "First")
    t2 = create_task(wp, "Second")

    move_item(t2, "up")

    assert positions_of(Task, "work_package_id", wp.id) == [
        (0, "Second"),
        (1, "First"),
    ]


def test_move_item_rejects_unknown_direction(db, project):
    section = create_section(project, "Alpha")
    with pytest.raises(StructureError):
        move_item(section, "sideways")


def test_cross_parent_move_raises(db, project):
    a = create_section(project, "Alpha")
    b = create_section(project, "Beta")
    wp = create_work_package(a, "WP")
    task = create_task(wp, "Task")
    other_wp = create_work_package(b, "Other WP")

    with pytest.raises(StructureError):
        move_to_parent(task, other_wp)
    assert task.work_package_id == wp.id


# --- delete: bottom-up only, renumbering --------------------------------------


def test_delete_section_with_work_packages_raises(db, project):
    section = create_section(project, "Alpha")
    create_work_package(section, "WP")

    with pytest.raises(StructureError):
        delete_section(section)
    assert Section.query.count() == 1


def test_delete_work_package_with_tasks_raises(db, project):
    section = create_section(project, "Alpha")
    wp = create_work_package(section, "WP")
    create_task(wp, "Task")

    with pytest.raises(StructureError):
        delete_work_package(wp)
    assert WorkPackage.query.count() == 1


def test_bottom_up_delete_succeeds(db, project):
    section = create_section(project, "Alpha")
    wp = create_work_package(section, "WP")
    task = create_task(wp, "Task")

    delete_task(task)
    delete_work_package(wp)
    delete_section(section)

    assert Task.query.count() == 0
    assert WorkPackage.query.count() == 0
    assert Section.query.count() == 0


def test_delete_renumbers_to_close_the_gap(db, project):
    create_section(project, "Alpha")
    b = create_section(project, "Beta")
    create_section(project, "Gamma")

    delete_section(b)

    assert positions_of(Section, "project_id", project.id) == [
        (0, "Alpha"),
        (1, "Gamma"),
    ]
    assert_contiguous(Section, "project_id", project.id)


def test_delete_renumbering_is_scoped_to_the_parent(db, project):
    a = create_section(project, "Alpha")
    b = create_section(project, "Beta")
    create_work_package(a, "A1")
    a2 = create_work_package(a, "A2")
    create_work_package(b, "B1")
    b2 = create_work_package(b, "B2")

    delete_work_package(a2)

    assert_contiguous(WorkPackage, "section_id", a.id)
    # Beta's numbering untouched.
    assert positions_of(WorkPackage, "section_id", b.id) == [(0, "B1"), (1, "B2")]


# --- rename -------------------------------------------------------------------


def test_rename_all_three_tiers(db, project):
    section = create_section(project, "Old Section")
    wp = create_work_package(section, "Old WP")
    task = create_task(wp, "Old Task")

    rename_section(section, "New Section")
    rename_work_package(wp, "New WP")
    rename_task(task, "New Task")

    db.session.expire_all()
    assert Section.query.one().name == "New Section"
    assert WorkPackage.query.one().name == "New WP"
    assert Task.query.one().title == "New Task"
