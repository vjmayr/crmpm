import enum

from app.extensions import db


class CustomerType(enum.Enum):
    INTERNAL = "INTERNAL"
    EXTERNAL = "EXTERNAL"


class BudgetType(enum.Enum):
    HARD = "HARD"
    SOFT = "SOFT"


class OverHoursPolicy(enum.Enum):
    BLOCK = "BLOCK"
    BILL_AT_RATE = "BILL_AT_RATE"


class ProjectStatus(enum.Enum):
    ACTIVE = "ACTIVE"
    ON_HOLD = "ON_HOLD"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"


class TaskStatus(enum.Enum):
    TODO = "TODO"
    IN_PROGRESS = "IN_PROGRESS"
    DONE = "DONE"


class Customer(db.Model):
    __tablename__ = "customers"

    id = db.Column(db.Integer, primary_key=True)
    # Invariant #9: a Customer IS a promoted Organization — one per org, DB-enforced.
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=False, unique=True
    )
    type = db.Column(
        db.Enum(CustomerType, native_enum=False, length=20), nullable=False
    )

    organization = db.relationship("Organization")


class Project(db.Model):
    __tablename__ = "projects"
    __table_args__ = (
        # Invariant #11: HARD budgets must carry hours and a policy.
        db.CheckConstraint(
            "budget_type != 'HARD' OR "
            "(budget_hours IS NOT NULL AND over_hours_policy IS NOT NULL)",
            name="ck_projects_hard_budget_fields",
        ),
    )

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    customer_id = db.Column(db.Integer, db.ForeignKey("customers.id"), nullable=False)
    manager_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)
    offer_id = db.Column(
        db.Integer, db.ForeignKey("offers.id"), nullable=True, unique=True
    )
    budget_type = db.Column(
        db.Enum(BudgetType, native_enum=False, length=20), nullable=False
    )
    budget_hours = db.Column(db.Numeric(10, 2), nullable=True)
    over_hours_policy = db.Column(
        db.Enum(OverHoursPolicy, native_enum=False, length=20), nullable=True
    )
    over_rate = db.Column(db.Numeric(12, 2), nullable=True)
    status = db.Column(
        db.Enum(ProjectStatus, native_enum=False, length=20),
        nullable=False,
        default=ProjectStatus.ACTIVE,
        server_default=ProjectStatus.ACTIVE.value,
    )

    customer = db.relationship("Customer", backref="projects")
    manager = db.relationship("User")


# Strict 3-tier hierarchy (invariant #12), enforced structurally: Task carries
# ONLY work_package_id — there is no skip-level FK to Section or Project.
# `position` is contiguous 0..n-1 within each parent scope, maintained
# exclusively by app/projects/services.py (no DB unique constraint: position
# swaps would transiently collide without deferred constraints, which SQLite
# lacks — see DECISIONS.md).


class Section(db.Model):
    __tablename__ = "sections"

    id = db.Column(db.Integer, primary_key=True)
    project_id = db.Column(db.Integer, db.ForeignKey("projects.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    position = db.Column(db.Integer, nullable=False)

    project = db.relationship("Project", backref="sections")


class WorkPackage(db.Model):
    __tablename__ = "work_packages"

    id = db.Column(db.Integer, primary_key=True)
    section_id = db.Column(db.Integer, db.ForeignKey("sections.id"), nullable=False)
    name = db.Column(db.String(255), nullable=False)
    position = db.Column(db.Integer, nullable=False)
    estimated_hours = db.Column(db.Numeric(10, 2), nullable=True)

    section = db.relationship("Section", backref="work_packages")


class Task(db.Model):
    __tablename__ = "tasks"

    id = db.Column(db.Integer, primary_key=True)
    work_package_id = db.Column(
        db.Integer, db.ForeignKey("work_packages.id"), nullable=False
    )
    title = db.Column(db.String(255), nullable=False)
    # Plain editable field like Person.qualification_status — NOT service-managed
    # (CLAUDE.md rule #7 covers Lead and Offer status only).
    status = db.Column(
        db.Enum(TaskStatus, native_enum=False, length=20),
        nullable=False,
        default=TaskStatus.TODO,
        server_default=TaskStatus.TODO.value,
    )
    assignee_id = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=True)
    estimated_hours = db.Column(db.Numeric(10, 2), nullable=True)
    position = db.Column(db.Integer, nullable=False)

    work_package = db.relationship("WorkPackage", backref="tasks")
    assignee = db.relationship("User")
