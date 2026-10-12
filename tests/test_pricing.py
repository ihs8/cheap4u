import pytest

from bot.pricing import format_stars, format_usd, parse_stars_per_usd, parse_usd, stars_price, usd_amount


def test_usd_amount() -> None:
    assert str(usd_amount("22.500000", qty=1)) == "22.50"
    assert str(usd_amount("22.5", qty=2)) == "45.00"


def test_format_usd() -> None:
    assert format_usd("25.000000") == "$25"
    assert format_usd("0.73") == "$0.73"
    assert format_usd("14", qty=2) == "$28"


def test_stars_from_retail_usd() -> None:
    assert stars_price("25", qty=1, stars_per_usd=20) == 500
    assert stars_price("0.73", qty=1, stars_per_usd=20) == 15


def test_stars_price_qty() -> None:
    one = stars_price("10", qty=1, stars_per_usd=10)
    two = stars_price("10", qty=2, stars_per_usd=10)
    assert two == one * 2


def test_stars_price_minimum_one() -> None:
    assert stars_price("0.001", qty=1, stars_per_usd=1) == 1


def test_format_stars() -> None:
    assert format_stars(50) == "50 Stars"


def test_parse_usd() -> None:
    assert parse_usd("$1.99") == "1.99"
    assert parse_usd("12") == "12.00"
    with pytest.raises(ValueError):
        parse_usd("0")


def test_parse_stars_per_usd() -> None:
    assert parse_stars_per_usd("50") == "50.00"
    assert parse_stars_per_usd("20.5") == "20.50"
    with pytest.raises(ValueError):
        parse_stars_per_usd("0")
