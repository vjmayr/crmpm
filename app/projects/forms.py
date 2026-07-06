from flask_wtf import FlaskForm
from wtforms import SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional

from app.core.validators import valid_decimal_string
from app.projects.models import BudgetType, CustomerType, OverHoursPolicy


class CustomerForm(FlaskForm):
    organization_id = SelectField("Organization", coerce=int, validators=[DataRequired()])
    type = SelectField(
        "Type",
        choices=[(t.value, t.value.title()) for t in CustomerType],
        default=CustomerType.EXTERNAL.value,
        validators=[DataRequired()],
    )
    submit = SubmitField("Create customer")


class ProjectForm(FlaskForm):
    name = StringField("Name", validators=[DataRequired(), Length(max=255)])
    customer_id = SelectField("Customer", coerce=int, validators=[DataRequired()])
    budget_type = SelectField(
        "Budget type",
        choices=[(t.value, t.value.title()) for t in BudgetType],
        default=BudgetType.SOFT.value,
        validators=[DataRequired()],
    )
    budget_hours = StringField(
        "Budget hours (required for HARD)", validators=[Optional(), valid_decimal_string]
    )
    over_hours_policy = SelectField(
        "Over-hours policy (required for HARD)",
        choices=[("", "—")] + [(p.value, p.value.replace("_", " ").title()) for p in OverHoursPolicy],
        validators=[Optional()],
    )
    over_rate = StringField(
        "Over rate (required for BILL_AT_RATE)",
        validators=[Optional(), valid_decimal_string],
    )
    submit = SubmitField("Save")
