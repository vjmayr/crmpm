from decimal import Decimal

from flask import abort, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from app.core.models import User
from app.extensions import db
from app.projects import projects_bp
from app.projects.exceptions import ProjectValidationError, StructureError
from app.projects.forms import CustomerForm, ProjectForm
from app.projects.models import (
    BudgetType,
    Customer,
    CustomerType,
    OverHoursPolicy,
    Project,
    ProjectStatus,
    Section,
    Task,
    TaskStatus,
    WorkPackage,
)
from app.projects.queries import portfolio_rollup, project_rollup
from app.projects.services import (
    create_customer,
    create_project,
    create_section,
    create_task,
    create_work_package,
    delete_section,
    delete_task,
    delete_work_package,
    move_item,
    rename_section,
    rename_task,
    rename_work_package,
    set_planning_hours,
    update_project_budget,
)

# NOTE: app.services.offer_acceptance is imported lazily inside customer_create.
# A module-level import would be circular: crm.routes -> offer_acceptance ->
# app.projects.models -> app.projects.__init__ -> this module -> offer_acceptance.


def _to_decimal(value):
    value = (value or "").strip()
    return Decimal(value) if value else None


# --- Customers ------------------------------------------------------------


@projects_bp.route("/customers")
@login_required
def customer_list():
    customers = Customer.query.order_by(Customer.id).all()
    return render_template("projects/customers/list.html", customers=customers)


@projects_bp.route("/customers/new", methods=["GET", "POST"])
@login_required
def customer_create():
    from app.services.offer_acceptance import organizations_directory

    form = CustomerForm()
    form.organization_id.choices = [
        (org.id, org.name) for org in organizations_directory()
    ]
    if form.validate_on_submit():
        try:
            customer = create_customer(
                form.organization_id.data, CustomerType(form.type.data)
            )
        except IntegrityError:
            db.session.rollback()
            form.organization_id.errors.append(
                "This organization is already a customer (one customer per organization)."
            )
        else:
            flash(f"Created customer {customer.organization.name}.", "success")
            return redirect(url_for("projects.customer_list"))

    return render_template("projects/customers/form.html", form=form)


# --- Projects: list / create / status / budget ------------------------------


@projects_bp.route("/")
@login_required
def index():
    # portfolio_rollup covers all statuses here; the dashboard's strip
    # passes [ProjectStatus.ACTIVE] — same query, different filter.
    entries = portfolio_rollup()
    entries.reverse()  # newest project first, matching the old ordering
    return render_template("projects/list.html", entries=entries)


@projects_bp.route("/new", methods=["GET", "POST"])
@login_required
def project_create():
    form = ProjectForm()
    form.customer_id.choices = [
        (customer.id, customer.organization.name)
        for customer in Customer.query.order_by(Customer.id).all()
    ]
    error = None
    if form.validate_on_submit():
        customer = db.session.get(Customer, form.customer_id.data)
        try:
            project = create_project(
                customer,
                form.name.data.strip(),
                current_user,
                BudgetType(form.budget_type.data),
                budget_hours=_to_decimal(form.budget_hours.data),
                over_hours_policy=(
                    OverHoursPolicy(form.over_hours_policy.data)
                    if form.over_hours_policy.data
                    else None
                ),
                over_rate=_to_decimal(form.over_rate.data),
            )
        except ProjectValidationError as exc:
            error = str(exc)
        else:
            flash(f"Created project {project.name}.", "success")
            return redirect(url_for("projects.project_detail", project_id=project.id))

    return render_template("projects/form.html", form=form, error=error)


def _body_context(project, board_error=None, budget_error=None):
    # Eager-load the display tree (the identity map hands project_rollup's
    # Section rows the same, already-loaded objects).
    sections = (
        Section.query.options(
            selectinload(Section.work_packages).selectinload(WorkPackage.tasks)
        )
        .filter_by(project_id=project.id)
        .order_by(Section.position)
        .all()
    )
    return {
        "project": project,
        "sections": sections,
        "rollup": project_rollup(project),
        "users": User.query.order_by(User.name).all(),
        "board_error": board_error,
        "budget_error": budget_error,
    }


@projects_bp.route("/<int:project_id>")
@login_required
def project_detail(project_id):
    project = Project.query.get_or_404(project_id)
    return render_template(
        "projects/detail.html",
        statuses=list(ProjectStatus),
        **_body_context(project),
    )


@projects_bp.route("/<int:project_id>/status", methods=["POST"])
@login_required
def project_status(project_id):
    project = Project.query.get_or_404(project_id)
    value = request.form.get("status", "")
    try:
        # Plain editable field like qualification_status — not service-managed
        # (CLAUDE.md rule #7 covers Lead/Offer status only).
        project.status = ProjectStatus(value)
    except ValueError:
        abort(400)
    db.session.commit()

    if request.headers.get("HX-Request"):
        return render_template(
            "projects/partials/_status_select.html",
            project=project,
            statuses=list(ProjectStatus),
        )
    return redirect(url_for("projects.project_detail", project_id=project.id))


@projects_bp.route("/<int:project_id>/budget", methods=["POST"])
@login_required
def project_budget(project_id):
    project = Project.query.get_or_404(project_id)
    error = None
    try:
        if project.offer_id is not None:
            set_planning_hours(project, _to_decimal(request.form.get("budget_hours")))
        else:
            update_project_budget(
                project,
                BudgetType(request.form.get("budget_type", "")),
                budget_hours=_to_decimal(request.form.get("budget_hours")),
                over_hours_policy=(
                    OverHoursPolicy(request.form["over_hours_policy"])
                    if request.form.get("over_hours_policy")
                    else None
                ),
                over_rate=_to_decimal(request.form.get("over_rate")),
            )
    except (ProjectValidationError, ValueError) as exc:
        db.session.rollback()
        error = str(exc) or "Invalid budget values."

    if request.headers.get("HX-Request"):
        return render_template(
            "projects/partials/_project_body.html",
            **_body_context(project, budget_error=error),
        )
    if error:
        flash(error, "error")
    else:
        flash("Budget updated.", "success")
    return redirect(url_for("projects.project_detail", project_id=project.id))


# --- Structure board ---------------------------------------------------------


def _board_response(project, error=None):
    if request.headers.get("HX-Request"):
        return render_template(
            "projects/partials/_project_body.html",
            **_body_context(project, board_error=error),
        )
    if error:
        flash(error, "error")
    return redirect(url_for("projects.project_detail", project_id=project.id))


@projects_bp.route("/<int:project_id>/sections", methods=["POST"])
@login_required
def section_create(project_id):
    project = Project.query.get_or_404(project_id)
    name = request.form.get("name", "").strip()
    if name:
        create_section(project, name)
    return _board_response(project)


@projects_bp.route("/sections/<int:section_id>/update", methods=["POST"])
@login_required
def section_update(section_id):
    section = Section.query.get_or_404(section_id)
    name = request.form.get("name", "").strip()
    if name and name != section.name:
        rename_section(section, name)
    return _board_response(section.project)


@projects_bp.route("/sections/<int:section_id>/delete", methods=["POST"])
@login_required
def section_delete(section_id):
    section = Section.query.get_or_404(section_id)
    project = section.project
    error = None
    try:
        delete_section(section)
    except StructureError as exc:
        error = str(exc)
    return _board_response(project, error)


@projects_bp.route("/sections/<int:section_id>/move", methods=["POST"])
@login_required
def section_move(section_id):
    section = Section.query.get_or_404(section_id)
    error = None
    try:
        move_item(section, request.form.get("direction", ""))
    except StructureError as exc:
        error = str(exc)
    return _board_response(section.project, error)


@projects_bp.route("/sections/<int:section_id>/work-packages", methods=["POST"])
@login_required
def work_package_create(section_id):
    section = Section.query.get_or_404(section_id)
    name = request.form.get("name", "").strip()
    if name:
        create_work_package(
            section, name, estimated_hours=_to_decimal(request.form.get("estimated_hours"))
        )
    return _board_response(section.project)


@projects_bp.route("/work-packages/<int:wp_id>/update", methods=["POST"])
@login_required
def work_package_update(wp_id):
    work_package = WorkPackage.query.get_or_404(wp_id)
    name = request.form.get("name", "").strip()
    if name and name != work_package.name:
        rename_work_package(work_package, name)
    if "estimated_hours" in request.form:
        # Plain field edit (planning data), like Task fields below.
        work_package.estimated_hours = _to_decimal(request.form.get("estimated_hours"))
        db.session.commit()
    return _board_response(work_package.section.project)


@projects_bp.route("/work-packages/<int:wp_id>/delete", methods=["POST"])
@login_required
def work_package_delete(wp_id):
    work_package = WorkPackage.query.get_or_404(wp_id)
    project = work_package.section.project
    error = None
    try:
        delete_work_package(work_package)
    except StructureError as exc:
        error = str(exc)
    return _board_response(project, error)


@projects_bp.route("/work-packages/<int:wp_id>/move", methods=["POST"])
@login_required
def work_package_move(wp_id):
    work_package = WorkPackage.query.get_or_404(wp_id)
    error = None
    try:
        move_item(work_package, request.form.get("direction", ""))
    except StructureError as exc:
        error = str(exc)
    return _board_response(work_package.section.project, error)


@projects_bp.route("/work-packages/<int:wp_id>/tasks", methods=["POST"])
@login_required
def task_create(wp_id):
    work_package = WorkPackage.query.get_or_404(wp_id)
    title = request.form.get("title", "").strip()
    if title:
        create_task(
            work_package,
            title,
            estimated_hours=_to_decimal(request.form.get("estimated_hours")),
        )
    return _board_response(work_package.section.project)


@projects_bp.route("/tasks/<int:task_id>/update", methods=["POST"])
@login_required
def task_update(task_id):
    task = Task.query.get_or_404(task_id)
    error = None

    title = request.form.get("title", "").strip()
    if title and title != task.title:
        rename_task(task, title)

    # Status, assignee, and estimated_hours are plain editable fields like
    # qualification_status — not service-managed (rule #7 is Lead/Offer only).
    if "status" in request.form:
        try:
            task.status = TaskStatus(request.form["status"])
        except ValueError:
            error = "Unknown task status."
    if "assignee_id" in request.form:
        raw = request.form.get("assignee_id", "")
        task.assignee_id = int(raw) if raw else None
    if "estimated_hours" in request.form:
        task.estimated_hours = _to_decimal(request.form.get("estimated_hours"))
    db.session.commit()

    return _board_response(task.work_package.section.project, error)


@projects_bp.route("/tasks/<int:task_id>/delete", methods=["POST"])
@login_required
def task_delete(task_id):
    task = Task.query.get_or_404(task_id)
    project = task.work_package.section.project
    delete_task(task)
    return _board_response(project)


@projects_bp.route("/tasks/<int:task_id>/move", methods=["POST"])
@login_required
def task_move(task_id):
    task = Task.query.get_or_404(task_id)
    error = None
    try:
        move_item(task, request.form.get("direction", ""))
    except StructureError as exc:
        error = str(exc)
    return _board_response(task.work_package.section.project, error)
