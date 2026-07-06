"""Read-only roll-up queries for the project hierarchy.

The WP-level and task-level sums are computed and returned independently and
are never coalesced: WP numbers are the planning estimate, task numbers are
the decomposition — their divergence is signal, not error (see DECISIONS.md).
NULL estimated_hours count as 0 in sums but are reported separately as
unestimated_* counts so the UI can render them distinguishably.
"""

from decimal import Decimal

from sqlalchemy import case, func

from app.extensions import db
from app.projects.models import Section, Task, WorkPackage

ZERO = Decimal("0")


def _unestimated_count(column):
    return func.count(case((column.is_(None), 1)))


def project_rollup(project):
    """Per-section and project-total roll-ups in a constant three queries."""
    sections = (
        Section.query.filter_by(project_id=project.id)
        .order_by(Section.position)
        .all()
    )

    wp_rows = (
        db.session.query(
            WorkPackage.section_id,
            func.sum(WorkPackage.estimated_hours),
            _unestimated_count(WorkPackage.estimated_hours),
        )
        .join(Section, WorkPackage.section_id == Section.id)
        .filter(Section.project_id == project.id)
        .group_by(WorkPackage.section_id)
        .all()
    )
    wp_by_section = {row[0]: (row[1] or ZERO, row[2]) for row in wp_rows}

    task_rows = (
        db.session.query(
            WorkPackage.section_id,
            func.sum(Task.estimated_hours),
            _unestimated_count(Task.estimated_hours),
        )
        .join(WorkPackage, Task.work_package_id == WorkPackage.id)
        .join(Section, WorkPackage.section_id == Section.id)
        .filter(Section.project_id == project.id)
        .group_by(WorkPackage.section_id)
        .all()
    )
    tasks_by_section = {row[0]: (row[1] or ZERO, row[2]) for row in task_rows}

    section_entries = []
    for section in sections:
        wp_hours, unestimated_wps = wp_by_section.get(section.id, (ZERO, 0))
        task_hours, unestimated_tasks = tasks_by_section.get(section.id, (ZERO, 0))
        section_entries.append(
            {
                "section": section,
                "wp_hours": wp_hours,
                "task_hours": task_hours,
                "unestimated_wps": unestimated_wps,
                "unestimated_tasks": unestimated_tasks,
            }
        )

    wp_total = sum((entry["wp_hours"] for entry in section_entries), ZERO)
    task_total = sum((entry["task_hours"] for entry in section_entries), ZERO)
    budget = project.budget_hours

    return {
        "sections": section_entries,
        "project": {
            "wp_hours": wp_total,
            "task_hours": task_total,
            "unestimated_wps": sum(e["unestimated_wps"] for e in section_entries),
            "unestimated_tasks": sum(e["unestimated_tasks"] for e in section_entries),
            "budget_hours": budget,
            "wp_delta": (budget - wp_total) if budget is not None else None,
            "task_delta": (budget - task_total) if budget is not None else None,
        },
    }
