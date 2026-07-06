from flask import abort, flash, make_response, redirect, render_template, request, url_for
from flask_login import login_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.crm import crm_bp
from app.crm.exceptions import LeadStateError, PromotionError
from app.crm.forms import LeadDiscoveryForm, OrganizationForm, PersonForm
from app.crm.models import Lead, LeadStatus, Organization, Person, QualificationStatus
from app.crm.services import close_lead, promote_to_lead
from app.extensions import db


@crm_bp.route("/")
@login_required
def index():
    return render_template("crm/index.html")


def _populate_organization_choices(form):
    organizations = Organization.query.order_by(Organization.name).all()
    form.organization_id.choices = [("", "— Unassigned —")] + [
        (str(org.id), org.name) for org in organizations
    ]


# --- Organizations ---------------------------------------------------------


@crm_bp.route("/organizations")
@login_required
def organization_list():
    search = request.args.get("search", "").strip()

    query = Organization.query
    if search:
        query = query.filter(Organization.name.ilike(f"%{search}%"))
    organizations = query.order_by(Organization.name).all()

    template = (
        "crm/partials/_organization_table.html"
        if request.headers.get("HX-Request")
        else "crm/organizations/list.html"
    )
    return render_template(template, organizations=organizations, search=search)


@crm_bp.route("/organizations/new", methods=["GET", "POST"])
@login_required
def organization_create():
    form = OrganizationForm()
    if form.validate_on_submit():
        organization = Organization(
            name=form.name.data.strip(),
            website=form.website.data.strip() or None,
            notes=form.notes.data.strip() or None,
        )
        db.session.add(organization)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            form.name.errors.append("An organization with this name already exists.")
        else:
            flash(f"Created {organization.name}.", "success")
            return redirect(url_for("crm.organization_detail", organization_id=organization.id))

    return render_template("crm/organizations/form.html", form=form, organization=None)


@crm_bp.route("/organizations/<int:organization_id>")
@login_required
def organization_detail(organization_id):
    organization = Organization.query.get_or_404(organization_id)
    return render_template("crm/organizations/detail.html", organization=organization)


@crm_bp.route("/organizations/<int:organization_id>/edit", methods=["GET", "POST"])
@login_required
def organization_edit(organization_id):
    organization = Organization.query.get_or_404(organization_id)
    form = OrganizationForm(obj=organization)
    if form.validate_on_submit():
        organization.name = form.name.data.strip()
        organization.website = form.website.data.strip() or None
        organization.notes = form.notes.data.strip() or None
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            form.name.errors.append("An organization with this name already exists.")
        else:
            flash(f"Updated {organization.name}.", "success")
            return redirect(url_for("crm.organization_detail", organization_id=organization.id))

    return render_template("crm/organizations/form.html", form=form, organization=organization)


@crm_bp.route("/organizations/<int:organization_id>/delete", methods=["POST"])
@login_required
def organization_delete(organization_id):
    organization = Organization.query.get_or_404(organization_id)
    linked_people = Person.query.filter_by(organization_id=organization.id).count()
    if linked_people:
        flash(
            f"Cannot delete {organization.name}: {linked_people} "
            "person(s) still reference it.",
            "error",
        )
        return redirect(url_for("crm.organization_detail", organization_id=organization.id))

    db.session.delete(organization)
    db.session.commit()
    flash(f"Deleted {organization.name}.", "success")
    return redirect(url_for("crm.organization_list"))


# --- People ------------------------------------------------------------


@crm_bp.route("/people")
@login_required
def person_list():
    search = request.args.get("search", "").strip()
    status = request.args.get("qualification_status", "").strip()
    contactable_only = request.args.get("contactable_only") == "1"

    query = Person.contactable() if contactable_only else Person.query

    if search:
        like = f"%{search}%"
        query = query.filter(or_(Person.name.ilike(like), Person.email.ilike(like)))

    if status:
        try:
            query = query.filter(Person.qualification_status == QualificationStatus(status))
        except ValueError:
            status = ""

    people = query.order_by(Person.name).all()

    template = (
        "crm/partials/_person_table.html"
        if request.headers.get("HX-Request")
        else "crm/people/list.html"
    )
    return render_template(
        template,
        people=people,
        search=search,
        status=status,
        contactable_only=contactable_only,
        statuses=list(QualificationStatus),
    )


@crm_bp.route("/people/new", methods=["GET", "POST"])
@login_required
def person_create():
    form = PersonForm()
    _populate_organization_choices(form)
    if form.validate_on_submit():
        person = Person(
            name=form.name.data.strip(),
            email=form.email.data.strip() or None,
            phone=form.phone.data.strip() or None,
            organization_id=form.organization_id.data,
            qualification_status=QualificationStatus(form.qualification_status.data),
            permission_to_contact=form.permission_to_contact.data,
        )
        db.session.add(person)
        db.session.commit()
        flash(f"Created {person.name}.", "success")
        return redirect(url_for("crm.person_detail", person_id=person.id))

    return render_template("crm/people/form.html", form=form, person=None)


@crm_bp.route("/people/<int:person_id>")
@login_required
def person_detail(person_id):
    person = Person.query.get_or_404(person_id)
    return render_template("crm/people/detail.html", person=person)


@crm_bp.route("/people/<int:person_id>/edit", methods=["GET", "POST"])
@login_required
def person_edit(person_id):
    person = Person.query.get_or_404(person_id)
    form = PersonForm(obj=person, qualification_status=person.qualification_status.value)
    _populate_organization_choices(form)
    if form.validate_on_submit():
        person.name = form.name.data.strip()
        person.email = form.email.data.strip() or None
        person.phone = form.phone.data.strip() or None
        person.organization_id = form.organization_id.data
        person.qualification_status = QualificationStatus(form.qualification_status.data)
        person.permission_to_contact = form.permission_to_contact.data
        db.session.commit()
        flash(f"Updated {person.name}.", "success")
        return redirect(url_for("crm.person_detail", person_id=person.id))

    return render_template("crm/people/form.html", form=form, person=person)


@crm_bp.route("/people/<int:person_id>/qualification", methods=["POST"])
@login_required
def person_update_qualification(person_id):
    person = Person.query.get_or_404(person_id)
    value = request.form.get("qualification_status", "")
    try:
        person.qualification_status = QualificationStatus(value)
    except ValueError:
        abort(400)

    db.session.commit()
    return render_template(
        "crm/partials/_person_row.html", person=person, statuses=list(QualificationStatus)
    )


@crm_bp.route("/people/<int:person_id>/promote", methods=["POST"])
@login_required
def person_promote(person_id):
    person = Person.query.get_or_404(person_id)
    try:
        lead = promote_to_lead(person)
    except PromotionError as exc:
        if request.headers.get("HX-Request"):
            return render_template(
                "crm/partials/_promote_widget.html", person=person, error=str(exc)
            )
        flash(str(exc), "error")
        return redirect(url_for("crm.person_detail", person_id=person.id))

    if request.headers.get("HX-Request"):
        response = make_response("", 200)
        response.headers["HX-Redirect"] = url_for("crm.lead_detail", lead_id=lead.id)
        return response

    flash(f"Promoted {person.name} to a lead.", "success")
    return redirect(url_for("crm.lead_detail", lead_id=lead.id))


# --- Leads -------------------------------------------------------------


@crm_bp.route("/leads")
@login_required
def lead_list():
    search = request.args.get("search", "").strip()
    status = request.args.get("status", "").strip()

    query = Lead.query.join(Person, Lead.person_id == Person.id).outerjoin(
        Organization, Person.organization_id == Organization.id
    )
    if search:
        like = f"%{search}%"
        query = query.filter(or_(Person.name.ilike(like), Organization.name.ilike(like)))
    if status:
        try:
            query = query.filter(Lead.status == LeadStatus(status))
        except ValueError:
            status = ""

    leads = query.order_by(Lead.created_at.desc()).all()

    template = (
        "crm/partials/_lead_table.html"
        if request.headers.get("HX-Request")
        else "crm/leads/list.html"
    )
    return render_template(
        template, leads=leads, search=search, status=status, statuses=list(LeadStatus)
    )


@crm_bp.route("/leads/<int:lead_id>", methods=["GET", "POST"])
@login_required
def lead_detail(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    form = LeadDiscoveryForm(obj=lead)
    if form.validate_on_submit():
        lead.source = form.source.data.strip() or None
        lead.timeline = form.timeline.data.strip() or None
        lead.budget_range = form.budget_range.data.strip() or None
        lead.pain_points = form.pain_points.data.strip() or None
        lead.discovery_notes = form.discovery_notes.data.strip() or None
        db.session.commit()
        flash("Updated discovery notes.", "success")
        return redirect(url_for("crm.lead_detail", lead_id=lead.id))

    return render_template("crm/leads/detail.html", lead=lead, form=form)


@crm_bp.route("/leads/<int:lead_id>/close", methods=["POST"])
@login_required
def lead_close(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    try:
        close_lead(lead)
    except LeadStateError as exc:
        if request.headers.get("HX-Request"):
            return render_template("crm/partials/_lead_status_block.html", lead=lead, error=str(exc))
        flash(str(exc), "error")
        return redirect(url_for("crm.lead_detail", lead_id=lead.id))

    if request.headers.get("HX-Request"):
        return render_template("crm/partials/_lead_status_block.html", lead=lead)

    flash("Lead marked as LOST.", "success")
    return redirect(url_for("crm.lead_detail", lead_id=lead.id))
