# Decision Log

- 2026-07-06: `User` model's table is named `users`, not `user`. Reason: `user` is a reserved word in PostgreSQL. Future FKs to User (e.g. `manager_id`, `created_by`, `assignee_id`) must reference `users.id`.
- 2026-07-06: Login form validates email with a regex (`app/core/forms.py`), not WTForms' `Email()` validator. Reason: `Email()` requires the optional `email_validator` package, which is not in the approved dependency list.
- 2026-07-06: No PostgreSQL driver (`psycopg2`/`psycopg`) is installed yet. Reason: not in the approved dependency list. `ProductionConfig` reads `DATABASE_URL` but cannot connect to Postgres until a driver is added and approved — needed before any production deploy.
- 2026-07-06: `templates/` lives at the repo root, not Flask's default `app/templates/`. Reason: matches the Phase 0 kickoff spec's `templates/base.html` path; required an explicit `template_folder` override in `create_app()` (`app/__init__.py`).
