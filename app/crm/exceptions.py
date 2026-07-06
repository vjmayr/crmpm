class CrmDomainError(Exception):
    """Base for CRM domain-rule violations."""


class PromotionError(CrmDomainError):
    """A Person cannot be promoted to a Lead."""


class LeadStateError(CrmDomainError):
    """An invalid Lead status transition was requested."""


class ProposalError(CrmDomainError):
    """A proposal operation is not allowed for this lead's state."""


class ImmutableVersionError(CrmDomainError):
    """ProposalVersion rows are append-only and can never be updated or deleted."""


class EstimationValidationError(CrmDomainError):
    """An Estimation's fields don't satisfy its pricing model's requirements."""


class EstimationLockedError(CrmDomainError):
    """The Estimation's version is bound to a SENT offer and is read-only."""


class OfferStateError(CrmDomainError):
    """An invalid Offer status transition was requested (invariant #7)."""


class OfferConflictError(CrmDomainError):
    """The lead already has an open (DRAFT or SENT) offer."""
