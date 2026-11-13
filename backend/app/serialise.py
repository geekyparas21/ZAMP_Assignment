"""Making values safe to store in a JSON column.

Reason-code evidence is assembled by many different rules, and it is easy for
a Decimal or a date to reach a JSON column by accident. SQLAlchemy only
discovers that at flush time, which fails the run *after* the decision has
been made - the worst possible moment.

So nothing is written to a JSON column without passing through here first.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


def json_safe(value: Any) -> Any:
    """Recursively convert a value into something json.dumps can handle.

    Decimals become strings rather than floats: this data is an audit trail,
    and 1/3 of a rupee is not worth a float's rounding behaviour.
    """
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.name
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    if hasattr(value, "to_dict"):
        return json_safe(value.to_dict())
    return str(value)
