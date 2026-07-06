"""CRM domain services — the ONLY code that changes Lead.status (CLAUDE.md #7).

Lead status transition ownership (ARCHITECTURE.md §4.2):
    OPEN        promote_to_lead() — this module (Phase 2)
    PROPOSAL    create_proposal() — this module (Phase 3); fires once, from OPEN only
                deny_offer() — this module (Phase 4); reverts OFFER_SENT -> PROPOSAL
                for renegotiation (D4, see DECISIONS.md)
    OFFER_SENT  send_offer() — this module (Phase 4)
    WON         accept_offer() in app/services/offer_acceptance.py — Phase 4
    LOST        close_lead() — this module; explicit user action ONLY (D4)

Offer status transitions (§4.1) are owned by create_offer/send_offer/deny_offer
here and accept_offer in app/services/offer_acceptance.py. ACCEPTED and DENIED
are terminal (invariant #7).
"""

from datetime import datetime, timezone

from app.crm.exceptions import (
    EstimationLockedError,
    EstimationValidationError,
    LeadStateError,
    OfferConflictError,
    OfferStateError,
    PromotionError,
    ProposalError,
)
from app.crm.models import (
    Estimation,
    Lead,
    LeadStatus,
    Offer,
    OfferStatus,
    PricingModel,
    Proposal,
    ProposalVersion,
    QualificationStatus,
)
from app.extensions import db

#: WON and LOST are terminal (§4.1/§4.2); no transition ever leaves them.
TERMINAL_STATUSES = frozenset({LeadStatus.WON, LeadStatus.LOST})


def promote_to_lead(person):
    """Promote a QUALIFIED Person to an OPEN Lead.

    permission_to_contact is deliberately NOT a guard here: the flag governs
    contact affordances, not pipeline membership. A qualified prospect whose
    permission is still pending may be tracked as a Lead (see DECISIONS.md).
    """
    if person.qualification_status != QualificationStatus.QUALIFIED:
        raise PromotionError(
            f"{person.name} is {person.qualification_status.value}, not QUALIFIED."
        )
    if Lead.query.filter_by(person_id=person.id).first() is not None:
        raise PromotionError(f"{person.name} already has a lead (one per person).")

    lead = Lead(person_id=person.id)
    db.session.add(lead)
    db.session.commit()
    return lead


def close_lead(lead):
    """Mark a Lead LOST. The ONLY code path that ever sets LOST (D4, invariant #10).

    Must only be triggered by an explicit user action — never call this from
    another service. A DENIED offer does not close its lead.
    """
    if lead.status in TERMINAL_STATUSES:
        raise LeadStateError(
            f"Lead {lead.id} is {lead.status.value} (terminal) and cannot be closed."
        )

    lead.status = LeadStatus.LOST
    db.session.commit()
    return lead


#: An offer that ever left DRAFT freezes its version's estimation forever —
#: DENIED offers are historical records (invariant #6).
_LOCKING_OFFER_STATUSES = (OfferStatus.SENT, OfferStatus.ACCEPTED, OfferStatus.DENIED)


def estimation_is_locked(estimation):
    """Invariant #6: locked iff the estimation's version has any offer ever sent.

    A DRAFT offer does not lock; SENT, ACCEPTED, and DENIED all do — a DENIED
    offer keeps its version's pricing frozen as part of the historical record.
    """
    return (
        Offer.query.filter(
            Offer.proposal_version_id == estimation.proposal_version_id,
            Offer.status.in_(_LOCKING_OFFER_STATUSES),
        ).first()
        is not None
    )


def _require_lead_not_terminal(lead, action):
    if lead.status in TERMINAL_STATUSES:
        raise ProposalError(
            f"Cannot {action}: lead {lead.id} is {lead.status.value} (terminal)."
        )


def create_proposal(lead, title, content, created_by):
    """Create a Proposal with version 1, atomically.

    Transitions the lead OPEN -> PROPOSAL only if currently OPEN: the
    transition fires once per lead (§4.2); later proposals (e.g. after a
    denied offer) never regress the status.
    """
    _require_lead_not_terminal(lead, "create a proposal")

    proposal = Proposal(lead_id=lead.id, title=title)
    db.session.add(proposal)
    db.session.flush()
    version = ProposalVersion(
        proposal_id=proposal.id,
        version_number=1,
        content=content,
        created_by=created_by.id,
    )
    db.session.add(version)
    if lead.status == LeadStatus.OPEN:
        lead.status = LeadStatus.PROPOSAL
    db.session.commit()
    return proposal


def add_version(proposal, content, created_by, copy_estimation=True):
    """Append the next version. Versions are never edited — only added.

    Copy-forward rule (see DECISIONS.md): with copy_estimation=True, the
    latest version's Estimation is duplicated as a NEW row on the new
    version, so pricing evolves by editing the copy. Estimation rows are
    never re-parented or shared across versions.
    """
    _require_lead_not_terminal(proposal.lead, "add a version")

    latest = (
        ProposalVersion.query.filter_by(proposal_id=proposal.id)
        .order_by(ProposalVersion.version_number.desc())
        .first()
    )
    # max+1; the (proposal_id, version_number) unique constraint is the race backstop.
    next_number = (latest.version_number if latest else 0) + 1

    version = ProposalVersion(
        proposal_id=proposal.id,
        version_number=next_number,
        content=content,
        created_by=created_by.id,
    )
    db.session.add(version)

    if copy_estimation and latest is not None and latest.estimation is not None:
        db.session.flush()
        source = latest.estimation
        db.session.add(
            Estimation(
                proposal_version_id=version.id,
                pricing_model=source.pricing_model,
                fixed_price=source.fixed_price,
                rate_amount=source.rate_amount,
                rate_unit=source.rate_unit,
                estimated_units=source.estimated_units,
                additional_rate=source.additional_rate,
            )
        )

    db.session.commit()
    return version


_ESTIMATION_FIELDS = (
    "pricing_model",
    "fixed_price",
    "rate_amount",
    "rate_unit",
    "estimated_units",
    "additional_rate",
)
_TIME_BASED_REQUIRED = ("rate_amount", "rate_unit", "estimated_units")


def _validate_estimation_state(effective):
    model = effective["pricing_model"]
    if model == PricingModel.FIXED:
        missing = [] if effective["fixed_price"] is not None else ["fixed_price"]
        forbidden = [
            f
            for f in (*_TIME_BASED_REQUIRED, "additional_rate")
            if effective[f] is not None
        ]
    elif model == PricingModel.TIME_BASED:
        missing = [f for f in _TIME_BASED_REQUIRED if effective[f] is None]
        forbidden = ["fixed_price"] if effective["fixed_price"] is not None else []
    else:
        raise EstimationValidationError("pricing_model is required (FIXED or TIME_BASED).")

    if missing or forbidden:
        parts = []
        if missing:
            parts.append(f"missing for {model.value}: {', '.join(missing)}")
        if forbidden:
            parts.append(
                f"forbidden for {model.value}: {', '.join(forbidden)} "
                "(clear them explicitly with None)"
            )
        raise EstimationValidationError("; ".join(parts))


def set_estimation(version, **fields):
    """Create or update the version's Estimation (invariant #4: at most one).

    Validation runs on the merged final state (existing values overlaid with
    the given fields), so switching pricing_model requires explicitly passing
    None for the now-forbidden fields.
    """
    unknown = set(fields) - set(_ESTIMATION_FIELDS)
    if unknown:
        raise EstimationValidationError(f"Unknown fields: {', '.join(sorted(unknown))}")

    estimation = version.estimation
    if estimation is not None and estimation_is_locked(estimation):
        raise EstimationLockedError(
            f"Estimation {estimation.id} is bound to a SENT offer and is read-only."
        )

    effective = {
        f: fields[f] if f in fields else (getattr(estimation, f) if estimation else None)
        for f in _ESTIMATION_FIELDS
    }
    _validate_estimation_state(effective)

    if estimation is None:
        estimation = Estimation(proposal_version_id=version.id, **effective)
        db.session.add(estimation)
    else:
        for field, value in effective.items():
            setattr(estimation, field, value)

    db.session.commit()
    return estimation


def create_offer(proposal_version):
    """Create a DRAFT offer bound to a specific proposal version (D7).

    At most one open offer (DRAFT or SENT) may exist per lead at a time —
    parallel contradictory offers are prevented (see DECISIONS.md). A new
    offer after a DENIED one is the D4 renegotiation loop.
    """
    lead = proposal_version.proposal.lead
    if lead.status in TERMINAL_STATUSES:
        raise OfferStateError(
            f"Cannot create an offer: lead {lead.id} is {lead.status.value} (terminal)."
        )

    open_offer = (
        Offer.query.join(ProposalVersion, Offer.proposal_version_id == ProposalVersion.id)
        .join(Proposal, ProposalVersion.proposal_id == Proposal.id)
        .filter(
            Proposal.lead_id == lead.id,
            Offer.status.in_((OfferStatus.DRAFT, OfferStatus.SENT)),
        )
        .first()
    )
    if open_offer is not None:
        raise OfferConflictError(
            f"Lead {lead.id} already has an open offer "
            f"(offer {open_offer.id}, {open_offer.status.value}). "
            "Resolve it before creating another."
        )

    offer = Offer(proposal_version_id=proposal_version.id)
    db.session.add(offer)
    db.session.commit()
    return offer


def send_offer(offer):
    """DRAFT -> SENT. Requires an Estimation on the bound version (invariant #5)."""
    if offer.status != OfferStatus.DRAFT:
        raise OfferStateError(
            f"Offer {offer.id} is {offer.status.value}; only DRAFT offers can be sent."
        )
    if offer.version.estimation is None:
        raise OfferStateError(
            f"Offer {offer.id} cannot be sent: version "
            f"{offer.version.version_number} has no estimation (invariant #5)."
        )

    offer.status = OfferStatus.SENT
    offer.sent_at = datetime.now(timezone.utc)
    lead = offer.version.proposal.lead
    if lead.status == LeadStatus.PROPOSAL:
        lead.status = LeadStatus.OFFER_SENT
    db.session.commit()
    return offer


def deny_offer(offer):
    """SENT -> DENIED (terminal for THIS offer only, D4).

    The lead reverts OFFER_SENT -> PROPOSAL for renegotiation; it never
    auto-transitions to LOST (invariant #10) and a LOST lead stays LOST.
    """
    if offer.status != OfferStatus.SENT:
        raise OfferStateError(
            f"Offer {offer.id} is {offer.status.value}; only SENT offers can be denied."
        )

    offer.status = OfferStatus.DENIED
    offer.decided_at = datetime.now(timezone.utc)
    lead = offer.version.proposal.lead
    if lead.status == LeadStatus.OFFER_SENT:
        lead.status = LeadStatus.PROPOSAL
    db.session.commit()
    return offer
