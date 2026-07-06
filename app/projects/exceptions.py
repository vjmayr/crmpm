class ProjectsDomainError(Exception):
    """Base for Projects domain-rule violations."""


class StructureError(ProjectsDomainError):
    """An invalid operation on the Section/WorkPackage/Task hierarchy."""
