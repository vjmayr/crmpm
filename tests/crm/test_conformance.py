import re
from pathlib import Path

APP_DIR = Path(__file__).resolve().parents[2] / "app"
SANCTIONED = APP_DIR / "crm" / "models.py"

# Catches the flag used as a query/filter expression: class-level column
# access, any filter()/filter_by() referencing it, or .is_() comparisons.
# Instance writes (person.permission_to_contact = ...) and form kwargs stay legal.
FORBIDDEN = re.compile(
    r"Person\.permission_to_contact"
    r"|filter(?:_by)?\([^)]*permission_to_contact"
    r"|permission_to_contact[^)\n]*\.is_\("
)


def test_contactable_lists_only_via_helper():
    offenders = []
    for path in sorted(APP_DIR.rglob("*.py")):
        if path == SANCTIONED:
            continue
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            if FORBIDDEN.search(line):
                offenders.append(f"{path.relative_to(APP_DIR.parent)}:{lineno}: {line.strip()}")

    assert not offenders, (
        "Invariant #1: contactable lists must be built via Person.contactable() "
        "(app/crm/models.py) — never by filtering permission_to_contact directly.\n"
        + "\n".join(offenders)
    )
