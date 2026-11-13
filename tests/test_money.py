"""Indian digit grouping, used everywhere an amount reaches a human."""

from __future__ import annotations

from decimal import Decimal

import pytest

from backend.app.money import format_inr

D = Decimal


@pytest.mark.parametrize("value,expected", [
    (D("0"), "0.00"),
    (D("180"), "180.00"),
    (D("1000"), "1,000.00"),
    (D("99999"), "99,999.00"),
    (D("107026"), "1,07,026.00"),          # one lakh, not 107,026
    (D("501500"), "5,01,500.00"),
    (D("10000000"), "1,00,00,000.00"),     # one crore
    (D("72570.50"), "72,570.50"),
    (D("-50150"), "-50,150.00"),
])
def test_indian_grouping(value, expected):
    assert format_inr(value) == expected


def test_none_renders_as_a_dash_not_a_crash():
    assert format_inr(None) == "-"


def test_rounds_to_paise():
    assert format_inr(D("107026.005")) == "1,07,026.01"


def test_accepts_ints_and_floats():
    assert format_inr(107026) == "1,07,026.00"
    assert format_inr(107026.0) == "1,07,026.00"
