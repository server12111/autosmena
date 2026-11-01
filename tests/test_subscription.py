from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import bot, subscription
from app.config import settings
from app.models import User

REAL_MEMBER_STATUS = subscription.member_status


@pytest.fixture
def channel(database, monkeypatch):
    monkeypatch.setattr(subscription.db, "Session", database)
    monkeypatch.setattr(settings, "admin_ids", "777")
    subscription._verified.clear()
    subscription.save([{"id": -1001, "title": "SMENA News", "url": "https://t.me/smena_news"}], 777, "add")
    return database


class FakeTelegram:
    """Stands in for the raw getChatMember call; status=None means Telegram refused."""

    def __init__(self, status):
        self.status = status
        self.calls = 0


def telegram(status):
    return FakeTelegram(status)


@pytest.fixture(autouse=True)
def raw_status(monkeypatch):
    async def fake(bot, chat_id, user_id):
        bot.calls += 1
        if isinstance(bot.status, Exception):
            raise bot.status
        return bot.status

    monkeypatch.setattr(subscription, "member_status", fake)


def message(text, user=123, chat="private"):
    return SimpleNamespace(
        text=text,
        from_user=SimpleNamespace(id=user, username="u"),
        answer=AsyncMock(),
        chat=SimpleNamespace(type=chat),
    )


@pytest.mark.asyncio
async def test_unsubscribed_user_sees_gate_and_handler_does_not_run(channel):
    handler = AsyncMock()
    gate = bot.SubscriptionGate()
    from aiogram.types import Message

    real = Message.model_construct(
        text="/start ref_123",
        from_user=SimpleNamespace(id=900, username="new"),
        chat=SimpleNamespace(type="private"),
    )
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
    assert await subscription.missing(telegram(RuntimeError("network")), 123) == []
    assert await subscription.missing(telegram(None), 123) == []  # bot removed from the channel


@pytest.mark.asyncio
async def test_check_button_rechecks_without_cache(channel):
    tg = telegram("left")
    from aiogram.types import CallbackQuery

    query = CallbackQuery.model_construct(
        data="subcheck",
        from_user=SimpleNamespace(id=123, username="u"),
        message=SimpleNamespace(
            answer=AsyncMock(), edit_text=AsyncMock(), chat=SimpleNamespace(type="private")
        ),
    )
    object.__setattr__(query, "answer", AsyncMock())
    await bot.SubscriptionGate()(AsyncMock(), query, {"bot": tg})
    assert query.answer.call_args.kwargs.get("show_alert")
    tg.status = "member"
    await bot.SubscriptionGate()(AsyncMock(), query, {"bot": tg})
    assert "SMENA" in query.message.edit_text.call_args.args[0]


@pytest.mark.asyncio
async def test_admin_adds_channel_only_when_bot_is_admin(channel):
    chat = SimpleNamespace(id=-1002, title="Promo", username="promo", invite_link=None)
    tg = telegram("member")
    tg.get_chat = AsyncMock(return_value=chat)
    tg.me = AsyncMock(return_value=SimpleNamespace(id=1))
    with pytest.raises(ValueError, match="администратором"):
        await subscription.add(tg, "https://t.me/promo", 777)
    tg.status = "administrator"
    added = await subscription.add(tg, "@promo", 777)
    assert added == {"id": -1002, "title": "Promo", "url": "https://t.me/promo"}
    assert [c["id"] for c in subscription.channels()] == [-1001, -1002]
    subscription.remove(-1001, 777)
    assert [c["id"] for c in subscription.channels()] == [-1002]


@pytest.mark.asyncio
async def test_gate_never_answers_in_groups(channel):
    from aiogram.types import Message

    event = Message.model_construct(
        text="hi", from_user=SimpleNamespace(id=900, username="x"), chat=SimpleNamespace(type="supergroup")
    )
    object.__setattr__(event, "answer", AsyncMock())
    handler = AsyncMock()
    await bot.SubscriptionGate()(handler, event, {"bot": telegram("left")})
    event.answer.assert_not_awaited()
    handler.assert_awaited_once()


@pytest.mark.asyncio
async def test_raw_status_reads_admin_answer_that_aiogram_models_reject(monkeypatch):
    """Telegram's answer for a channel admin lacks a field aiogram 3.31 requires."""
    answer = {
        "ok": True,
        "result": {
            "status": "administrator",
            "user": {"id": 1, "is_bot": True, "first_name": "b"},
            "can_be_edited": False,
            "can_manage_chat": True,
            "can_post_messages": True,
        },
    }

    class Response:
        def json(self):
            return answer

    class Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, json):
            assert url.endswith("/getChatMember") and json == {"chat_id": -1001, "user_id": 1}
            return Response()

    monkeypatch.setattr(subscription.httpx, "AsyncClient", Client)
    real = SimpleNamespace(member_status=REAL_MEMBER_STATUS)
    assert await real.member_status(SimpleNamespace(token="t"), -1001, 1) == "administrator"
    answer["result"] = {"status": "restricted", "is_member": True, "user": answer["result"]["user"]}
    assert await real.member_status(SimpleNamespace(token="t"), -1001, 1) == "member"
    answer.clear()
    answer.update({"ok": False, "description": "Bad Request: member list is inaccessible"})
    assert await real.member_status(SimpleNamespace(token="t"), -1001, 1) is None
