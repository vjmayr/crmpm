from decimal import Decimal

from flask import abort, flash, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.crm import crm_bp
from app.crm.exceptions import (
    EstimationLockedError,
    EstimationValidationError,
    LeadStateError,
    OfferConflictError,
    OfferStateError,
    PromotionError,
    ProposalError,
)
from app.crm.forms import (
    EstimationForm,
    LeadDiscoveryForm,
    OrganizationForm,
    PersonForm,
    ProposalForm,
    ProposalVersionForm,
)
from app.crm.models import (
    Lead,
    LeadStatus,
    Offer,
    OfferStatus,
    Organization,
    Person,
    PricingModel,
    Proposal,
    ProposalVersion,
    QualificationStatus,
    RateUnit,
)
from app.crm.services import (
    add_version,
    close_lead,
    create_offer,
    create_proposal,
    deny_offer,
    estimation_is_locked,
    promote_to_lead,
    send_offer,
    set_estimation,
)
from app.extensions import db
from app.services.offer_acceptance import (
    OrganizationRequiredError,
    accept_offer,
    project_for_lead,
    project_for_offer,
)


@crm_bp.route("/")
@login_required
def index():
    return render_template("crm/index.html")


def _populate_organization_choices(form):
    organizations = Organization.query.order_by(Organization.name).all()
    form.organization_id.choices = [("", "— Unassigned —")] + [
        (str(org.id), org.name) for org in organizations
    ]


def _open_offer_for_lead(lead):
    return (
        Offer.query.join(ProposalVersion, Offer.proposal_version_id == ProposalVersion.id)
        .join(Proposal, ProposalVersion.proposal_id == Proposal.id)
        .filter(
            Proposal.lead_id == lead.id,
            Offer.status.in_((OfferStatus.DRAFT, OfferStatus.SENT)),
        )
        .first()
    )


def _offers_for_proposal(proposal):
    return (
        Offer.query.join(ProposalVersion, Offer.proposal_version_id == ProposalVersion.id)
        .filter(ProposalVersion.proposal_id == proposal.id)
        .order_by(Offer.id)
        .all()
    )


def _is_locked(version):
    return version.estimation is not None and estimation_is_locked(version.estimation)


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

    proposal_offers = {
        proposal.id: _offers_for_proposal(proposal) for proposal in lead.proposals
    }
    return render_template(
        "crm/leads/detail.html",
        lead=lead,
        form=form,
        proposal_offers=proposal_offers,
        won_project=project_for_lead(lead),
    )


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


# --- Proposals -----------------------------------------------------------


def _to_decimal(value):
    value = (value or "").strip()
    return Decimal(value) if value else None


def _estimation_fields_from_form(form):
    return {
        "pricing_model": PricingModel(form.pricing_model.data),
        "fixed_price": _to_decimal(form.fixed_price.data),
        "rate_amount": _to_decimal(form.rate_amount.data),
        "rate_unit": RateUnit(form.rate_unit.data) if form.rate_unit.data else None,
        "estimated_units": _to_decimal(form.estimated_units.data),
        "additional_rate": _to_decimal(form.additional_rate.data),
    }


def _estimation_form_for(version):
    estimation = version.estimation
    if estimation is None:
        return EstimationForm(pricing_model=PricingModel.FIXED.value)

    def s(value):
        return "" if value is None else str(value)

    return EstimationForm(
        pricing_model=estimation.pricing_model.value,
        fixed_price=s(estimation.fixed_price),
        rate_amount=s(estimation.rate_amount),
        rate_unit=estimation.rate_unit.value if estimation.rate_unit else "",
        estimated_units=s(estimation.estimated_units),
        additional_rate=s(estimation.additional_rate),
    )


@crm_bp.route("/leads/<int:lead_id>/proposals/new", methods=["GET", "POST"])
@login_required
def proposal_create(lead_id):
    lead = Lead.query.get_or_404(lead_id)
    form = ProposalForm()
    error = None

    if form.validate_on_submit():
        try:
            proposal = create_proposal(
                lead, form.title.data.strip(), form.content.data, current_user
            )
        except ProposalError as exc:
            error = str(exc)
        else:
            if request.headers.get("HX-Request"):
                response = make_response("", 200)
                response.headers["HX-Redirect"] = url_for(
                    "crm.proposal_detail", proposal_id=proposal.id
                )
                return response
            flash(f"Created proposal {proposal.title}.", "success")
            return redirect(url_for("crm.proposal_detail", proposal_id=proposal.id))

    if request.headers.get("HX-Request"):
        return render_template(
            "crm/partials/_proposal_form_fields.html", form=form, lead=lead, error=error
        )
    return render_template("crm/proposals/form.html", form=form, lead=lead, error=error)


@crm_bp.route("/proposals/<int:proposal_id>")
@login_required
def proposal_detail(proposal_id):
    proposal = Proposal.query.get_or_404(proposal_id)
    version = proposal.versions[-1]
    return render_template(
        "crm/proposals/detail.html",
        proposal=proposal,
        version=version,
        estimation_form=_estimation_form_for(version),
        locked=_is_locked(version),
        open_offer=_open_offer_for_lead(proposal.lead),
        offers=_offers_for_proposal(proposal),
    )


@crm_bp.route("/proposals/<int:proposal_id>/versions/<int:version_number>")
@login_required
def proposal_version_detail(proposal_id, version_number):
    proposal = Proposal.query.get_or_404(proposal_id)
    version = ProposalVersion.query.filter_by(
        proposal_id=proposal.id, version_number=version_number
    ).first_or_404()

    template = (
        "crm/partials/_version_pane.html"
        if request.headers.get("HX-Request")
        else "crm/proposals/detail.html"
    )
    return render_template(
        template,
        proposal=proposal,
        version=version,
        estimation_form=_estimation_form_for(version),
        locked=_is_locked(version),
        open_offer=_open_offer_for_lead(proposal.lead),
        offers=_offers_for_proposal(proposal),
    )


@crm_bp.route("/proposals/<int:proposal_id>/revise", methods=["GET", "POST"])
@login_required
def proposal_revise(proposal_id):
    proposal = Proposal.query.get_or_404(proposal_id)
    source_number = request.args.get("from", type=int)
    source_version = None
    if source_number:
        source_version = ProposalVersion.query.filter_by(
            proposal_id=proposal.id, version_number=source_number
        ).first()
    if source_version is None:
        source_version = proposal.versions[-1]

    if request.method == "GET":
        form = ProposalVersionForm(content=source_version.content)
    else:
        form = ProposalVersionForm()
    error = None

    if form.validate_on_submit():
        try:
            version = add_version(
                proposal,
                form.content.data,
                current_user,
                copy_estimation=form.copy_estimation.data,
            )
        except ProposalError as exc:
            error = str(exc)
        else:
            if request.headers.get("HX-Request"):
                response = make_response("", 200)
                response.headers["HX-Redirect"] = url_for(
                    "crm.proposal_version_detail",
                    proposal_id=proposal.id,
                    version_number=version.version_number,
                )
                return response
            flash(f"Created version {version.version_number}.", "success")
            return redirect(
                url_for(
                    "crm.proposal_version_detail",
                    proposal_id=proposal.id,
                    version_number=version.version_number,
                )
            )

    context = {
        "proposal": proposal,
        "form": form,
        "source_version": source_version,
        "error": error,
    }
    if request.headers.get("HX-Request"):
        return render_template("crm/partials/_revise_form_fields.html", **context)
    return render_template("crm/proposals/revise_form.html", **context)


@crm_bp.route("/proposal-versions/<int:version_id>/estimation-fields")
@login_required
def estimation_fields_partial(version_id):
    version = ProposalVersion.query.get_or_404(version_id)
    pricing_model = request.args.get("pricing_model", PricingModel.FIXED.value)
    form = EstimationForm(pricing_model=pricing_model)
    return render_template(
        "crm/partials/_estimation_fields.html", form=form, pricing_model=pricing_model
    )


@crm_bp.route("/proposal-versions/<int:version_id>/estimation", methods=["POST"])
@login_required
def estimation_set(version_id):
    version = ProposalVersion.query.get_or_404(version_id)
    form = EstimationForm()
    error = None

    if form.validate_on_submit():
        try:
            set_estimation(version, **_estimation_fields_from_form(form))
        except (EstimationValidationError, EstimationLockedError) as exc:
            error = str(exc)
    else:
        error = "; ".join(
            f"{field}: {', '.join(errs)}" for field, errs in form.errors.items()
        )

    proposal = version.proposal
    if request.headers.get("HX-Request"):
        return render_template(
            "crm/partials/_estimation_panel.html",
            proposal=proposal,
            version=version,
            estimation_form=form,
            error=error,
            locked=_is_locked(version),
        )

    if error:
        flash(error, "error")
    else:
        flash("Estimation saved.", "success")
    return redirect(
        url_for(
            "crm.proposal_version_detail",
            proposal_id=proposal.id,
            version_number=version.version_number,
        )
    )


# --- Offers --------------------------------------------------------------


@crm_bp.route("/proposal-versions/<int:version_id>/offer", methods=["POST"])
@login_required
def offer_create(version_id):
    version = ProposalVersion.query.get_or_404(version_id)
    error = None
    try:
        offer = create_offer(version)
    except (OfferStateError, OfferConflictError) as exc:
        error = str(exc)
    else:
        if request.headers.get("HX-Request"):
            response = make_response("", 200)
            response.headers["HX-Redirect"] = url_for("crm.offer_detail", offer_id=offer.id)
            return response
        flash(f"Created offer for version {version.version_number}.", "success")
        return redirect(url_for("crm.offer_detail", offer_id=offer.id))

    if request.headers.get("HX-Request"):
        return render_template(
            "crm/partials/_create_offer_widget.html",
            version=version,
            open_offer=_open_offer_for_lead(version.proposal.lead),
            error=error,
        )
    flash(error, "error")
    return redirect(
        url_for(
            "crm.proposal_version_detail",
            proposal_id=version.proposal_id,
            version_number=version.version_number,
        )
    )


@crm_bp.route("/offers/<int:offer_id>")
@login_required
def offer_detail(offer_id):
    offer = Offer.query.get_or_404(offer_id)
    project = project_for_offer(offer) if offer.status == OfferStatus.ACCEPTED else None
    return render_template("crm/offers/detail.html", offer=offer, project=project)


@crm_bp.route("/offers/<int:offer_id>/send", methods=["POST"])
@login_required
def offer_send(offer_id):
    offer = Offer.query.get_or_404(offer_id)
    error = None
    try:
        send_offer(offer)
    except OfferStateError as exc:
        error = str(exc)

    if request.headers.get("HX-Request"):
        return render_template("crm/partials/_offer_status_block.html", offer=offer, error=error)
    if error:
        flash(error, "error")
    else:
        flash("Offer sent.", "success")
    return redirect(url_for("crm.offer_detail", offer_id=offer.id))


@crm_bp.route("/offers/<int:offer_id>/deny", methods=["POST"])
@login_required
def offer_deny(offer_id):
    offer = Offer.query.get_or_404(offer_id)
    error = None
    try:
        deny_offer(offer)
    except OfferStateError as exc:
        error = str(exc)

    if request.headers.get("HX-Request"):
        return render_template("crm/partials/_offer_status_block.html", offer=offer, error=error)
    if error:
        flash(error, "error")
    else:
        flash("Offer denied.", "success")
    return redirect(url_for("crm.offer_detail", offer_id=offer.id))


@crm_bp.route("/offers/<int:offer_id>/accept", methods=["POST"])
@login_required
def offer_accept(offer_id):
    offer = Offer.query.get_or_404(offer_id)
    error = None
    show_org_prompt = False
    try:
        project = accept_offer(offer, current_user)
    except OrganizationRequiredError as exc:
        error = str(exc)
        show_org_prompt = True
    except OfferStateError as exc:
        error = str(exc)
    else:
        if request.headers.get("HX-Request"):
            response = make_response("", 200)
            response.headers["HX-Redirect"] = url_for(
                "projects.project_detail", project_id=project.id
            )
            return response
        flash("Offer accepted — project created.", "success")
        return redirect(url_for("projects.project_detail", project_id=project.id))

    if request.headers.get("HX-Request"):
        return render_template(
            "crm/partials/_offer_status_block.html",
            offer=offer,
            error=error,
            show_org_prompt=show_org_prompt,
        )
    flash(error, "error")
    return redirect(url_for("crm.offer_detail", offer_id=offer.id))
