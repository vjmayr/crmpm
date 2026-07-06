"""Project structure services — all hierarchy mutations live here.

Positions are contiguous 0..n-1 per parent scope: creation appends, deletion
renumbers, moves swap adjacent siblings. No position is ever written outside
this module (there is deliberately no DB unique on (parent, position) — swaps
would transiently collide without deferred constraints; see DECISIONS.md).
Deletes are bottom-up only — no cascading deletes of structure.

Task.status is a plain editable field, NOT service-managed (CLAUDE.md rule #7
covers Lead and Offer status only).
"""

from app.extensions import db
from app.projects.exceptions import StructureError
from app.projects.models import Section, Task, WorkPackage

#: Parent FK attribute per hierarchy tier — the position scope.
_PARENT_ATTR = {Section: "project_id", WorkPackage: "section_id", Task: "work_package_id"}


def _parent_scope_filter(model, parent_id):
    return getattr(model, _PARENT_ATTR[model]) == parent_id


def _sibling_count(model, parent_id):
    return model.query.filter(_parent_scope_filter(model, parent_id)).count()


def _renumber(model, parent_id):
    siblings = (
        model.query.filter(_parent_scope_filter(model, parent_id))
        .order_by(model.position)
        .all()
    )
    for index, sibling in enumerate(siblings):
        sibling.position = index


# --- create (append at end of parent scope) ---------------------------------


def create_section(project, name):
    section = Section(
        project_id=project.id,
        name=name,
        position=_sibling_count(Section, project.id),
    )
    db.session.add(section)
    db.session.commit()
    return section


def create_work_package(section, name, estimated_hours=None):
    work_package = WorkPackage(
        section_id=section.id,
        name=name,
        estimated_hours=estimated_hours,
        position=_sibling_count(WorkPackage, section.id),
    )
    db.session.add(work_package)
    db.session.commit()
    return work_package


def create_task(work_package, title, estimated_hours=None, assignee=None):
    task = Task(
        work_package_id=work_package.id,
        title=title,
        estimated_hours=estimated_hours,
        assignee_id=assignee.id if assignee is not None else None,
        position=_sibling_count(Task, work_package.id),
    )
    db.session.add(task)
    db.session.commit()
    return task


# --- rename -------------------------------------------------------------------


def rename_section(section, name):
    section.name = name
    db.session.commit()
    return section


def rename_work_package(work_package, name):
    work_package.name = name
    db.session.commit()
    return work_package


def rename_task(task, title):
    task.title = title
    db.session.commit()
    return task


# --- delete (bottom-up only, renumber to close the gap) -----------------------


def delete_section(section):
    if WorkPackage.query.filter_by(section_id=section.id).count():
        raise StructureError(
            f"Section '{section.name}' still has work packages — delete them first."
        )
    parent_id = section.project_id
    db.session.delete(section)
    db.session.flush()
    _renumber(Section, parent_id)
    db.session.commit()


def delete_work_package(work_package):
    if Task.query.filter_by(work_package_id=work_package.id).count():
        raise StructureError(
            f"Work package '{work_package.name}' still has tasks — delete them first."
        )
    parent_id = work_package.section_id
    db.session.delete(work_package)
    db.session.flush()
    _renumber(WorkPackage, parent_id)
    db.session.commit()


def delete_task(task):
    parent_id = task.work_package_id
    db.session.delete(task)
    db.session.flush()
    _renumber(Task, parent_id)
    db.session.commit()


# --- move ---------------------------------------------------------------------


def move_item(item, direction):
    """Swap position with the adjacent sibling in the same parent scope.

    No-op at the edges. Never crosses parents.
    """
    if direction not in ("up", "down"):
        raise StructureError(f"Unknown move direction {direction!r} (up or down).")

    model = type(item)
    offset = -1 if direction == "up" else 1
    neighbor = model.query.filter(
        _parent_scope_filter(model, getattr(item, _PARENT_ATTR[model])),
        model.position == item.position + offset,
    ).first()
    if neighbor is None:
        return item  # already at the edge

    item.position, neighbor.position = neighbor.position, item.position
    db.session.commit()
    return item


def move_to_parent(item, new_parent):
    """Cross-parent moves are out of v1 scope — v2 candidate (see DECISIONS.md)."""
    raise StructureError(
        "Moving items to a different parent is not supported in v1. "
        "Delete and recreate, or wait for v2."
    )