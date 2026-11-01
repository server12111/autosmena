from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import bot
from app.models import Order


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
    assert "+1000\n" in message.answer.call_args_list[0].args[0]
    navigation = message.answer.call_args_list[-1].kwargs["reply_markup"]
    assert navigation.inline_keyboard[0][0].callback_data == "orders:0"


@pytest.mark.asyncio
async def test_navigation_cancels_amount_entry(database):
    message = SimpleNamespace(answer=AsyncMock())
    query = SimpleNamespace(
        answer=AsyncMock(),
        from_user=SimpleNamespace(id=123, username="tester"),
        data="catalog:0",
        message=message,
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
