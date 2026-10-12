from __future__ import annotations

from decimal import ROUND_UP, Decimal


def usd_amount(unit_price: str, qty: int = 1) -> Decimal:
    if qty < 1:
        raise ValueError("qty must be >= 1")
    return (Decimal(str(unit_price)) * qty).quantize(Decimal("0.01"))


def format_usd(amount: str | Decimal, qty: int = 1) -> str:
    value = usd_amount(str(amount), qty)
    quantized = format(value, "f")
    if "." in quantized:
        quantized = quantized.rstrip("0").rstrip(".")
    return f"${quantized}"


def stars_from_usd(usd: str | Decimal, stars_per_usd: float = 20) -> int:
    stars = (Decimal(str(usd)) * Decimal(str(stars_per_usd))).to_integral_value(rounding=ROUND_UP)
    return max(1, int(stars))


def stars_price(unit_price: str, qty: int = 1, stars_per_usd: float = 20) -> int:
    return stars_from_usd(usd_amount(unit_price, qty), stars_per_usd)


def parse_usd(raw: str) -> str:
    text = raw.strip().replace("$", "").replace(",", "")
    value = Decimal(text)
    if value <= 0:
        raise ValueError("price must be greater than 0")
    return format(value.quantize(Decimal("0.01")), "f")


def parse_stars_per_usd(raw: str) -> str:
    text = raw.strip().replace(",", "")
    value = Decimal(text)
    if value <= 0:
        raise ValueError("stars per USD must be greater than 0")
    return format(value.quantize(Decimal("0.01")), "f")


def format_stars(amount: int) -> str:
    return f"{amount} Stars"
