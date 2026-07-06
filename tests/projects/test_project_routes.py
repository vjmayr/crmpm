from decimal import Decimal

import pytest

from app.crm.models import Organization, Person, PricingModel, QualificationStatus, RateUnit
from app.crm.services import (
    create_offer,
    create_proposal,
    promote_to_lead,
    send_offer,
    set_estimation,
)
from app.projects.models import (
    BudgetType,
    Customer,
    CustomerType,
    Project,
    ProjectStatus,
    Section,
    Task,
    WorkPackage,
)
from app.projects.services import (
    create_customer,
    create_project,
    create_section,
    create_task,
    create_work_package,
)
from app.services.offer_acceptance import accept_offer


@pytest.fixture()
def organization(db):
    org = Organization(name="Route Test Org")
    db.session.add(org)
    db.session.commit()
    return org


@pytest.fixture()
def customer(db, organization):
    return create_customer(organization.id, CustomerType.EXTERNAL)


@pytest.fixture()
def project(db, customer, test_user):
    return create_project(
        customer,
        "Route Test Project",
        test_user,
        BudgetType.SOFT,
        budget_hours=Decimal("40"),
    )


def make_offer_linked_project(db, user, estimation_fields, org_name):
    org = Organization(name=org_name)
    person = Person(
        name=f"Contact at {org_name}",
        qualification_status=QualificationStatus.QUALIFIED,
        organization=org,
    )
    db.session.add_all([org, person])
    db.session.commit()
    lead = promote_to_lead(person)
    proposal = create_proposal(lead, f"Offer project {org_name}", "scope", user)
    version = proposal.versions[0]
    set_estimation(version, **estimation_fields)
    offer = create_offer(version)
    send_offer(offer)
    return accept_offer(offer, user)


HARD_ESTIMATION = {
    "pricing_model": PricingModel.TIME_BASED,
    "rate_amount": Decimal("120.00"),
    "rate_unit": RateUnit.HOURLY,
    "estimated_units": Decimal("80"),
    "additional_rate": Decimal("150.00"),
}
FIXED_ESTIMATION = {
    "pricing_model": PricingModel.FIXED,
    "fixed_price": Decimal("9000.00"),
}


# --- customers ---------------------------------------------------------------


def test_customer_list_renders(logged_in_client, customer):
    response = logged_in_client.get("/projects/customers")
    assert response.status_code == 200
    assert b"Route Test Org" in response.data


def test_customer_create_internal(logged_in_client, db, organization):
    response = logged_in_client.post(
        "/projects/customers/new",
        data={"organization_id": str(organization.id), "type": "INTERNAL"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    created = Customer.query.one()
    assert created.type == CustomerType.INTERNAL


def test_customer_duplicate_organization_conflict_inline(logged_in_client, db, customer, organization):
    response = logged_in_client.post(
        "/projects/customers/new",
        data={"organization_id": str(organization.id), "type": "INTERNAL"},
    )
    assert response.status_code == 200
    assert b"already a customer" in response.data
    assert Customer.query.count() == 1


# --- manual project create -----------------------------------------------------


def test_manual_project_create(logged_in_client, db, customer):
    response = logged_in_client.post(
        "/projects/new",
        data={
            "name": "Internal Tooling",
            "customer_id": str(customer.id),
            "budget_type": "SOFT",
            "budget_hours": "25",
            "over_hours_policy": "",
            "over_rate": "",
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    project = Project.query.filter_by(name="Internal Tooling").one()
    assert project.budget_hours == Decimal("25")
    assert project.offer_id is None


def test_manual_project_hard_without_hours_shows_inline_error(logged_in_client, db, customer):
    response = logged_in_client.post(
        "/projects/new",
        data={
            "name": "Bad Hard",
            "customer_id": str(customer.id),
            "budget_type": "HARD",
            "budget_hours": "",
            "over_hours_policy": "",
            "over_rate": "",
        },
    )
    assert response.status_code == 200
    assert b"HARD budget requires" in response.data
    assert Project.query.filter_by(name="Bad Hard").count() == 0


# --- project status inline -----------------------------------------------------


def test_project_status_inline_update(logged_in_client, db, project):
    response = logged_in_client.post(
        f"/projects/{project.id}/status",
        data={"status": "ON_HOLD"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    db.session.expire_all()
    assert db.session.get(Project, project.id).status == ProjectStatus.ON_HOLD


# --- structure CRUD via routes --------------------------------------------------


def test_structure_crud_via_routes(logged_in_client, db, project):
    resp = logged_in_client.post(
        f"/projects/{project.id}/sections",
        data={"name": "Build"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    section = Section.query.one()

    resp = logged_in_client.post(
        f"/projects/sections/{section.id}/work-packages",
        data={"name": "Backend", "estimated_hours": "12"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    wp = WorkPackage.query.one()
    assert wp.estimated_hours == Decimal("12")

    resp = logged_in_client.post(
        f"/projects/work-packages/{wp.id}/tasks",
        data={"title": "Write models", "estimated_hours": "3"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    task = Task.query.one()
    assert task.estimated_hours == Decimal("3")

    # Rename all tiers through the update routes.
    logged_in_client.post(f"/projects/sections/{section.id}/update", data={"name": "Build v2"})
    logged_in_client.post(
        f"/projects/work-packages/{wp.id}/update",
        data={"name": "Backend v2", "estimated_hours": "14"},
    )
    logged_in_client.post(
        f"/projects/tasks/{task.id}/update",
        data={"title": "Write models v2", "status": "IN_PROGRESS", "estimated_hours": "4"},
    )
    db.session.expire_all()
    assert Section.query.one().name == "Build v2"
    assert WorkPackage.query.one().estimated_hours == Decimal("14")
    updated_task = Task.query.one()
    assert updated_task.title == "Write models v2"
    assert updated_task.status.value == "IN_PROGRESS"


def test_blocked_deletes_surface_inline(logged_in_client, db, project):
    section = create_section(project, "Guarded")
    wp = create_work_package(section, "Guarded WP")
    create_task(wp, "Guarded Task")

    resp = logged_in_client.post(
        f"/projects/sections/{section.id}/delete", headers={"HX-Request": "true"}
    )
    assert resp.status_code == 200
    assert b"still has work packages" in resp.data
    assert Section.query.count() == 1

    resp = logged_in_client.post(
        f"/projects/work-packages/{wp.id}/delete", headers={"HX-Request": "true"}
    )
    assert resp.status_code == 200
    assert b"still has tasks" in resp.data
    assert WorkPackage.query.count() == 1


def test_delete_disabled_state_rendered_when_children_exist(logged_in_client, db, project):
    section = create_section(project, "Has Children")
    create_work_package(section, "Child WP")

    response = logged_in_client.get(f"/projects/{project.id}")

    assert b"Delete its work packages first" in response.data


def test_reorder_round_trip(logged_in_client, db, project):
    a = create_section(project, "Alpha")
    b = create_section(project, "Beta")

    resp = logged_in_client.post(
        f"/projects/sections/{b.id}/move",
        data={"direction": "up"},
        headers={"HX-Request": "true"},
    )
    assert resp.status_code == 200
    db.session.expire_all()
    assert (b.position, a.position) == (0, 1)
    # Rendered board reflects the new order.
    body = resp.data.decode()
    assert body.index('value="Beta"') < body.index('value="Alpha"')

    resp = logged_in_client.post(
        f"/projects/sections/{b.id}/move",
        data={"direction": "down"},
        headers={"HX-Request": "true"},
    )
    db.session.expire_all()
    assert (a.position, b.position) == (0, 1)
    body = resp.data.decode()
    assert body.index('value="Alpha"') < body.index('value="Beta"')


# --- roll-up rendering ----------------------------------------------------------


def test_rollup_rendered_on_project_page(logged_in_client, db, project):
    # The 5a fixture shape: 2 sections / 3 WPs / 5 tasks with NULLs.
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

    response = logged_in_client.get(f"/projects/{project.id}")
    body = response.data.decode()

    assert "WP estimates: <strong>30.00h</strong>" in body
    assert "Task estimates: <strong>9.50h</strong>" in body
    assert "(1 unestimated)" in body  # section-level counts
    assert "(2 unestimated)" in body  # project-total task count
    # Budget comparison against budget_hours=40: within budget on both.
    assert "+10.00h within budget" in body
    assert "+30.50h within budget" in body


def test_rollup_over_budget_shows_red_signal(logged_in_client, db, project):
    section = create_section(project, "Over")
    create_work_package(section, "Big WP", estimated_hours=Decimal("55"))

    response = logged_in_client.get(f"/projects/{project.id}")
    body = response.data.decode()

    assert "-15.00h over budget" in body
    assert "text-red-700" in body


def test_rollup_without_budget_shows_no_budget_state(logged_in_client, db, customer, test_user):
    project = create_project(customer, "No Budget", test_user, BudgetType.SOFT)

    response = logged_in_client.get(f"/projects/{project.id}")

    assert b"No budget set" in response.data


# --- budget-health column on the projects list (Phase 6) ------------------------


def test_projects_list_renders_one_project_per_health_state(logged_in_client, db, customer, test_user):
    def with_wp(name, budget_hours, wp_hours):
        project = create_project(customer, name, test_user, BudgetType.SOFT, budget_hours=budget_hours)
        section = create_section(project, "S")
        create_work_package(section, "WP", estimated_hours=wp_hours)
        return project

    create_project(customer, "No Budget Proj", test_user, BudgetType.SOFT)
    with_wp("Within Proj", Decimal("40"), Decimal("10"))
    with_wp("Near Proj", Decimal("40"), Decimal("35"))
    with_wp("Over Proj", Decimal("40"), Decimal("55"))

    body = logged_in_client.get("/projects/").data.decode()

    assert "No budget set" in body
    assert "Within budget" in body
    assert "Near budget" in body
    assert "Over budget" in body
    # Numbers alongside — color is never the only carrier of meaning.
    assert "35.00h of 40.00h" in body
    assert "55.00h of 40.00h" in body


def test_projects_list_empty_state(logged_in_client, db):
    body = logged_in_client.get("/projects/").data.decode()
    assert "No projects yet" in body
    assert "create one manually" in body


# --- budget editing rules --------------------------------------------------------


def test_offerless_project_budget_fully_editable(logged_in_client, db, project):
    response = logged_in_client.post(
        f"/projects/{project.id}/budget",
        data={
            "budget_type": "HARD",
            "budget_hours": "50",
            "over_hours_policy": "BLOCK",
            "over_rate": "",
        },
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    db.session.expire_all()
    updated = db.session.get(Project, project.id)
    assert updated.budget_type == BudgetType.HARD
    assert updated.budget_hours == Decimal("50")


def test_offerless_hard_without_policy_rejected_inline(logged_in_client, db, project):
    response = logged_in_client.post(
        f"/projects/{project.id}/budget",
        data={"budget_type": "HARD", "budget_hours": "50", "over_hours_policy": "", "over_rate": ""},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert b"HARD budget requires" in response.data
    db.session.expire_all()
    assert db.session.get(Project, project.id).budget_type == BudgetType.SOFT


def test_offer_linked_hard_project_budget_fully_locked(logged_in_client, db, test_user):
    project = make_offer_linked_project(db, test_user, HARD_ESTIMATION, "Hard Lock Co")

    page = logged_in_client.get(f"/projects/{project.id}")
    assert b"read-only" in page.data
    assert b"Edit budget" not in page.data
    assert b"Internal planning hours" not in page.data

    response = logged_in_client.post(
        f"/projects/{project.id}/budget",
        data={"budget_hours": "999"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    assert b"read-only" in response.data
    db.session.expire_all()
    assert db.session.get(Project, project.id).budget_hours == Decimal("80")


def test_offer_linked_fixed_project_allows_setting_planning_hours(logged_in_client, db, test_user):
    project = make_offer_linked_project(db, test_user, FIXED_ESTIMATION, "Fixed Plan Co")
    assert project.budget_hours is None

    page = logged_in_client.get(f"/projects/{project.id}")
    assert b"Internal planning hours" in page.data
    assert b"Edit budget" not in page.data  # contract fields not editable

    response = logged_in_client.post(
        f"/projects/{project.id}/budget",
        data={"budget_hours": "35"},
        headers={"HX-Request": "true"},
    )
    assert response.status_code == 200
    db.session.expire_all()
    updated = db.session.get(Project, project.id)
    assert updated.budget_hours == Decimal("35")
    assert updated.budget_type == BudgetType.SOFT  # contract fields untouched
