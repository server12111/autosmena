from decimal import Decimal, InvalidOperation
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.exc import OperationalError

from app import db
from app.config import settings
from app.models import AppSetting, Audit, Product


def supplier_pricing():
    try:
        with db.Session() as session:
            url = session.get_bind().url
            if (
                url.get_backend_name() == "sqlite"
                and url.database not in (None, "", ":memory:")
                and not Path(url.database).exists()
            ):
                return {"currency": "", "usd_rate": settings.apicc_usd_rate}
            values = {row.key: row.value for row in session.scalars(select(AppSetting))}
    except OperationalError:
        values = {}
    return {
        "currency": values.get("supplier_currency", ""),
        "usd_rate": values.get("supplier_usd_rate", settings.apicc_usd_rate),
    }


def supplier_rate():
    try:
        rate = Decimal(supplier_pricing()["usd_rate"])
        return rate if rate.is_finite() and rate > 0 else None
    except (InvalidOperation, ValueError):
        return None


def save_supplier_pricing(currency, value):
    try:
        rate = Decimal(value)
        if not rate.is_finite() or not 0 < rate <= 100000:
            raise ValueError("Неверный курс")
        if currency in ("USD", "USDT") and rate != 1:
            raise ValueError("Для USD/USDT здесь используется курс 1:1")
    except InvalidOperation:
        raise ValueError("Неверный курс") from None
    with db.Session.begin() as session:
        old = session.get(AppSetting, "supplier_usd_rate")
        changed = old is None or Decimal(old.value) != rate
        for key, value in {"supplier_currency": currency, "supplier_usd_rate": str(rate)}.items():
            row = session.get(AppSetting, key)
            if row:
                row.value = value
            else:
                session.add(AppSetting(key=key, value=value))
        if changed:
            for product in session.scalars(select(Product).where(~Product.supplier_code.startswith("demo:"))):
                product.enabled = False
        session.add(Audit(action="supplier.currency", detail=f"currency={currency}; usd_rate={rate}"))
