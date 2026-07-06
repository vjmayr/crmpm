from flask import redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user
from sqlalchemy import func
from sqlalchemy.orm import joinedload

from app.core import core_bp
from app.core.forms import LoginForm
from app.core.models import User

# The dashboard reads across both domains. That's fine here: CLAUDE.md rule #4
# forbids CRM and Projects importing EACH OTHER for writes — core is the shared
# base module, and these are aggregate reads only.
from app.crm.models import (
    Lead,
    LeadStatus,
    Offer,
    OfferStatus,
    Person,
    Proposal,
    ProposalVersion,
    QualificationStatus,
)
from app.extensions import db
from app.projects.models import ProjectStatus
from app.projects.queries import portfolio_rollup


@core_bp.route("/")
@login_required
def dashboard():
    funnel_rows = dict(
        db.session.query(Lead.status, func.count(Lead.id)).group_by(Lead.status).all()
    )
    funnel = [(status, funnel_rows.get(status, 0)) for status in LeadStatus]

    people_total = Person.query.count()
    contactable_count = Person.contactable().count()
    promotion_candidates = (
        Person.query.outerjoin(Lead, Lead.person_id == Person.id)
        .filter(
            Person.qualification_status == QualificationStatus.QUALIFIED,
            Lead.id.is_(None),
        )
        .count()
    )

    offers_in_flight = (
        Offer.query.filter(Offer.status == OfferStatus.SENT)
        .options(
            joinedload(Offer.version)
            .joinedload(ProposalVersion.proposal)
            .joinedload(Proposal.lead)
            .joinedload(Lead.person)
            .joinedload(Person.organization)
        )
        .order_by(Offer.sent_at)
        .all()
    )

    portfolio = portfolio_rollup(statuses=[ProjectStatus.ACTIVE])

    return render_template(
        "core/dashboard.html",
        funnel=funnel,
        people_total=people_total,
        contactable_count=contactable_count,
        promotion_candidates=promotion_candidates,
        offers_in_flight=offers_in_flight,
        portfolio=portfolio,
    )


@core_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("core.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("core.dashboard"))
        form.password.errors.append("Invalid email or password")

    return render_template("core/login.html", form=form)


@core_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("core.login"))
