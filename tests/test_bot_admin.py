from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from app import bot_admin
from app.config import settings
from app.models import Notice, Order, Product, User


@pytest.fixture
def admin(database, monkeypatch):
    for module in (bot_admin,):
        monkeypatch.setattr(module, "Session", database)
    from app import services

    monkeypatch.setattr(services, "Session", database)
    monkeypatch.setattr(settings, "admin_ids", "777")
    with database.begin() as db:
        db.add(User(id=777, username="owner"))
    return database


class FakeState:
    def __init__(self):
        self.data, self.state = {}, None

    async def set_state(self, value):
        self.state = value

    async def update_data(self, **values):
        self.data.update(values)

    async def get_data(self):
        return dict(self.data)

    async def clear(self):
        self.data, self.state = {}, None


def click(data, user=777):
    message = SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock())
    return SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=user), data=data, message=message)


def typed(text, user=777):
    return SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=user), text=text)


@pytest.mark.asyncio
async def test_strangers_cannot_open_admin(admin):
    query = click("adm:home", user=123)
    await bot_admin.admin_callback(query, FakeState())
    assert not query.message.edit_text.await_count and not query.message.answer.await_count
    message = typed("/admin", user=123)
    await bot_admin.admin_command(message, FakeState())
    assert not message.answer.await_count


@pytest.mark.asyncio
async def test_admin_home_shows_stats(admin):
    query = click("adm:home")
    await bot_admin.admin_callback(query, FakeState())
    assert "Пользователи: <b>2</b>" in query.message.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_admin_credits_balance_once_and_notifies_user(admin):
    state = FakeState()
    await bot_admin.admin_callback(click("adm:ask:credit:123"), state)
    await bot_admin.admin_input(typed("2.50"), state)
    await bot_admin.admin_input(typed("2.50"), state)  # stale dialog: no second credit
    with admin() as db:
        assert db.get(User, 123).balance == 2500000
        assert len(db.scalars(select(Notice)).all()) == 1


@pytest.mark.asyncio
async def test_admin_debit_cannot_go_negative(admin):
    state = FakeState()
    await bot_admin.admin_callback(click("adm:ask:debit:123"), state)
    message = typed("5")
    await bot_admin.admin_input(message, state)
    assert "Недостаточно" in message.answer.call_args.args[0]
    with admin() as db:
        assert db.get(User, 123).balance == 0


@pytest.mark.asyncio
async def test_admin_sets_price_and_toggles_product(admin):
    state = FakeState()
    await bot_admin.admin_callback(click("adm:ask:price:1"), state)
    await bot_admin.admin_input(typed("1.25"), state)
    await bot_admin.admin_callback(click("adm:toggle:1"), FakeState())
    with admin() as db:
        product = db.get(Product, 1)
        assert product.fixed_price == 1250000 and product.enabled is False
    message = typed("0.10")
    await bot_admin.admin_callback(click("adm:ask:price:1"), state)
    await bot_admin.admin_input(message, state)
    assert "ниже закупки" in message.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_admin_finds_user_by_username(admin):
    state = FakeState()
    await bot_admin.admin_callback(click("adm:find"), state)
    message = typed("@Tester")
    await bot_admin.admin_input(message, state)
    assert "<code>123</code>" in message.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_admin_refunds_review_order_once(admin):
    with admin.begin() as db:
        db.add(
            Order(
                id="r" * 32,
                request_key="review",
                user_id=123,
                product_id=1,
                price=700000,
                cost=1,
                status="review",
            )
        )
    await bot_admin.admin_callback(click("adm:refund:" + "r" * 32), FakeState())
    second = click("adm:refund:" + "r" * 32)
    await bot_admin.admin_callback(second, FakeState())
    assert "уже не на проверке" in second.message.answer.call_args.args[0]
    with admin() as db:
        assert db.get(User, 123).balance == 700000
        assert db.get(Order, "r" * 32).status == "refunded"


@pytest.mark.asyncio
async def test_admin_is_notified_when_purchase_needs_review(admin, monkeypatch):
    from types import SimpleNamespace as NS

    from app import services, worker

    async def broken(code, request_id):
        raise RuntimeError("supplier timeout after charge?")

    monkeypatch.setattr(services, "supplier", NS(ready=True, allocate=broken))
    monkeypatch.setattr(settings, "admin_ids", "777,999")  # 999 never opened the bot
    with admin.begin() as db:
        db.get(User, 123).balance = 5_000_000
    order = services.reserve_order(123, 1, "review-notify", 1_000_000)
    await services.allocate_order(order.id)
    with admin() as db:
        assert db.get(Order, order.id).status == "review"
        notices = db.scalars(select(Notice).where(Notice.event.startswith("review-admin:"))).all()
    assert [n.user_id for n in notices] == [777]
    assert "Заказ на проверке" in notices[0].text
    rows = worker.notice_buttons(notices[0].event, notices[0].text)
    assert rows[0][0][1] == "adm:o:" + order.id


@pytest.mark.asyncio
async def test_setup_screen_lists_what_is_missing(admin, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "apicc_api_key", "")
    query = click("adm:setup")
    await bot_admin.admin_callback(query, FakeState())
    text = query.message.edit_text.call_args.args[0]
    assert "Запуск магазина" in text and "APICC_API_KEY" in text


@pytest.mark.asyncio
async def test_admin_sets_currency_from_the_bot(admin, monkeypatch):
    from app import preferences

    monkeypatch.setattr(preferences.db, "Session", admin)
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "apicc_api_key", "key")
    state = FakeState()
    await bot_admin.admin_callback(click("adm:ask:currency:0"), state)
    bad = typed("CNY")
    await bot_admin.admin_input(bad, state)
    assert "CNY 0.14" in bad.answer.call_args.args[0] and "<code>" not in bad.answer.call_args.args[0]
    good = typed("cny 0.14")
    await bot_admin.admin_input(good, state)
    assert preferences.supplier_pricing() == {"currency": "CNY", "usd_rate": "0.14"}
    assert "Валюта сохранена" in good.answer.call_args.args[0]
