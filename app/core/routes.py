from flask import redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from app.core import core_bp
from app.core.forms import LoginForm
from app.core.models import User


@core_bp.route("/")
@login_required
def dashboard():
    return render_template("core/dashboard.html")


@core_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("core.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        user = User.query.filter_by(email=form.email.data.strip().lower()).first()
        if user and user.check_password(form.password.data):
            login_user(user)
            next_page = request.args.get("next")
            return redirect(next_page or url_for("core.dashboard"))
        form.password.errors.append("Invalid email or password")

    return render_template("core/login.html", form=form)


@core_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("core.login"))
