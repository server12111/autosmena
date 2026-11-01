"""api.cc adapter based on docs/apicc.openapi.json.

Rejected is reserved for failures proven to occur before a charged operation.
Any ambiguous result is held for reconciliation instead of retried.
"""

import json
import time
from dataclasses import dataclass, field
from decimal import ROUND_UP, Decimal

import httpx

from app.config import settings

RENTAL_EXPIRY = 5  # UI: "Більше 60 днів"; OpenAPI: 60–80 days remaining.
RENTAL_LABEL = "Больше 60 дней (диапазон поставщика 60–80)"


class Rejected(Exception):
    pass


@dataclass
class Allocation:
    external_id: str
    phone: str
    payload: dict = field(default_factory=dict)


@dataclass
class State:
    status: str
    sms: str = ""
    payload: dict = field(default_factory=dict)


class DemoSupplier:
    ready = True
    can_cancel = True

    async def allocate(self, code, request_id):
        return Allocation("demo-" + request_id, "+1555000" + str(int(request_id[:6], 16) % 10000).zfill(4))

    async def status(self, order):
        from app.models import now

        return State("completed", "DEMO: 123456") if now() - order.created_at >= 45 else State("waiting")

    async def cancel(self, order):
        state = await self.status(order)
        return state if state.status == "completed" else State("cancelled")


class ApiccSupplier:
    can_cancel = False

    @property
    def ready(self):
        try:
            rate = Decimal(settings.apicc_usd_rate)
            return bool(settings.apicc_api_key) and rate.is_finite() and rate > 0
        except Exception:
            return False

    async def call(self, path, body=None):
        if not settings.apicc_api_key:
            raise ValueError("Укажите APICC_API_KEY в .env")
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.request(
                "GET" if body is None else "POST",
                settings.apicc_url + path,
                headers={"Authorization": "Bearer " + settings.apicc_api_key},
                **({"json": body} if body is not None else {}),
            )
            response.raise_for_status()
            result = json.loads(response.text, parse_float=Decimal)
        if result.get("code") != 1:
            # A business failure on a charged operation is held for review. No inferred refund.
            raise ValueError("api.cc отклонил запрос (code=" + str(result.get("code")) + ")")
        return result["data"]

    async def catalog(self, category, card_type=1):
        return await self.call("/api/v1/app/list", {"cate_id": category, "type": card_type})

    async def check_rental_stock(self, code):
        _, app_id, card_type = map(int, code.split(":"))
        if card_type == 3:
            return
        stock = await self.call(
            "/api/v1/buy/prefix", {"app_id": app_id, "type": card_type, "expiry": RENTAL_EXPIRY}
        )
        if int(stock.get("num", 0)) < 1:
            raise Rejected("Нет номеров со сроком больше 60 дней")

    def cost_usd(self, price):
        rate = Decimal(settings.apicc_usd_rate)
        cost = Decimal(str(price)) * rate * 1000000
        if not cost.is_finite() or cost < 0:
            raise ValueError("Некорректная цена поставщика")
        return int(cost.to_integral_value(rounding=ROUND_UP))

    async def check_cost(self, code, expected_cost):
        category, app_id, card_type = map(int, code.split(":"))
        data = await self.catalog(category, card_type)
        item = next((p for p in data["list"] if int(p["id"]) == app_id), None)
        if not item or (card_type != 3 and int(item["num"]) < 1):
            raise Rejected("Нет номеров в наличии")
        if self.cost_usd(item["price"]) != expected_cost:
            raise Rejected("Цена поставщика изменилась; нужно обновить каталог")
        await self.check_rental_stock(code)
        return app_id, card_type

    async def allocate(self, code, request_id):
        category, app_id, card_type = map(int, code.split(":"))
        if card_type == 3:
            raise Rejected("Для продления выберите существующий номер")
        data = await self.call(
            "/api/v1/buy/create", {"app_id": app_id, "num": 1, "type": card_type, "expiry": RENTAL_EXPIRY}
        )
        item = data["list"][0]
        return Allocation(str(data["ordernum"]), item["tel"], item)

    async def status(self, order):
        data = await self.call("/api/v1/order/api", {"ordernum": order.supplier_id})
        item = next(p for p in data["list"] if p["tel"] == order.phone)
        if int(item["status"]) not in (0, 2):
            raise ValueError("Неизвестный статус номера api.cc")
        status = "expired" if int(item["status"]) == 2 else "active"
        return State(status, payload=item)

    async def cancel(self, order):
        raise ValueError("В API api.cc нет отмены аренды. Обратитесь в поддержку")

    async def renew(self, code, order):
        category, app_id, card_type = map(int, code.split(":"))
        payload = json.loads(order.supplier_payload)
        if card_type != 3 or payload.get("app_id") != app_id or payload.get("cate_id") != category:
            raise Rejected("Товар продления не соответствует номеру")
        result = await self.call("/api/v1/order/renewApis", {"ids": str(payload["id"])})
        if str(payload["id"]) not in str(result["ids"]).split(","):
            raise RuntimeError("Renewal result requires reconciliation")
        return Allocation("renew-" + order.id + "-" + str(time.time_ns()), order.phone, payload)


supplier = DemoSupplier() if settings.demo_mode else ApiccSupplier()


async def sync_catalog(category: int, label: str):
    """Read-only supplier calls. New products stay disabled until an admin sets their price."""
    from sqlalchemy import select

    from app.db import Session
    from app.models import Audit, Product

    if settings.demo_mode:
        raise ValueError("Отключите DEMO_MODE, чтобы импортировать каталог api.cc")
    if not supplier.ready:
        raise ValueError("Нужны APICC_API_KEY и APICC_USD_RATE")
    collected = []
    for card_type in (1, 2, 3):
        data = await supplier.catalog(category, card_type)
        for item in data["list"]:
            collected.append((card_type, item))
    with Session() as db:
        enabled_codes = set(
            db.scalars(
                select(Product.supplier_code).where(
                    Product.supplier_code.startswith(f"{category}:"), Product.enabled.is_(True)
                )
            )
        )
    missing_stock = set()
    for card_type, item in collected:
        code = f"{category}:{item['id']}:{card_type}"
        if card_type != 3 and code in enabled_codes:
            try:
                await supplier.check_rental_stock(code)
            except Rejected:
                missing_stock.add(code)
    with Session.begin() as db:
        seen = set()
        for card_type, item in collected:
            code = f"{category}:{item['id']}:{card_type}"
            seen.add(code)
            product = db.scalar(select(Product).where(Product.supplier_code == code))
            if product is None:
                product = Product(
                    supplier_code=code,
                    name=str(item["name"])[:128],
                    country=label,
                    enabled=False,
                    cost=0,
                    markup_bps=3000,
                )
                db.add(product)
            product.cost = supplier.cost_usd(item["price"])
            product.country = label
            product.kind = {1: "new", 2: "restart", 3: "renew"}[card_type]
            if card_type != 3 and (int(item["num"]) < 1 or code in missing_stock):
                product.enabled = False
        for product in db.scalars(select(Product).where(Product.supplier_code.startswith(f"{category}:"))):
            if product.supplier_code not in seen:
                product.enabled = False
        db.add(Audit(action="catalog.sync", detail=f"category={category}; products={len(collected)}"))
    return len(collected)
