"""Amount formatting.

One function, used everywhere an amount reaches a human, so the numbers in a
reason code, on the decision card and on the invoice PDF are grouped the same
way. Indian digit grouping (1,07,026.00) is not what Python's ',' format spec
produces, and a finance tool that mixes the two looks broken to the people who
use it.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

CENT = Decimal("0.01")


def format_inr(value: Decimal | int | float | None) -> str:
    """Format an amount with Indian digit grouping: 1,07,026.00

    The last three digits are grouped together, then every two digits before
    that (thousand, lakh, crore). Rounds half UP, as money conventionally does -
    Python's default banker's rounding would turn 0.005 into 0.00.
    """
    if value is None:
        return "-"
    amount = Decimal(str(value)).quantize(CENT, rounding=ROUND_HALF_UP)
    sign = "-" if amount < 0 else ""
    whole, _, frac = f"{abs(amount):.2f}".partition(".")

    if len(whole) > 3:
        head, tail = whole[:-3], whole[-3:]
        groups = []
        while len(head) > 2:
            groups.insert(0, head[-2:])
            head = head[:-2]
        if head:
            groups.insert(0, head)
        whole = ",".join(groups + [tail])

    return f"{sign}{whole}.{frac}"
