class ProjectsDomainError(Exception):
    """Base for Projects domain-rule violations."""


class StructureError(ProjectsDomainError):
    """An invalid operation on the Section/WorkPackage/Task hierarchy."""


class ProjectValidationError(ProjectsDomainError):
    """Project/Customer fields don't satisfy the domain's budget rules."""
