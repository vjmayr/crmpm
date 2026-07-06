from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Regexp

EMAIL_PATTERN = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"


class LoginForm(FlaskForm):
    email = StringField(
        "Email",
        validators=[DataRequired(), Regexp(EMAIL_PATTERN, message="Enter a valid email address")],
    )
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Log in")
