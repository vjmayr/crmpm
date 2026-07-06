import re
from decimal import Decimal, InvalidOperation

from wtforms.validators import ValidationError

EMAIL_REGEX = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def valid_decimal_string(form, field):
    """WTForms validator: empty is fine (pair with Optional()), else a Decimal."""
    value = (field.data or "").strip()
    if not value:
        return
    try:
        Decimal(value)
    except InvalidOperation as exc:
        raise ValidationError("Enter a valid number.") from exc
