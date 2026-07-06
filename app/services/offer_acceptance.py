"""Offer acceptance — the cross-domain transaction (invariant #8, D8).

This module is the ONLY place that imports across the CRM/Projects write
seam (CLAUDE.md rule #4). No other module may write to both domains.

accept_offer() is a single atomic unit: get-or-create Customer, create
Project (budget per ARCHITECTURE.md §4.4), Offer -> ACCEPTED,
Lead -> WON — one commit, full rollback on any failure.
"""

from datetime import datetime, timezone
from decimal import Decimal

from app.crm.exceptions import CrmDomainError, OfferStateError
from app.crm.models import (
    LeadStatus,
    Offer,
    OfferStatus,
    PricingModel,
    Proposal,
    ProposalVersion,
    RateUnit,
)
from app.extensions import db
from app.projects.models import (
    BudgetType,
    Customer,
    CustomerType,
    OverHoursPolicy,
    Project,
)

#: DAILY estimation units normalize to hours at this rate (see DECISIONS.md).
HOURS_PER_DAY = Decimal("8")


class OrganizationRequiredError(CrmDomainError):
    """The lead's person has no Organization — assign one, then re-accept.

    Halt-and-prompt case (§4.1): acceptance never auto-creates organizations.
    """


def _normalized_hours(estimation):
    if estimation.estimated_units is None:
        return None
    units = estimation.estimated_units
    if estimation.rate_unit == RateUnit.DAILY:
        return units * HOURS_PER_DAY
    return units


def _budget_fields(estimation):
    """Map an Estimation to Project budget fields per §4.4 (see DECISIONS.md
    for the FIXED -> budget_hours NULL amendment)."""
    if estimation.pricing_model == PricingModel.FIXED:
        return {
            "budget_type": BudgetType.SOFT,
            "budget_hours": None,
            "over_hours_policy": None,
            "over_rate": None,
        }

    hours = _normalized_hours(estimation)
    if estimation.additional_rate is not None:
        return {
            "budget_type": BudgetType.HARD,
            "budget_hours": hours,
            "over_hours_policy": OverHoursPolicy.BILL_AT_RATE,
            "over_rate": estimation.additional_rate,
        }
    return {
        "budget_type": BudgetType.SOFT,
        "budget_hours": hours,
        "over_hours_policy": None,
        "over_rate": None,
    }


def _mark_lead_won(lead):
    # Separate seam so tests can force a mid-transaction failure (invariant #8).
    lead.status = LeadStatus.WON


def accept_offer(offer, manager):
    """SENT -> ACCEPTED, atomically creating the project side (§4.1 row 2).

    Returns the created Project. Raises OrganizationRequiredError if the
    lead's person has no organization (nothing persisted), OfferStateError
    if the offer is not SENT.
    """
    if offer.status != OfferStatus.SENT:
        raise OfferStateError(
            f"Offer {offer.id} is {offer.status.value}; only SENT offers can be accepted."
        )

    version = offer.version
    proposal = version.proposal
    lead = proposal.lead
    person = lead.person
    organization = person.organization
    if organization is None:
        raise OrganizationRequiredError(
            f"{person.name} has no organization. Assign one, then accept again."
        )

    try:
        customer = Customer.query.filter_by(organization_id=organization.id).first()
        if customer is None:
            # Pipeline-originated customers are EXTERNAL (see DECISIONS.md).
            customer = Customer(
                organization_id=organization.id, type=CustomerType.EXTERNAL
            )
            db.session.add(customer)
            db.session.flush()

        project = Project(
            name=proposal.title,
            customer_id=customer.id,
            manager_id=manager.id,
            offer_id=offer.id,
            **_budget_fields(version.estimation),
        )
        db.session.add(project)

        offer.status = OfferStatus.ACCEPTED
        offer.decided_at = datetime.now(timezone.utc)
        _mark_lead_won(lead)

        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

    return project


def project_for_offer(offer):
    """Read-only UI-display helper — app/services/ stays the only module that
    imports across the CRM/Projects seam, so routes never import Project."""
    return Project.query.filter_by(offer_id=offer.id).first()


def project_for_lead(lead):
    """The Project resulting from a WON lead's accepted offer, or None."""
    if lead.status != LeadStatus.WON:
        return None

    accepted_offer = (
        Offer.query.join(ProposalVersion, Offer.proposal_version_id == ProposalVersion.id)
        .join(Proposal, ProposalVersion.proposal_id == Proposal.id)
        .filter(Proposal.lead_id == lead.id, Offer.status == OfferStatus.ACCEPTED)
        .first()
    )
    if accepted_offer is None:
        return None
    return project_for_offer(accepted_offer)
