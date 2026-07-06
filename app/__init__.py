import os

from flask import Flask

from app.extensions import csrf, db, login_manager, migrate
from config import config

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def create_app(config_name=None):
    config_name = config_name or os.environ.get("FLASK_CONFIG", "default")

    app = Flask(
        __name__,
        instance_relative_config=True,
        template_folder=os.path.join(PROJECT_ROOT, "templates"),
    )
    app.config.from_object(config[config_name])

    os.makedirs(app.instance_path, exist_ok=True)

    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    csrf.init_app(app)

    from app.core import core_bp
    from app.crm import crm_bp
    from app.projects import projects_bp

    app.register_blueprint(core_bp)
    app.register_blueprint(crm_bp, url_prefix="/crm")
    app.register_blueprint(projects_bp, url_prefix="/projects")

    from app.core import models  # noqa: F401  (registers User + user_loader)
    from app.crm import models  # noqa: F401  (registers CRM domain models)
    from app.projects import models  # noqa: F401,F811  (registers Customer, Project)
    from app.core.commands import register_commands

    register_commands(app)

    return app
