"""CRM domain services — the ONLY code that changes Lead.status (CLAUDE.md #7).

Lead status transition ownership (ARCHITECTURE.md §4.2):
    OPEN        promote_to_lead() — this module (Phase 2)
    PROPOSAL    proposal-creation service — Phase 3
    OFFER_SENT  offer send() service — Phase 4
    WON         offer_accepted() in app/services/ — Phase 4
    LOST        close_lead() — this module; explicit user action ONLY (D4)
"""

from app.crm.exceptions import LeadStateError, PromotionError
from app.crm.models import Lead, LeadStatus, QualificationStatus
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
