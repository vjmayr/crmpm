from flask import render_template
from flask_login import login_required

from app.projects import projects_bp
from app.projects.models import Project


@projects_bp.route("/")
@login_required
def index():
    return render_template("projects/index.html")


@projects_bp.route("/<int:project_id>")
@login_required
def project_detail(project_id):
    project = Project.query.get_or_404(project_id)
    return render_template("projects/detail.html", project=project)
