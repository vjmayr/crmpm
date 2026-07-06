from flask_wtf import FlaskForm
from wtforms import PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Regexp

from app.core.validators import EMAIL_REGEX


class LoginForm(FlaskForm):
    email = StringField(
        "Email",
        validators=[DataRequired(), Regexp(EMAIL_REGEX, message="Enter a valid email address")],
    )
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Log in")
