from fastapi.testclient import TestClient

from app.api import app
from app.models import Product


def test_supplier_currency_saved_in_db_without_rate_environment_variable(database, monkeypatch):
    from app.config import settings
    from app.preferences import supplier_rate

    monkeypatch.setattr(settings, "apicc_usd_rate", "")
    with database.begin() as db:
        db.get(Product, 1).supplier_code = "2:100:1"
    with TestClient(app) as client:
        csrf = client.post("/api/login", json={"password": "test-password-123"}).json()["csrf"]
        response = client.put(
            "/api/supplier/pricing",
            json={"currency": "USDT", "usd_rate": "1"},
            headers={"X-CSRF-Token": csrf},
        )
        assert response.status_code == 200
        assert client.get("/api/supplier/pricing").json() == {"currency": "USDT", "usd_rate": "1"}
    assert supplier_rate() == 1
    with database() as db:
        assert db.get(Product, 1).enabled is False


def test_bad_supplier_rate_does_not_change_settings(database):
    with TestClient(app) as client:
        csrf = client.post("/api/login", json={"password": "test-password-123"}).json()["csrf"]
        headers = {"X-CSRF-Token": csrf}
        for value in ["NaN", "Infinity", "-1", "0", "abc"]:
            assert (
                client.put(
                    "/api/supplier/pricing", json={"currency": "CNY", "usd_rate": value}, headers=headers
                ).status_code
                == 400
            )
