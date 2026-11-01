import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import bot
from app.models import Order


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "https://api.sms8.net/api/record?token=test",
        "https://api.cc/api/record?token=test",
        "https://api.sms8.net.evil.example/api/record?token=test",
        "http://api.sms8.net/api/record?token=test",
    ],
)
async def test_supplier_reception_links_never_appear_in_customer_messages(database, url):
    with database.begin() as db:
        db.add(
            Order(
                id="f" * 32,
                request_key="reception-link",
                user_id=123,
                product_id=1,
                price=1000000,
                cost=1000000,
                status="active",
                supplier_payload=json.dumps({"api": url}),
            )
        )
    message = SimpleNamespace(answer=AsyncMock())
    await bot.order_card(message, 123, "f" * 32)
    markup = message.answer.call_args_list[0].kwargs["reply_markup"]
    urls = [button.url for row in markup.inline_keyboard for button in row if button.url]
    assert not urls
    assert url not in message.answer.call_args_list[0].args[0]
    assert "Код придёт в этот чат" in message.answer.call_args_list[0].args[0]


@pytest.mark.asyncio
async def test_old_orders_remain_accessible_after_ten_newer_orders(database):
    with database.begin() as db:
        for i in range(11):
            db.add(
                Order(
                    id=f"{i:032x}",
                    request_key=f"history-{i}",
                    user_id=123,
                    product_id=1,
                    price=1000000,
                    cost=1000000,
                    phone=f"+100{i}",
                    created_at=100 + i,
                    status="active",
                )
            )
    message = SimpleNamespace(answer=AsyncMock())
    await bot.orders(message, 123, offset=10)
    markup = message.answer.call_args.kwargs["reply_markup"].inline_keyboard
    assert markup[0][0].callback_data == "order:" + f"{0:032x}"
    assert markup[1][0].callback_data == "orders:0"


@pytest.mark.asyncio
async def test_navigation_cancels_amount_entry(database):
    query = SimpleNamespace(
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=123, username="tester"),
        data="catalog:0",
        message=SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()),
    )
    state = SimpleNamespace(set_state=AsyncMock())
    bot.last_action.clear()
    await bot.callback(query, state)
    state.set_state.assert_awaited_once_with(None)


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["paid", "expired", "review"])
async def test_settled_or_expired_ton_invoice_does_not_request_another_transfer(status):
    from app.models import now

    message = SimpleNamespace(answer=AsyncMock())
    payment = SimpleNamespace(
        id="a" * 32,
        provider="ton",
        amount=1000000,
        crypto_amount="0.5",
        reference="ref",
        status=status,
        url="ton://transfer/test",
        expires_at=now() + 100,
    )
    await bot.payment_message(message, payment)
    text = message.answer.call_args.args[0]
    assert "Отправьте" not in text
    assert "ton://transfer" not in text


def query(data, message=None):
    return SimpleNamespace(
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=123, username="tester"),
        data=data,
        message=message or SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()),
    )


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


def buttons(mock):
    markup = mock.call_args.kwargs["reply_markup"]
    return [
        b.callback_data or (b.copy_text and b.copy_text.text) or b.url
        for r in markup.inline_keyboard
        for b in r
    ]


@pytest.mark.asyncio
async def test_purchase_flow_edits_one_message_until_number_card(database):
    from app.models import User

    with database.begin() as db:
        db.get(User, 123).balance = 2000000
    bot.last_action.clear()
    event = query("product:1")
    await bot.callback(event, FakeState())
    buy = next(b for b in buttons(event.message.edit_text) if b.startswith("buy:"))
    assert not event.message.answer.await_count
    bot.last_action.clear()
    event = query(buy)
    await bot.callback(event, FakeState())
    text = event.message.edit_text.call_args.args[0]
    assert "<code>+1 555" in text and "Код придёт в этот чат" in text
    assert "Добавить аккаунт" not in text
    assert not any(b and b.startswith("cancel") for b in buttons(event.message.edit_text))
    assert any(b and b.startswith("+1555") for b in buttons(event.message.edit_text))
    assert not event.message.answer.await_count
    with database() as db:
        from app.models import Notice

        assert db.get(User, 123).balance == 1000000
        # The card is already on screen: no duplicate "number ready" notification.
        assert all(n.sent for n in db.query(Notice).all())


@pytest.mark.asyncio
async def test_insufficient_balance_offers_top_up_instead_of_buy(database):
    with database.begin() as db:
        from app.models import User

        db.get(User, 123).balance = 0
    bot.last_action.clear()
    event = query("product:1")
    await bot.callback(event, FakeState())
    assert "wallet" in buttons(event.message.edit_text)
    assert not any(b.startswith("buy:") for b in buttons(event.message.edit_text))


@pytest.mark.asyncio
async def test_quick_amount_creates_invoice(database):
    bot.last_action.clear()
    state = FakeState()
    event = query("deposit:demo")
    await bot.callback(event, state)
    assert "amount:demo:5" in buttons(event.message.edit_text)
    bot.last_action.clear()
    event = query("amount:demo:5")
    await bot.callback(event, state)
    assert "$5.00" in event.message.edit_text.call_args.args[0]
    assert state.state is None


@pytest.mark.asyncio
async def test_free_text_shows_menu(database):
    message = SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=123, username="tester"))
    await bot.anything_else(message, FakeState())
    assert "SMENA" in message.answer.call_args.args[0]


@pytest.mark.asyncio
async def test_demo_code_is_digits_only():
    from app.supplier import DemoSupplier

    state = await DemoSupplier().status(SimpleNamespace(created_at=0))
    assert state.sms == "123456"


@pytest.mark.asyncio
async def test_menu_has_no_help_or_payment_history_and_admin_only_for_owner(database, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "admin_ids", "999")
    message = SimpleNamespace(answer=AsyncMock())
    await bot.send_menu(message, 123)
    assert not {"help", "payments", "adm:home"} & set(buttons(message.answer))
    await bot.send_menu(message, 999)
    assert "adm:home" in buttons(message.answer)


@pytest.mark.asyncio
async def test_profile_referral_and_api_screens(database):
    for data in ("profile", "ref", "api", "apidocs"):
        bot.last_action.clear()
        event = query(data)
        event.from_user.first_name = "Tester"
        await bot.callback(event, FakeState())
        assert event.message.edit_text.await_count, data
    bot.last_action.clear()
    event = query("ref")
    await bot.callback(event, FakeState())
    assert "start=ref_123" in event.message.edit_text.call_args.args[0]
    bot.last_action.clear()
    event = query("apikey")
    await bot.callback(event, FakeState())
    key = next(b for b in buttons(event.message.edit_text) if b and b.startswith("smn_"))
    assert key in event.message.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_code_again_button_after_first_code(database):
    with database.begin() as db:
        db.add(
            Order(
                id="e" * 32,
                request_key="again",
                user_id=123,
                product_id=1,
                price=1,
                cost=1,
                status="active",
                phone="+15550001111",
                sms="111111",
            )
        )
    bot.last_action.clear()
    event = query("order:" + "e" * 32)
    await bot.callback(event, FakeState())
    assert "again:" + "e" * 32 in buttons(event.message.edit_text)
    bot.last_action.clear()
    event = query("again:" + "e" * 32)
    await bot.callback(event, FakeState())
    assert "Ждём новый код" in event.message.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_screens_show_their_banner_as_large_preview_above_text(database, monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "banner_base_url", "https://cdn.example/brand")
    bot.banner_version.cache_clear()
    message = SimpleNamespace(answer=AsyncMock())
    await bot.send_menu(message, 123)
    preview = message.answer.call_args.kwargs["link_preview_options"]
    assert preview.url.startswith("https://cdn.example/brand/main.png?v=")
    assert preview.show_above_text and preview.prefer_large_media
    for data, name in (
        ("catalog:0", "buy"),
        ("orders", "numbers"),
        ("ref", "ref"),
        ("profile", "profile"),
        ("wallet", "wallet"),
    ):
        bot.last_action.clear()
        event = query(data)
        event.from_user.first_name = "T"
        await bot.callback(event, FakeState())
        assert f"/{name}.png" in event.message.edit_text.call_args.kwargs["link_preview_options"].url, data
    bot.banner_version.cache_clear()


@pytest.mark.asyncio
async def test_out_of_stock_refund_shown_on_card_without_duplicate_message(database, monkeypatch):
    from app import services
    from app.models import Notice, User
    from app.supplier import Rejected

    async def no_stock(code, request_id):
        raise Rejected("Нет номеров в наличии")

    monkeypatch.setattr(services, "supplier", SimpleNamespace(ready=True, allocate=no_stock))
    with database.begin() as db:
        db.get(User, 123).balance = 2_000_000
    bot.last_action.clear()
    event = query("buy:1:1000000:tok-refund")
    await bot.callback(event, FakeState())
    assert "возвращены на баланс" in event.message.edit_text.call_args.args[0]
    with database() as db:
        assert db.get(User, 123).balance == 2_000_000
        assert all(n.sent for n in db.query(Notice).all())


def test_banner_follows_domain_changes_without_restart(monkeypatch):
    from app.config import settings

    monkeypatch.setattr(settings, "banner_base_url", "")
    monkeypatch.setattr(bot, "public_url", lambda: "https://old.example")
    assert bot.banner("main").startswith("https://old.example/static/brand/main.png")
    monkeypatch.setattr(bot, "public_url", lambda: "https://new.example")
    assert bot.banner("main").startswith("https://new.example/static/brand/main.png")
