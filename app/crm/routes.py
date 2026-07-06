from flask import render_template
from flask_login import login_required

from app.crm import crm_bp


@crm_bp.route("/")
@login_required
def index():
    return render_template("crm/index.html")
