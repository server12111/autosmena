import base64
import hashlib
import hmac
from decimal import Decimal
from types import SimpleNamespace

import pytest
from pytoniq_core import Builder

from app.config import settings
from app.money import usd
from app.payments import provider_matches, ton_matches
from app.security import verify_crypto_signature


def test_crypto_signature_requires_exact_raw_body():
    token, body = "test-token", b'{"ok": true}'
    signature = hmac.new(hashlib.sha256(token.encode()).digest(), body, hashlib.sha256).hexdigest()
    assert verify_crypto_signature(body, signature, token)
    assert not verify_crypto_signature(body + b" ", signature, token)
    assert not verify_crypto_signature(body, signature, "")


def test_crypto_amount_currency_and_reference_must_match():
    p = SimpleNamespace(provider="cryptopay", external_id="12", amount=2000000, reference="ref")
    data = {
        "invoice_id": 12,
        "status": "paid",
        "currency_type": "fiat",
        "fiat": "USD",
        "amount": "2.00",
        "payload": "ref",
    }
    assert provider_matches(p, data)
    for key, value in [
        ("fiat", "EUR"),
        ("amount", "1.99"),
        ("payload", "other"),
        ("invoice_id", 13),
        ("status", "active"),
    ]:
        assert not provider_matches(p, {**data, key: value})


def test_ton_requires_final_transaction_amount_comment_and_destination(monkeypatch):
    wallet = "0:" + "a" * 64
    monkeypatch.setattr(settings, "ton_wallet", wallet)
    p = SimpleNamespace(created_at=100, expires_at=200, crypto_amount="1.5", reference="test-ref")
    body = Builder().store_uint(0, 32).store_snake_string("test-ref").end_cell().to_boc()
    tx = {
        "account": wallet,
        "description": {"aborted": False},
        "mc_block_seqno": 10,
        "now": 150,
        "in_msg": {
            "source": "0:" + "b" * 64,
            "destination": wallet,
            "value": "1500000000",
            "bounced": False,
            "message_content": {"body": base64.b64encode(body).decode()},
        },
    }
    assert ton_matches(p, tx, 11)
    assert not ton_matches(p, tx, 10)
    assert not ton_matches(p, {**tx, "now": 201}, 11)
    assert not ton_matches(p, {**tx, "description": {"aborted": True}}, 11)
    assert not ton_matches(p, {**tx, "in_msg": {**tx["in_msg"], "value": "1499999999"}}, 11)
    assert not ton_matches(p, {**tx, "in_msg": {**tx["in_msg"], "destination": "0:" + "c" * 64}}, 11)


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-1", "0", "1.001", "1e100", "abc"])
def test_invalid_money_is_rejected(value):
    with pytest.raises(ValueError):
        usd(value)


def test_money_is_exact():
    assert usd("1.23") == 1230000
    assert Decimal(usd("1.23")) / 1000000 == Decimal("1.23")


def test_rocket_requires_real_payment_and_matching_payload():
    p = SimpleNamespace(
        provider="xrocket", external_id="123", amount=2000000, crypto_amount="2.00", reference="ref"
    )
    data = {
        "id": 123,
        "status": "paid",
        "currency": "USDT",
        "amount": "2.00",
        "payload": "ref",
        "payments": [{"paymentAmount": "2.00"}],
    }
    assert provider_matches(p, data)
    assert not provider_matches(p, {**data, "payments": []})
    assert not provider_matches(p, {**data, "currency": "TONCOIN"})
    assert not provider_matches(p, {**data, "payload": "different"})


@pytest.mark.asyncio
async def test_expiration_cannot_overwrite_a_concurrent_paid_webhook(database, monkeypatch):
    from sqlalchemy import event

    from app import payments
    from app.models import Payment, User, now
    from app.services import credit_payment

    with database.begin() as db:
        payment = Payment(
            user_id=123,
            provider="cryptopay",
            external_id="42",
            amount=2000000,
            status="pending",
            expires_at=now() - 1,
        )
        db.add(payment)
        db.flush()

    async def crypto_call(*_):
        return {"items": []}

    monkeypatch.setattr(payments, "crypto_call", crypto_call)
    injected = False
    engine = database.kw["bind"]

    def concurrent_webhook(conn, cursor, statement, parameters, context, executemany):
        nonlocal injected
        if not injected and statement.startswith("UPDATE payments") and "expired" in parameters:
            injected = True
            credit_payment(payment.id)

    event.listen(engine, "before_cursor_execute", concurrent_webhook)
    try:
        assert await payments.check_payment(payment.id) == "paid"
    finally:
        event.remove(engine, "before_cursor_execute", concurrent_webhook)
    assert injected
    with database() as db:
        assert db.get(Payment, payment.id).status == "paid"
        assert db.get(User, 123).balance == 2000000


def test_concurrent_invoice_creation_allows_one_active_invoice(database):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import func, select

    from app.models import Payment
    from app.payments import create_payment

    def create(_):
        try:
            return asyncio.run(create_payment(123, "demo", 1000000)).id
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(create, range(16)))
    assert len([result for result in results if result]) == 1
    with database() as db:
        assert db.scalar(select(func.count()).select_from(Payment)) == 1


@pytest.mark.asyncio
async def test_recover_invoice_checks_ownership_before_credit(database, monkeypatch):
    from app import payments
    from app.models import Payment, User, now

    with database.begin() as db:
        payment = Payment(
            user_id=123, provider="cryptopay", amount=2000000, status="review", expires_at=now() + 100
        )
        db.add(payment)
        db.flush()
    data = {
        "invoice_id": 42,
        "currency_type": "fiat",
        "fiat": "USD",
        "amount": "2.00",
        "status": "paid",
        "payload": "different",
        "bot_invoice_url": "https://t.me/CryptoBot?start=invoice",
    }

    async def crypto_call(*_):
        return {"items": [data]}

    monkeypatch.setattr(payments, "crypto_call", crypto_call)
    with pytest.raises(ValueError, match="payload"):
        await payments.recover_invoice(payment.id, "42")
    with database() as db:
        assert db.get(User, 123).balance == 0
    data["payload"] = payment.reference
    await payments.recover_invoice(payment.id, "42")
    with pytest.raises(ValueError):
        await payments.recover_invoice(payment.id, "42")
    with database() as db:
        assert db.get(User, 123).balance == 2000000
        assert db.get(Payment, payment.id).status == "paid"
