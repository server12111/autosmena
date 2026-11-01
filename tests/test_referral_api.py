from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app import bot, reseller
from app.models import Notice, Payment, User, now
from app.services import credit_payment, ensure_user, referral_stats


@pytest.fixture
def api(database, monkeypatch):
    from app import api as api_module

    monkeypatch.setattr(reseller, "Session", database)
    reseller.hits.clear()
    return TestClient(api_module.app)


def test_referrer_is_attached_only_to_new_users(database):
    assert ensure_user(500, "new", referrer_id=123)
    assert not ensure_user(500, "new", referrer_id=999)
    ensure_user(501, "self", referrer_id=501)
    with database() as db:
        assert db.get(User, 500).referrer_id == 123
        assert db.get(User, 501).referrer_id is None


@pytest.mark.asyncio
async def test_start_link_registers_referral(database):
    message = SimpleNamespace(answer=AsyncMock(), from_user=SimpleNamespace(id=600, username="friend"))
    state = SimpleNamespace(clear=AsyncMock())
    await bot.start(message, state, SimpleNamespace(args="ref_123"))
    with database() as db:
        assert db.get(User, 600).referrer_id == 123


def test_referrer_gets_five_percent_of_each_top_up_once(database):
    ensure_user(700, "friend", referrer_id=123)
    with database.begin() as db:
        db.add(
            Payment(
                id="p" * 32,
                user_id=700,
                provider="demo",
                amount=10_000_000,
                status="pending",
                expires_at=now() + 60,
            )
        )
    assert credit_payment("p" * 32)
    assert not credit_payment("p" * 32)
    with database() as db:
        assert db.get(User, 123).balance == 500_000
        assert db.get(User, 700).balance == 10_000_000
        assert db.scalar(select(Notice).where(Notice.event == "ref:" + "p" * 32)).user_id == 123
    assert referral_stats(123) == (1, 500_000)


def test_api_requires_valid_key(api):
    assert api.get("/reseller/v1/balance").status_code == 401
    assert api.get("/reseller/v1/balance", headers={"Authorization": "Bearer smn_wrong"}).status_code == 401


def test_reissued_key_revokes_old_one(api):
    old = reseller.issue_key(123)
    new = reseller.issue_key(123)
    assert api.get("/reseller/v1/balance", headers={"Authorization": "Bearer " + old}).status_code == 401
    response = api.get("/reseller/v1/balance", headers={"Authorization": "Bearer " + new})
    assert response.json() == {"balance": "0.00", "currency": "USD"}


def test_api_purchase_is_idempotent_and_charges_balance(api, database):
    with database.begin() as db:
        db.get(User, 123).balance = 5_000_000
    headers = {"Authorization": "Bearer " + reseller.issue_key(123)}
    products = api.get("/reseller/v1/products", headers=headers).json()["products"]
    assert products == [{"id": 1, "name": "Telegram USA +1", "kind": "new", "price": "1.00"}]
    body = {"product_id": 1, "request_id": "client-order-1"}
    first = api.post("/reseller/v1/orders", json=body, headers=headers).json()
    second = api.post("/reseller/v1/orders", json=body, headers=headers).json()
    assert first["id"] == second["id"] and first["phone"].startswith("+1555")
    assert api.get("/reseller/v1/balance", headers=headers).json()["balance"] == "4.00"
    assert api.get("/reseller/v1/orders/" + first["id"], headers=headers).json()["status"] in (
        "waiting",
        "active",
    )
    other = {"Authorization": "Bearer " + reseller.issue_key(500 if ensure_user(500) else 500)}
    assert api.get("/reseller/v1/orders/" + first["id"], headers=other).status_code == 404


def test_api_rejects_blocked_users_and_insufficient_balance(api, database):
    headers = {"Authorization": "Bearer " + reseller.issue_key(123)}
    response = api.post("/reseller/v1/orders", json={"product_id": 1}, headers=headers)
    assert response.status_code == 400 and "Недостаточно" in response.json()["detail"]
    with database.begin() as db:
        db.get(User, 123).blocked = True
    assert api.get("/reseller/v1/balance", headers=headers).status_code == 403
