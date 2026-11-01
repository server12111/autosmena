from fastapi.testclient import TestClient

from app.api import app


def test_admin_auth_csrf_prices_and_audit(database):
    with TestClient(app) as client:
        assert client.get("/api/overview").status_code == 401
        login = client.post("/api/login", json={"password": "test-password-123"})
        assert login.status_code == 200
        assert "HttpOnly" in login.headers["set-cookie"]
        csrf = login.json()["csrf"]
        body = {"markup_percent": 50, "fixed_price": None, "enabled": True}
        assert client.patch("/api/products/1", json=body).status_code == 403
        assert client.patch("/api/products/1", json=body, headers={"X-CSRF-Token": csrf}).status_code == 200
        assert client.get("/api/table/products").json()["rows"][0]["price"] == 1500000
        assert any(r["action"] == "price.update" for r in client.get("/api/table/audit").json()["rows"])
        assert client.get("/").status_code == 200
        assert client.get("/static/app.js").status_code == 200
        assert client.post("/api/logout", headers={"X-CSRF-Token": csrf}).status_code == 200
        assert client.get("/api/overview").status_code == 401


def test_non_demo_payments_cannot_be_manually_credited(database):
    from app.models import Payment, now

    with database.begin() as db:
        p = Payment(user_id=123, provider="ton", amount=1000000, status="pending", expires_at=now() + 100)
        db.add(p)
        db.flush()
    with TestClient(app) as client:
        csrf = client.post("/api/login", json={"password": "test-password-123"}).json()["csrf"]
        assert (
            client.post("/api/demo/payments/" + p.id + "/pay", headers={"X-CSRF-Token": csrf}).status_code
            == 404
        )


def test_review_refund_requires_confirmation_and_is_idempotent(database):
    from app.models import Order, User
    from app.services import adjust_balance, reserve_order

    adjust_balance(123, 2000000, "fund", "test")
    order = reserve_order(123, 1, "manual-review", 1000000)
    with database.begin() as db:
        db.get(Order, order.id).status = "review"
    with TestClient(app) as client:
        csrf = client.post("/api/login", json={"password": "test-password-123"}).json()["csrf"]
        headers = {"X-CSRF-Token": csrf}
        path = "/api/orders/" + order.id + "/review"
        body = {"action": "refund", "reason": "Supplier confirmed no charge"}
        assert client.post(path, json=body, headers=headers).status_code == 400
        body["refund_confirmed"] = True
        assert client.post(path, json=body, headers=headers).status_code == 200
        assert client.post(path, json=body, headers=headers).status_code == 400
    with database() as db:
        assert db.get(User, 123).balance == 2000000


def test_empty_fixed_price_is_not_silently_replaced_by_markup(database):
    with TestClient(app) as client:
        csrf = client.post("/api/login", json={"password": "test-password-123"}).json()["csrf"]
        response = client.patch(
            "/api/products/1",
            json={"markup_percent": 30, "fixed_price": "", "enabled": True},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 400


def test_order_list_is_sorted_by_creation_time_not_random_id(database):
    from app.models import Order

    with database.begin() as db:
        db.add_all(
            [
                Order(
                    id="f" * 32,
                    request_key="older",
                    user_id=123,
                    product_id=1,
                    price=1000000,
                    cost=1000000,
                    created_at=100,
                ),
                Order(
                    id="0" * 32,
                    request_key="newer",
                    user_id=123,
                    product_id=1,
                    price=1000000,
                    cost=1000000,
                    created_at=200,
                ),
            ]
        )
    with TestClient(app) as client:
        client.post("/api/login", json={"password": "test-password-123"})
        assert client.get("/api/table/orders").json()["rows"][0]["id"] == "0" * 32


def test_enabling_service_without_long_term_numbers_is_rejected(database, monkeypatch):
    from app import api
    from app.config import settings
    from app.models import Product
    from app.supplier import Rejected

    with database.begin() as db:
        db.get(Product, 1).supplier_code = "2:74:1"

    class NoStock:
        async def check_rental_stock(self, code):
            raise Rejected("Нет номеров со сроком больше 60 дней")

    monkeypatch.setattr(api, "supplier", NoStock())
    with TestClient(app) as client:
        csrf = client.post("/api/login", json={"password": "test-password-123"}).json()["csrf"]
        monkeypatch.setattr(settings, "demo_mode", False)
        response = client.patch(
            "/api/products/1",
            json={"markup_percent": 30, "fixed_price": None, "enabled": True},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 400
        assert "60" in response.json()["detail"]


def test_renewal_review_requires_confirmation_and_preserves_wallet(database, monkeypatch):
    import json

    from app import api
    from app.config import settings
    from app.models import Order, Product, User
    from app.services import adjust_balance, reserve_order
    from app.supplier import State

    with database.begin() as db:
        db.add(
            Product(
                id=2,
                supplier_code="2:74:3",
                name="Renew",
                country="US",
                kind="renew",
                cost=1000000,
                markup_bps=0,
            )
        )
        db.add(
            Order(
                id="a" * 32,
                request_key="parent",
                user_id=123,
                product_id=1,
                price=1000000,
                cost=1000000,
                status="active",
                phone="+123",
                supplier_id="remote-order",
                supplier_payload=json.dumps({"id": 10, "app_id": 74, "cate_id": 2}),
            )
        )
    adjust_balance(123, 2000000, "fund", "test")
    child = reserve_order(123, 2, "renewal-review", 1000000, "a" * 32)
    with database.begin() as db:
        db.get(Order, child.id).status = "review"

    class ConfirmedSupplier:
        async def status(self, order):
            return State("active", payload={"end_time": "2030-01-01"})

    monkeypatch.setattr(api, "supplier", ConfirmedSupplier())
    with TestClient(app) as client:
        csrf = client.post("/api/login", json={"password": "test-password-123"}).json()["csrf"]
        monkeypatch.setattr(settings, "demo_mode", False)
        body = {"action": "renewed", "reason": "Provider confirmed successful renewal"}
        headers = {"X-CSRF-Token": csrf}
        path = "/api/orders/" + child.id + "/review"
        assert client.post(path, json=body, headers=headers).status_code == 400
        body["renewal_confirmed"] = True
        assert client.post(path, json=body, headers=headers).status_code == 200
    with database() as db:
        assert db.get(User, 123).balance == 1000000
        assert db.get(Order, child.id).status == "completed"
