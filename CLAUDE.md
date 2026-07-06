# CLAUDE.md — Standing Orders

Lightweight CRM & Project Management Tool. Single developer, token-conscious workflow.
**`ARCHITECTURE.md` is the single source of truth. Read it before any task. Do not duplicate its content here.**

## Non-negotiable rules

1. **Invariants are law.** Never write or modify code that violates the twelve invariants in `ARCHITECTURE.md` §6. If a requested change would violate one, stop and say so instead of implementing.
2. **Scope discipline.** Implement only what the current phase (§5) specifies. No speculative features, no "while we're at it" additions. v2 items (TimeEntry, budget guard) must not be built in v1 — schema fields for them already exist and are sufficient.
3. **Test-first for critical logic.** Phases 3 and 4 (proposal versioning, Offer FSM, `offer_accepted()`): write pytest tests from the §6 invariants *before* implementation. Do not stop until the suite is green.
4. **Domain seam.** CRM and Projects modules never import each other's models directly for writes. All cross-domain effects go through `app/services/`.
5. **Dependencies.** Ask before adding any new package. Current approved set: Flask, SQLAlchemy, Alembic (Flask-Migrate), Flask-Login, Flask-WTF, pytest, python-dotenv.
6. **Frontend.** HTMX partials over custom JavaScript, always. Tailwind utility classes only — no custom CSS files unless unavoidable. No frontend build pipeline in v1.
7. **State transitions.** Lead status and Offer status are changed ONLY by service-layer functions, never by direct field assignment in routes or templates.
8. **Database.** All schema changes go through Alembic migrations. SQLite in dev, but write PostgreSQL-compatible code (no SQLite-only features). Use `Decimal`/`Numeric` for all money and hours — never floats.

## Conventions

- Blueprints: `core`, `crm`, `projects`. Templates namespaced per blueprint (`templates/crm/...`).
- HTMX endpoints return partials from `templates/<blueprint>/partials/`.
- Enums: Python `enum.Enum` classes stored as strings (PostgreSQL-friendly, readable in DB).
- Timestamps: UTC, timezone-aware.
- Tests mirror app structure: `tests/crm/`, `tests/projects/`, `tests/services/`.
- Commits: one logical change per commit; tag phase completions (`phase-0`, `phase-1`, ...).
- Contactable lists: only via `Person.contactable()` — never filter `permission_to_contact` directly (enforced by `tests/crm/test_conformance.py`). `Organization.people` is an unfiltered roster; never use it to drive contact actions.

## Decision log

- If a session ends with any decision that deviates from or extends `ARCHITECTURE.md`, append one line to `DECISIONS.md`: date, decision, reason. Never silently diverge from the blueprint.
