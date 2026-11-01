from decimal import ROUND_UP, Decimal, InvalidOperation

SCALE = 1_000_000


def usd(value: str) -> int:
    try:
        amount = Decimal(value)
        if not amount.is_finite() or amount <= 0 or amount > 10000:
            raise ValueError("Сумма должна быть от 0.01 до 10000 USD")
        if amount != amount.quantize(Decimal("0.01")) or amount < Decimal("0.01"):
            raise ValueError("Укажите сумму с точностью до цента")
        return int(amount * SCALE)
    except InvalidOperation:
        raise ValueError("Неверная сумма") from None


def display(value: int) -> str:
    return f"{Decimal(value) / SCALE:.2f}"


def sale_price(product) -> int:
    if product.fixed_price is not None:
        return product.fixed_price
    raw = Decimal(product.cost) * (10000 + product.markup_bps) / 10000
    return int((raw / 10000).to_integral_value(rounding=ROUND_UP)) * 10000
