import enum
from datetime import datetime, timezone

from sqlalchemy import event, false

from app.crm.exceptions import ImmutableVersionError
from app.extensions import db


class QualificationStatus(enum.Enum):
    NEW = "NEW"
    CONTACTED = "CONTACTED"
    QUALIFIED = "QUALIFIED"
    DISQUALIFIED = "DISQUALIFIED"


class LeadStatus(enum.Enum):
    OPEN = "OPEN"
    PROPOSAL = "PROPOSAL"
    OFFER_SENT = "OFFER_SENT"
    WON = "WON"
    LOST = "LOST"


class PricingModel(enum.Enum):
    FIXED = "FIXED"
    TIME_BASED = "TIME_BASED"


class RateUnit(enum.Enum):
    HOURLY = "HOURLY"
    DAILY = "DAILY"


class Organization(db.Model):
    __tablename__ = "organizations"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), unique=True, nullable=False)
    website = db.Column(db.String(255), nullable=True)
    notes = db.Column(db.Text, nullable=True)

    people = db.relationship("Person", back_populates="organization")


class Person(db.Model):
    __tablename__ = "people"

    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(255), nullable=False)
    email = db.Column(db.String(255), nullable=True)
    phone = db.Column(db.String(50), nullable=True)
    organization_id = db.Column(
        db.Integer, db.ForeignKey("organizations.id"), nullable=True
    )
    qualification_status = db.Column(
        db.Enum(QualificationStatus, native_enum=False, length=20),
        nullable=False,
        default=QualificationStatus.NEW,
        server_default=QualificationStatus.NEW.value,
    )
    # Invariant #1: opt-in is strict — False at the database level, not just the form.
    permission_to_contact = db.Column(
        db.Boolean, nullable=False, default=False, server_default=false()
    )
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    organization = db.relationship("Organization", back_populates="people")

    @classmethod
    def contactable(cls):
        """The only sanctioned way to build a 'contactable' list of people."""
        return cls.query.filter(cls.permission_to_contact.is_(True))


class Lead(db.Model):
    __tablename__ = "leads"

    id = db.Column(db.Integer, primary_key=True)
    # Invariant #2: a Person is promoted at most once — unique at the DB level.
    person_id = db.Column(
        db.Integer, db.ForeignKey("people.id"), nullable=False, unique=True
    )
    # Status is written ONLY by services (CLAUDE.md rule #7); see app/crm/services.py.
    status = db.Column(
        db.Enum(LeadStatus, native_enum=False, length=20),
        nullable=False,
        default=LeadStatus.OPEN,
        server_default=LeadStatus.OPEN.value,
    )
    source = db.Column(db.String(255), nullable=True)
    pain_points = db.Column(db.Text, nullable=True)
    timeline = db.Column(db.String(255), nullable=True)
    budget_range = db.Column(db.String(255), nullable=True)
    discovery_notes = db.Column(db.Text, nullable=True)
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    person = db.relationship(
        "Person", backref=db.backref("lead", uselist=False)
    )


class Proposal(db.Model):
    """Container only — content lives exclusively in ProposalVersion rows (§3.1)."""

    __tablename__ = "proposals"

    id = db.Column(db.Integer, primary_key=True)
    lead_id = db.Column(db.Integer, db.ForeignKey("leads.id"), nullable=False)
    title = db.Column(db.String(255), nullable=False)

    lead = db.relationship("Lead", backref="proposals")
    versions = db.relationship(
        "ProposalVersion",
        back_populates="proposal",
        order_by="ProposalVersion.version_number",
    )


class ProposalVersion(db.Model):
    """Append-only, rows immutable (invariant #3) — enforced by ORM event
    listeners, not convention. No exceptions: if a typo needs fixing, the fix
    is a new version.
    """

    __tablename__ = "proposal_versions"
    __table_args__ = (db.UniqueConstraint("proposal_id", "version_number"),)

    id = db.Column(db.Integer, primary_key=True)
    proposal_id = db.Column(db.Integer, db.ForeignKey("proposals.id"), nullable=False)
    version_number = db.Column(db.Integer, nullable=False)
    content = db.Column(db.Text, nullable=False)  # markdown
    created_at = db.Column(
        db.DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    created_by = db.Column(db.Integer, db.ForeignKey("users.id"), nullable=False)

    proposal = db.relationship("Proposal", back_populates="versions")
    estimation = db.relationship(
        "Estimation", back_populates="version", uselist=False
    )
    creator = db.relationship("User")


@event.listens_for(ProposalVersion, "before_update")
def _reject_proposal_version_update(mapper, connection, target):
    raise ImmutableVersionError(
        f"ProposalVersion {target.id} (v{target.version_number}) is immutable — "
        "create a new version instead (invariant #3)."
    )


@event.listens_for(ProposalVersion, "before_delete")
def _reject_proposal_version_delete(mapper, connection, target):
    raise ImmutableVersionError(
        f"ProposalVersion {target.id} (v{target.version_number}) is append-only "
        "and cannot be deleted (invariant #3)."
    )


class Estimation(db.Model):
    __tablename__ = "estimations"

    id = db.Column(db.Integer, primary_key=True)
    # Invariant #4: exactly one Estimation per version — unique at the DB level.
    proposal_version_id = db.Column(
        db.Integer, db.ForeignKey("proposal_versions.id"), nullable=False, unique=True
    )
    pricing_model = db.Column(
        db.Enum(PricingModel, native_enum=False, length=20), nullable=False
    )
    # FIXED:
    fixed_price = db.Column(db.Numeric(12, 2), nullable=True)
    # TIME_BASED:
    rate_amount = db.Column(db.Numeric(12, 2), nullable=True)
    rate_unit = db.Column(
        db.Enum(RateUnit, native_enum=False, length=20), nullable=True
    )
    estimated_units = db.Column(db.Numeric(10, 2), nullable=True)
    additional_rate = db.Column(db.Numeric(12, 2), nullable=True)

    version = db.relationship("ProposalVersion", back_populates="estimation")
