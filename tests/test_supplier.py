import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.config import settings
from app.supplier import ApiccSupplier, Rejected


@pytest.fixture
def supplier(monkeypatch):
    monkeypatch.setattr(settings, "apicc_usd_rate", "1")
    monkeypatch.setattr(settings, "apicc_api_key", "test-token")
    return ApiccSupplier()


@pytest.mark.asyncio
async def test_buy_uses_documented_request(supplier, monkeypatch):
    calls = []

    async def call(path, body):
        calls.append((path, body))
        return {
            "ordernum": "supplier-order",
            "list": [{"tel": "+123", "api": "https://api.cc/api/record?token=test"}],
        }

    monkeypatch.setattr(supplier, "call", call)
    allocation = await supplier.allocate("2:74:1", "local-id")
    path, body = calls[0]
    spec = json.loads((Path(__file__).parents[1] / "docs/apicc.openapi.json").read_text(encoding="utf-8-sig"))
    schema = spec["paths"][path]["post"]["requestBody"]["content"]["application/json"]["schema"]
    assert set(schema["required"]) <= body.keys() <= schema["properties"].keys()
    assert body == {"app_id": 74, "num": 1, "type": 1, "expiry": 5}
    assert allocation.external_id == "supplier-order"
    assert allocation.phone == "+123"


@pytest.mark.asyncio
async def test_supplier_price_change_rejects_before_purchase(supplier, monkeypatch):
    async def catalog(*_):
        return {"list": [{"id": 74, "name": "Telegram", "price": "2.00", "num": 3}]}

    monkeypatch.setattr(supplier, "catalog", catalog)

    async def call(path, body):
        assert path == "/api/v1/buy/prefix"
        assert body == {"app_id": 74, "type": 1, "expiry": 5}
        return {"num": 1}

    monkeypatch.setattr(supplier, "call", call)
    with pytest.raises(Rejected):
        await supplier.check_cost("2:74:1", 1000000)
    await supplier.check_cost("2:74:1", 2000000)


@pytest.mark.asyncio
async def test_short_term_stock_cannot_pass_preflight(supplier, monkeypatch):
    async def catalog(*_):
        return {"list": [{"id": 74, "name": "Telegram", "price": "1.00", "num": 50}]}

    async def call(path, body):
        assert body["expiry"] == 5
        return {"num": 0}

    monkeypatch.setattr(supplier, "catalog", catalog)
    monkeypatch.setattr(supplier, "call", call)
    with pytest.raises(Rejected, match="60"):
        await supplier.check_cost("2:74:1", 1000000)


@pytest.mark.asyncio
async def test_regressing_sms_counter_does_not_break_notifications(database, monkeypatch):
    from sqlalchemy import select

    from app import services
    from app.models import Notice
    from app.services import adjust_balance, allocate_order, refresh_order, reserve_order
    from app.supplier import State

    adjust_balance(123, 2000000, "fund", "test")
    order = reserve_order(123, 1, "regressing-count", 1000000)
    await allocate_order(order.id)

    class CounterSupplier:
        counts = iter([2, 1, 2, 3])

        async def status(self, order):
            return State("active", payload={"sms_count": next(self.counts)})

    monkeypatch.setattr(services, "supplier", CounterSupplier())
    for _ in range(4):
        await refresh_order(order.id)
    with database() as db:
        events = db.scalars(select(Notice.event).where(Notice.event.startswith("sms-count:"))).all()
        assert sorted(e.rsplit(":", 1)[1] for e in events) == ["2", "3"]


@pytest.mark.asyncio
async def test_real_expiry_does_not_mean_refund(supplier, monkeypatch):
    async def call(*_):
        return {"list": [{"tel": "+123", "status": 2, "sms_count": 0}]}

    monkeypatch.setattr(supplier, "call", call)
    state = await supplier.status(SimpleNamespace(supplier_id="supplier-order", phone="+123"))
    assert state.status == "expired"
    with pytest.raises(ValueError):
        await supplier.cancel(None)


@pytest.mark.asyncio
async def test_renewal_cannot_target_different_service(supplier, monkeypatch):
    called = False

    async def call(*_):
        nonlocal called
        called = True

    monkeypatch.setattr(supplier, "call", call)
    order = SimpleNamespace(supplier_payload=json.dumps({"app_id": 74, "cate_id": 2, "id": 12}))
    with pytest.raises(Rejected):
        await supplier.renew("2:999:3", order)
    assert not called


@pytest.mark.asyncio
async def test_repeated_sms_count_produces_one_notice(database, monkeypatch):
    from sqlalchemy import func, select

    from app import services
    from app.models import Notice, Order
    from app.services import adjust_balance, allocate_order, refresh_order, reserve_order
    from app.supplier import State

    adjust_balance(123, 2000000, "fund", "test")
    order = reserve_order(123, 1, "sms-count", 1000000)
    await allocate_order(order.id)

    class ActiveSupplier:
        async def status(self, order):
            return State("active", payload={"sms_count": 2, "end_time": "2030-01-01"})

    monkeypatch.setattr(services, "supplier", ActiveSupplier())
    await refresh_order(order.id)
    await refresh_order(order.id)
    with database() as db:
        assert db.get(Order, order.id).status == "active"
        assert (
            db.scalar(
                select(func.count()).select_from(Notice).where(Notice.event == "sms-count:" + order.id + ":2")
            )
            == 1
        )


@pytest.mark.asyncio
async def test_live_expiry_does_not_credit_wallet(database, monkeypatch):
    from app import services
    from app.models import User
    from app.services import adjust_balance, allocate_order, refresh_order, reserve_order
    from app.supplier import State

    adjust_balance(123, 2000000, "fund", "test")
    order = reserve_order(123, 1, "expiry", 1000000)
    await allocate_order(order.id)

    class ExpiredSupplier:
        async def status(self, order):
            return State("expired", payload={"sms_count": 0})

    monkeypatch.setattr(services, "supplier", ExpiredSupplier())
    await refresh_order(order.id)
    with database() as db:
        assert db.get(User, 123).balance == 1000000


@pytest.mark.asyncio
async def test_catalog_sync_disables_enabled_service_without_long_term_stock(database, monkeypatch):
    from app import supplier as module
    from app.models import Product

    class Catalog:
        ready = True

        async def catalog(self, category, card_type):
            return {"list": [{"id": 74, "name": "Telegram", "price": "1.00", "num": 50}]}

        async def check_rental_stock(self, code):
            raise Rejected("Нет номеров больше 60 дней")

        def cost_usd(self, price):
            return 1000000

    with database.begin() as db:
        db.get(Product, 1).supplier_code = "2:74:1"
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(module, "supplier", Catalog())
    assert await module.sync_catalog(2, "США") == 3
    with database() as db:
        product = db.get(Product, 1)
        assert product.enabled is False
        assert product.country.startswith("США")
