from flask_wtf import FlaskForm
from wtforms import BooleanField, SelectField, StringField, SubmitField, TextAreaField
from wtforms.validators import DataRequired, Length, Optional, Regexp

from app.core.validators import EMAIL_REGEX
from app.crm.models import QualificationStatus


def coerce_optional_int(value):
    if value in (None, "", "None"):
        return None
    return int(value)


class OrganizationForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=255)])
    website = StringField("Website", validators=[Optional(), Length(max=255)])
    notes = TextAreaField("Notes", validators=[Optional()])
    submit = SubmitField("Save")


class PersonForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=255)])
    permission_to_contact = BooleanField("Permission to contact")
    email = StringField(
        "Email",
        validators=[Optional(), Regexp(EMAIL_REGEX, message="Enter a valid email address")],
    )
    phone = StringField("Phone", validators=[Optional(), Length(max=50)])
    organization_id = SelectField(
        "Organization", choices=[], coerce=coerce_optional_int, validators=[Optional()]
    )
    qualification_status = SelectField(
        "Qualification status",
        choices=[(s.value, s.value.title()) for s in QualificationStatus],
        default=QualificationStatus.NEW.value,
        validators=[DataRequired()],
    )
    submit = SubmitField("Save")


class LeadDiscoveryForm(FlaskForm):
    source = StringField("Source", validators=[Optional(), Length(max=255)])
    timeline = StringField("Timeline", validators=[Optional(), Length(max=255)])
    budget_range = StringField("Budget range", validators=[Optional(), Length(max=255)])
    pain_points = TextAreaField("Pain points", validators=[Optional()])
    discovery_notes = TextAreaField("Discovery notes", validators=[Optional()])
    submit = SubmitField("Save discovery notes")
