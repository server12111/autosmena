from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import bot, subscription
from app.config import settings
from app.models import User


@pytest.fixture
def channel(database, monkeypatch):
    monkeypatch.setattr(subscription.db, "Session", database)
    monkeypatch.setattr(settings, "admin_ids", "777")
    subscription._verified.clear()
    subscription.save([{"id": -1001, "title": "SMENA News", "url": "https://t.me/smena_news"}], 777, "add")
    return database


def telegram(status):
    return SimpleNamespace(get_chat_member=AsyncMock(return_value=SimpleNamespace(status=status)))


def message(text, user=123):
    return SimpleNamespace(text=text, from_user=SimpleNamespace(id=user, username="u"), answer=AsyncMock())


@pytest.mark.asyncio
async def test_unsubscribed_user_sees_gate_and_handler_does_not_run(channel):
    handler = AsyncMock()
    gate = bot.SubscriptionGate()
    from aiogram.types import Message

    real = Message.model_construct(text="/start ref_123", from_user=SimpleNamespace(id=900, username="new"))
    object.__setattr__(real, "answer", AsyncMock())
    await gate(handler, real, {"bot": telegram("left")})
    handler.assert_not_awaited()
    assert "Подпишитесь" in real.answer.call_args.args[0]
    with channel() as db:
        assert db.get(User, 900).referrer_id == 123  # referral kept while gated


@pytest.mark.asyncio
async def test_subscribed_user_and_admin_pass(channel):
    handler = AsyncMock()
    await bot.SubscriptionGate()(handler, message("/start"), {"bot": telegram("member")})
    await bot.SubscriptionGate()(handler, message("/start", user=777), {"bot": telegram("left")})
    assert handler.await_count == 2


@pytest.mark.asyncio
async def test_unreachable_channel_does_not_lock_everyone_out(channel):
    tg = SimpleNamespace(get_chat_member=AsyncMock(side_effect=RuntimeError("bot removed")))
    assert await subscription.missing(tg, 123) == []


@pytest.mark.asyncio
async def test_check_button_rechecks_without_cache(channel):
    tg = telegram("left")
    from aiogram.types import CallbackQuery

    query = CallbackQuery.model_construct(
        data="subcheck",
        from_user=SimpleNamespace(id=123, username="u"),
        message=SimpleNamespace(answer=AsyncMock(), edit_text=AsyncMock()),
    )
    object.__setattr__(query, "answer", AsyncMock())
    await bot.SubscriptionGate()(AsyncMock(), query, {"bot": tg})
    assert query.answer.call_args.kwargs.get("show_alert")
    tg.get_chat_member.return_value = SimpleNamespace(status="member")
    await bot.SubscriptionGate()(AsyncMock(), query, {"bot": tg})
    assert "SMENA" in query.message.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_admin_adds_channel_only_when_bot_is_admin(channel):
    chat = SimpleNamespace(id=-1002, title="Promo", username="promo", invite_link=None)
    tg = SimpleNamespace(
        get_chat=AsyncMock(return_value=chat),
        me=AsyncMock(return_value=SimpleNamespace(id=1)),
        get_chat_member=AsyncMock(return_value=SimpleNamespace(status="member")),
    )
    with pytest.raises(ValueError, match="администратором"):
        await subscription.add(tg, "https://t.me/promo", 777)
    tg.get_chat_member.return_value = SimpleNamespace(status="administrator")
    added = await subscription.add(tg, "@promo", 777)
    assert added == {"id": -1002, "title": "Promo", "url": "https://t.me/promo"}
    assert [c["id"] for c in subscription.channels()] == [-1001, -1002]
    subscription.remove(-1001, 777)
    assert [c["id"] for c in subscription.channels()] == [-1002]
