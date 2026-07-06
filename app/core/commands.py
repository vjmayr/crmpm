import click
from flask.cli import with_appcontext

from app.core.models import User
from app.extensions import db


@click.command("create-user")
@click.option("--email", prompt=True)
@click.option("--password", prompt=True, hide_input=True, confirmation_prompt=True)
@click.option("--name", prompt=True, default="Admin")
@with_appcontext
def create_user_command(email, password, name):
    """Create the single application account, or reset it if it already exists."""
    email = email.strip().lower()
    user = User.query.filter_by(email=email).first()

    if user is None:
        user = User(email=email, name=name)
        db.session.add(user)
        action = "Created"
    else:
        user.name = name
        action = "Reset"

    user.set_password(password)
    db.session.commit()
    click.echo(f"{action} user {email}")


def register_commands(app):
    app.cli.add_command(create_user_command)
