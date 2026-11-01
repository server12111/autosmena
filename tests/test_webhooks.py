import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app import bot, payments, webhooks
from app.api import app
from app.config import settings
from app.models import Ledger, Payment, User, WebhookEvent, now


@pytest.fixture
def live(database, monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    monkeypatch.setattr(settings, "bot_token", "123456789:test-token-not-real")
    monkeypatch.setattr(settings, "cryptopay_token", "crypto-test-key")
    monkeypatch.setattr(settings, "xrocket_token", "rocket-test-key")
    return database


def signed(raw, token, header="crypto-pay-api-signature"):
    key = hashlib.sha256(token.encode()).digest()
    return {header: hmac.new(key, raw, hashlib.sha256).hexdigest(), "Content-Type": "application/json"}


@pytest.mark.parametrize("provider", ["cryptopay", "xrocket"])
def test_unsigned_or_tampered_webhooks_are_rejected(live, provider):
    token = settings.cryptopay_token if provider == "cryptopay" else settings.xrocket_token
    header = "crypto-pay-api-signature" if provider == "cryptopay" else "rocket-pay-signature"
    with TestClient(app) as client:
        assert client.post("/webhooks/" + provider, json={"id": 1}).status_code == 401
        raw = b'{"id": 1}'
        assert (
            client.post(
                "/webhooks/" + provider, content=raw + b" ", headers=signed(raw, token, header)
            ).status_code
            == 401
        )
    assert webhooks.queue_status() == {}


@pytest.mark.asyncio
async def test_crypto_webhook_is_durable_and_repeated_event_credits_once(live, monkeypatch):
    with live.begin() as db:
        db.add(
            Payment(
                id="a" * 32,
                user_id=123,
                provider="cryptopay",
                amount=2000000,
                external_id="99",
                reference="local-reference",
                status="pending",
                expires_at=now() + 100,
            )
        )
    api = AsyncMock(
        return_value={
            "items": [
                {
                    "invoice_id": 99,
                    "currency_type": "fiat",
                    "fiat": "USD",
                    "amount": "2.00",
                    "payload": "local-reference",
                    "status": "paid",
                }
            ]
        }
    )
    monkeypatch.setattr(payments, "crypto_call", api)
    raw = json.dumps({"update_id": 1, "update_type": "invoice_paid", "payload": {"invoice_id": 99}}).encode()
    with TestClient(app) as client:
        for _ in range(2):
            assert (
                client.post(
                    "/webhooks/cryptopay", content=raw, headers=signed(raw, settings.cryptopay_token)
                ).status_code
                == 200
            )
        api.assert_not_awaited()
        assert webhooks.queue_status() == {"pending": 1}
        with live() as db:
            assert db.get(User, 123).balance == 0
        await webhooks.process_payments()
        assert (
            client.post(
                "/webhooks/cryptopay", content=raw, headers=signed(raw, settings.cryptopay_token)
            ).status_code
            == 200
        )
        await webhooks.process_payments()
    with live() as db:
        assert db.get(User, 123).balance == 2000000
        assert db.scalar(select(func.count()).select_from(Ledger)) == 1
        assert db.scalar(select(WebhookEvent)).payload == "{}"
    api.assert_awaited_once()


@pytest.mark.asyncio
async def test_xrocket_legacy_event_only_triggers_independent_verification(live, monkeypatch):
    with live.begin() as db:
        db.add(
            Payment(
                id="b" * 32,
                user_id=123,
                provider="xrocket",
                amount=2000000,
                external_id="88",
                reference="local-reference",
                status="pending",
                expires_at=now() + 100,
            )
        )
    api = AsyncMock(
        return_value={
            "id": 88,
            "currency": "USDT",
            "amount": "2.00",
            "payload": "local-reference",
            "status": "active",
        }
    )
    monkeypatch.setattr(payments, "rocket_call", api)
    raw = b'{"id":"rocket-1","status":"paid","amount":99999}'
    with TestClient(app) as client:
        assert (
            client.post(
                "/webhooks/xrocket",
                content=raw,
                headers=signed(raw, settings.xrocket_token, "rocket-pay-signature"),
            ).status_code
            == 200
        )
    api.assert_not_awaited()
    await webhooks.process_payments()
    with live() as db:
        assert db.get(User, 123).balance == 0
    api.assert_awaited_once_with("GET", "/tg-invoices/88")


@pytest.mark.asyncio
async def test_failed_provider_read_is_retried_from_durable_queue(live, monkeypatch):
    with live.begin() as db:
        db.add(
            Payment(
                id="c" * 32,
                user_id=123,
                provider="cryptopay",
                amount=1000000,
                external_id="77",
                status="pending",
                expires_at=now() + 100,
            )
        )
    webhooks.enqueue("cryptopay", "retry", {"external_id": "77"})
    api = AsyncMock(side_effect=[RuntimeError("timeout"), {"items": []}])
    monkeypatch.setattr(payments, "crypto_call", api)
    await webhooks.process_payments()
    with live.begin() as db:
        event = db.scalar(select(WebhookEvent))
        assert event.status == "pending" and event.attempts == 1
        event.available_at = now() - 1
    await webhooks.process_payments()
    assert webhooks.queue_status() == {"done": 1}
    assert api.await_count == 2


@pytest.mark.asyncio
async def test_telegram_secret_duplicate_delivery_and_queued_processing(live):
    webhooks.save_configuration("https://shop.example.com", True)
    raw = {
        "update_id": 100,
        "message": {
            "message_id": 1,
            "date": now(),
            "chat": {"id": 123, "type": "private"},
            "from": {"id": 123, "is_bot": False, "first_name": "Test"},
            "text": "/start",
        },
    }
    with TestClient(app) as client:
        assert client.post("/webhooks/telegram", json=raw).status_code == 401
        headers = {"X-Telegram-Bot-Api-Secret-Token": webhooks.telegram_secret()}
        for _ in range(2):
            assert client.post("/webhooks/telegram", json=raw, headers=headers).status_code == 200
    assert webhooks.queue_status() == {"pending": 1}
    dispatcher = SimpleNamespace(feed_update=AsyncMock())
    assert await bot.process_telegram_event(None, dispatcher)
    assert not await bot.process_telegram_event(None, dispatcher)
    dispatcher.feed_update.assert_awaited_once()
    assert dispatcher.feed_update.call_args.args[1].message.text == "/start"
    assert webhooks.queue_status() == {"done": 1}


@pytest.mark.asyncio
async def test_transport_registers_on_startup_and_preserves_pending_updates(live):
    telegram = SimpleNamespace(set_webhook=AsyncMock(), delete_webhook=AsyncMock())
    dispatcher = SimpleNamespace(resolve_used_update_types=lambda: ["message", "callback_query"])
    webhooks.save_configuration("https://shop.example.com", True)
    assert await bot.configure_transport(telegram, dispatcher)
    telegram.set_webhook.assert_awaited_once_with(
        "https://shop.example.com/webhooks/telegram",
        secret_token=webhooks.telegram_secret(),
        allowed_updates=["message", "callback_query"],
        max_connections=1,
        drop_pending_updates=False,
    )
    telegram.delete_webhook.assert_not_awaited()
    webhooks.save_configuration("https://shop.example.com", False)
    assert not await bot.configure_transport(telegram, dispatcher)
    telegram.delete_webhook.assert_awaited_once_with(drop_pending_updates=False)


def test_webhook_admin_settings_require_auth_and_csrf_and_hide_secrets(live):
    with TestClient(app) as client:
        assert client.get("/api/webhooks").status_code == 401
        csrf = client.post("/api/login", json={"password": "test-password-123"}).json()["csrf"]
        body = {"base_url": "https://shop.example.com", "telegram_enabled": True}
        assert client.put("/api/webhooks", json=body).status_code == 403
        assert client.put("/api/webhooks", json=body, headers={"X-CSRF-Token": csrf}).status_code == 200
        data = client.get("/api/webhooks").json()
        assert data["restart_required"]
        assert data["urls"]["xrocket"] == "https://shop.example.com/webhooks/xrocket"
        assert webhooks.telegram_secret() not in json.dumps(data)


@pytest.mark.parametrize(
    "url",
    [
        "http://shop.example.com",
        "https://shop.example.com/path",
        "https://user:pass@shop.example.com",
        "https://shop.example.com?token=secret",
    ],
)
def test_webhook_base_url_rejects_insecure_or_private_components(live, url):
    with pytest.raises(ValueError):
        webhooks.save_configuration(url, True)


def test_oversized_signed_event_is_rejected_before_parsing(live):
    raw = b"x" * 65537
    with TestClient(app) as client:
        assert (
            client.post(
                "/webhooks/cryptopay", content=raw, headers=signed(raw, settings.cryptopay_token)
            ).status_code
            == 413
        )


def test_crashed_inbox_claim_can_be_recovered_after_restart(live):
    webhooks.enqueue("telegram", "stale", {"update_id": 1})
    event = webhooks.claim(["telegram"])
    assert event is not None and webhooks.claim(["telegram"]) is None
    with live.begin() as db:
        db.get(WebhookEvent, event.id).updated_at = now() - 121
    recovered = webhooks.claim(["telegram"])
    assert recovered.id == event.id and recovered.attempts == 2
