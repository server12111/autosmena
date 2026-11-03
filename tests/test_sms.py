import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from app import services, supplier, worker
from app.models import Notice, Order
from app.supplier import ApiccSupplier, State


def mock_endpoint(monkeypatch, response):
    original = httpx.AsyncClient
    calls = []

    def handler(request):
        calls.append(request)
        assert "authorization" not in request.headers
        return response

    monkeypatch.setattr(
        supplier.httpx,
        "AsyncClient",
        lambda **kwargs: original(**kwargs, transport=httpx.MockTransport(handler)),
    )
    return calls


@pytest.mark.asyncio
@pytest.mark.parametrize("code", ["01234", "123456"])
async def test_code_is_read_privately_and_leading_zero_is_preserved(monkeypatch, code):
    calls = mock_endpoint(
        monkeypatch,
        httpx.Response(200, json={"code": 1, "data": {"code": code, "code_time": "2026-11-01 15:00:00"}}),
    )
    adapter = ApiccSupplier()
    sms, timestamp = await adapter.receive_sms("https://api.sms8.net/api/record?token=private-test")
    assert sms == code and timestamp == "2026-11-01 15:00:00"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_empty_sms_is_not_a_failure(monkeypatch):
    mock_endpoint(monkeypatch, httpx.Response(200, json={"code": 0, "data": {"code": "", "code_time": ""}}))
    assert await ApiccSupplier().receive_sms("https://api.cc/api/record?token=test") == ("", "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://api.sms8.net/api/record?token=test",
        "https://api.sms8.net.evil.example/api/record?token=test",
        "https://127.0.0.1/api/record?token=test",
        "https://api.sms8.net:8443/api/record?token=test",
        "https://user:secret@api.sms8.net/api/record?token=test",
        "https://api.sms8.net/other?token=test",
    ],
)
async def test_sms_fetch_refuses_untrusted_urls_before_network(monkeypatch, url):
    calls = mock_endpoint(monkeypatch, httpx.Response(200, json={}))
    with pytest.raises(ValueError):
        await ApiccSupplier().receive_sms(url)
    assert not calls


@pytest.mark.asyncio
async def test_sms_redirect_is_not_followed(monkeypatch):
    calls = mock_endpoint(monkeypatch, httpx.Response(302, headers={"Location": "https://evil.example/"}))
    with pytest.raises((ValueError, httpx.HTTPStatusError)):
        await ApiccSupplier().receive_sms("https://api.sms8.net/api/record?token=test")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_status_fetches_code_even_if_sms_counter_has_not_updated(monkeypatch):
    adapter = ApiccSupplier()
    monkeypatch.setattr(
        adapter, "call", AsyncMock(return_value={"list": [{"tel": "+123", "status": 0, "sms_count": 0}]})
    )
    receive = AsyncMock(return_value=("12345", "time-1"))
    monkeypatch.setattr(adapter, "receive_sms", receive)
    order = SimpleNamespace(
        supplier_id="test",
        phone="+123",
        supplier_payload=json.dumps({"api": "https://api.sms8.net/api/record?token=test"}),
    )
    state = await adapter.status(order)
    assert state.status == "active" and state.sms == "12345"
    assert state.payload["_sms_code_time"] == "time-1"
    receive.assert_awaited_once()


@pytest.mark.asyncio
async def test_new_codes_are_sent_in_chat_once_without_ending_rental(database, monkeypatch):
    with database.begin() as db:
        db.add(
            Order(
                id="a" * 32,
                request_key="sms-test",
                user_id=123,
                product_id=1,
                status="active",
                price=1000000,
                cost=1000000,
                phone="+123",
            )
        )
    state = State("active", sms="01234", payload={"sms_count": 1, "_sms_code_time": "time-1"})
    monkeypatch.setattr(services, "supplier", SimpleNamespace(status=AsyncMock(side_effect=lambda _: state)))
    bot = SimpleNamespace(send_message=AsyncMock())
    await worker.cycle(bot)
    await worker.cycle(bot)
    assert bot.send_message.await_count == 1
    assert bot.send_message.call_args.args[0] == 123
    assert "<code>01234</code>" in bot.send_message.call_args.args[1]
    copy = bot.send_message.call_args.kwargs["reply_markup"].inline_keyboard[0][0]
    assert copy.copy_text.text == "01234"
    # The same digits in a later SMS are still a new message.
    state = State("active", sms="01234", payload={"sms_count": 2, "_sms_code_time": "time-2"})
    await worker.cycle(bot)
    assert bot.send_message.await_count == 2
    state = State("active", sms="54321", payload={"sms_count": 3, "_sms_code_time": "time-3"})
    await worker.cycle(bot)
    assert bot.send_message.await_count == 3
    # A stale provider response must not deliver the first SMS again.
    state = State("active", sms="01234", payload={"sms_count": 1, "_sms_code_time": "time-1"})
    await worker.cycle(bot)
    assert bot.send_message.await_count == 3
    with database() as db:
        assert db.get(Order, "a" * 32).status == "active"
        assert db.get(Order, "a" * 32).sms == "54321"
        assert len(db.scalars(select(Notice)).all()) == 3


@pytest.mark.asyncio
async def test_failed_sms_check_keeps_order_and_retries_later(database, monkeypatch):
    with database.begin() as db:
        db.add(
            Order(
                id="b" * 32,
                request_key="sms-retry",
                user_id=123,
                product_id=1,
                status="active",
                price=1000000,
                cost=1000000,
                phone="+123",
            )
        )
    status = AsyncMock(side_effect=[ValueError("temporary"), State("active", sms="54321")])
    monkeypatch.setattr(services, "supplier", SimpleNamespace(status=status))
    bot = SimpleNamespace(send_message=AsyncMock())
    await worker.cycle(bot)
    assert not bot.send_message.await_count
    await worker.cycle(bot)
    bot.send_message.assert_awaited_once()
    assert "<code>54321</code>" in bot.send_message.call_args.args[1]


@pytest.mark.asyncio
async def test_parallel_manual_and_worker_check_create_one_sms_notice(database, monkeypatch):
    with database.begin() as db:
        db.add(
            Order(
                id="c" * 32,
                request_key="sms-parallel",
                user_id=123,
                product_id=1,
                status="active",
                price=1000000,
                cost=1000000,
                phone="+123",
            )
        )

    async def status(order):
        await asyncio.sleep(0)
        return State("active", sms="12345", payload={"_sms_code_time": "time-1"})

    monkeypatch.setattr(services, "supplier", SimpleNamespace(status=status))
    await asyncio.gather(services.refresh_order("c" * 32, 123), services.refresh_order("c" * 32))
    with database() as db:
        assert len(db.scalars(select(Notice)).all()) == 1


@pytest.mark.asyncio
async def test_telegram_delivery_failure_keeps_sms_queued(database, monkeypatch):
    with database.begin() as db:
        db.add(
            Order(
                id="d" * 32,
                request_key="sms-delivery",
                user_id=123,
                product_id=1,
                status="active",
                price=1000000,
                cost=1000000,
                phone="+123",
            )
        )
    monkeypatch.setattr(
        services, "supplier", SimpleNamespace(status=AsyncMock(return_value=State("active", sms="12345")))
    )
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[RuntimeError("temporary"), None]))
    await worker.cycle(bot)
    with database() as db:
        assert db.scalar(select(Notice)).sent is False
    await worker.cycle(bot)
    await worker.cycle(bot)
    assert bot.send_message.await_count == 2
    with database() as db:
        assert db.scalar(select(Notice)).sent is True


@pytest.mark.asyncio
async def test_blocked_bot_notice_is_not_retried_forever(database):
    from aiogram.exceptions import TelegramForbiddenError
    from aiogram.methods import SendMessage

    with database.begin() as db:
        db.add(Notice(user_id=123, event="test:blocked", text="hi"))
    error = TelegramForbiddenError(
        method=SendMessage(chat_id=123, text="hi"), message="bot was blocked by the user"
    )
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=error))
    await worker.send_notices(bot)
    await worker.send_notices(bot)
    assert bot.send_message.await_count == 1


@pytest.mark.asyncio
async def test_plain_fallback_strips_premium_tags_and_entities(database):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import SendMessage

    notice = SimpleNamespace(
        user_id=123, event="test:x", text='<tg-emoji emoji-id="1">✅</tg-emoji> <b>Готово</b> &amp; всё'
    )
    error = TelegramBadRequest(method=SendMessage(chat_id=123, text="x"), message="can't parse entities")
    bot = SimpleNamespace(send_message=AsyncMock(side_effect=[error, None]))
    await worker.deliver(bot, notice)
    assert bot.send_message.call_args.args[1] == "✅ Готово & всё"


@pytest.mark.asyncio
async def test_worker_loop_survives_transient_errors(monkeypatch):
    import asyncio

    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("database is locked")
        raise asyncio.CancelledError  # stop the endless loop for the test

    async def no_sleep(seconds):
        pass

    monkeypatch.setattr(worker.asyncio, "sleep", no_sleep)
    with pytest.raises(asyncio.CancelledError):
        await worker.guarded("orders", flaky, 0)
    assert calls["n"] == 2  # the first failure was logged and the loop went on


@pytest.mark.asyncio
async def test_only_recipient_errors_drop_a_notice(database):
    from aiogram.exceptions import TelegramBadRequest
    from aiogram.methods import SendMessage

    with database.begin() as db:
        db.add(Notice(user_id=123, event="sms:keep", text="<code>111</code>"))
        db.add(Notice(user_id=123, event="test:gone", text="hi"))
    method = SendMessage(chat_id=123, text="x")

    async def send(chat_id, text, **kwargs):
        if "111" in text:
            raise TelegramBadRequest(method=method, message="Bad Request: BUTTON_DATA_INVALID")
        raise TelegramBadRequest(method=method, message="Bad Request: chat not found")

    await worker.send_notices(SimpleNamespace(send_message=send))
    with database() as db:
        sent = {n.event: n.sent for n in db.scalars(select(Notice))}
    assert sent == {"sms:keep": False, "test:gone": True}


@pytest.mark.asyncio
async def test_active_numbers_are_checked_in_parallel(database, monkeypatch):
    import asyncio
    import time as clock

    with database.begin() as db:
        for i in range(16):
            db.add(
                Order(
                    id=f"{i:032x}",
                    request_key=f"par-{i}",
                    user_id=123,
                    product_id=1,
                    status="active",
                    price=1,
                    cost=1,
                    phone=f"+1555000{i:04d}",
                )
            )

    async def slow_status(order):
        await asyncio.sleep(0.2)
        return State("active")

    monkeypatch.setattr(services, "supplier", SimpleNamespace(status=slow_status))
    started = clock.monotonic()
    await worker.cycle(None)
    # 16 checks x 0.2 s: one by one would take 3.2 s, with 8 at a time about 0.4 s.
    assert clock.monotonic() - started < 1.5


@pytest.mark.parametrize(
    "answer, expected",
    [
        ({"code": 1, "data": {"code": "48512", "code_time": "t1"}}, ("48512", "t1")),
        ({"code": "1", "data": {"code": 48512}}, ("48512", "")),
        ({"code": 1, "data": {"code": "01234"}}, ("01234", "")),
        (
            {"code": 1, "data": {"code": "", "sms": "Telegram code: 48512. Do not give it to anyone"}},
            ("48512", ""),
        ),
        ({"code": 1, "data": {"content": "Your code is 123-456"}}, ("123456", "")),
        ({"code": 1, "data": {"code": "Telegram code 77123"}}, ("77123", "")),
        ({"code": 0, "data": {"code": ""}}, ("", "")),
        ({"code": 0, "data": {"code": None, "expired_date": "2027-01-01"}}, ("", "")),
    ],
)
def test_sms_code_is_found_in_any_supplier_format(answer, expected):
    from app.supplier import parse_sms

    assert parse_sms(answer) == expected


def test_unknown_sms_answer_is_logged_without_the_link(caplog):
    from app.supplier import parse_sms

    with pytest.raises(ValueError):
        parse_sms({"code": 1, "data": {"sms": "no digits here"}})
    assert "Unrecognized SMS answer" in caplog.text and "token" not in caplog.text


@pytest.mark.parametrize(
    "answer, expected",
    [
        ({"code": 1, "data": {"verify_code": 48512}}, "48512"),
        ({"code": 1, "data": {"list": [{"smsText": "Telegram code 55123"}]}}, "55123"),
        ({"code": 1, "data": {"code": "", "tel": "+17752953734", "code_time": "2026-11-03 10:00"}}, ""),
        ({"code": 1, "data": {"count": 12345, "expired_date": "2027-01-01"}}, ""),
    ],
)
def test_code_in_unknown_fields_but_not_phone_or_dates(answer, expected):
    from app.supplier import parse_sms

    assert parse_sms(answer)[0] == expected


def test_supplier_answer_shown_to_admin_hides_links_and_tokens():
    from app.supplier import sanitized

    raw = {
        "api": "https://api.sms8.net/api/record?token=secret",
        "token": "abc",
        "tel": "+1555",
        "x": "https://y",
    }
    assert sanitized(raw) == {"api": "•••", "token": "•••", "tel": "+1555", "x": "•••"}
