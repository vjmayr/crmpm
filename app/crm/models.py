import enum
from datetime import datetime, timezone

from sqlalchemy import false

from app.extensions import db


class QualificationStatus(enum.Enum):
    NEW = "NEW"
    CONTACTED = "CONTACTED"
    QUALIFIED = "QUALIFIED"
    DISQUALIFIED = "DISQUALIFIED"


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
