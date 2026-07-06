class CrmDomainError(Exception):
    """Base for CRM domain-rule violations."""


class PromotionError(CrmDomainError):
    """A Person cannot be promoted to a Lead."""


class LeadStateError(CrmDomainError):
    """An invalid Lead status transition was requested."""
