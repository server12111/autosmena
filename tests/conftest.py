import pytest
from sqlalchemy.orm import sessionmaker

from app.db import make_engine
from app.models import Base, Product, User
from app.security import password_hash


@pytest.fixture
def database(tmp_path, monkeypatch):
    from app import api, bot, bot_admin, db, payments, reseller, services, worker
    from app.config import settings

    engine = make_engine("sqlite:///" + str(tmp_path / "test.db"))
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    for module in (api, bot, bot_admin, db, payments, reseller, services, worker):
        monkeypatch.setattr(module, "Session", factory)
    monkeypatch.setattr(settings, "demo_mode", True)
    monkeypatch.setattr(settings, "admin_password_hash", password_hash("test-password-123"))
    from app.supplier import DemoSupplier

    monkeypatch.setattr(services, "supplier", DemoSupplier())
    with factory.begin() as db:
        db.add(User(id=123, username="tester"))
        db.add(
            Product(id=1, supplier_code="demo:us:tg", name="Test", country="US", cost=1000000, markup_bps=0)
        )
    yield factory
    engine.dispose()
