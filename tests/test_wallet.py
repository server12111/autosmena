from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import func, select

from app.models import Ledger, Order, Payment, User, now
from app.services import adjust_balance, allocate_order, credit_payment, refresh_order, reserve_order


def balance(factory):
    with factory() as db:
        return db.get(User, 123).balance


def fund():
    adjust_balance(123, 2000000, "initial-funding", "test")


def test_double_payment_credits_once(database):
    with database.begin() as db:
        payment = Payment(
            user_id=123, provider="demo", amount=3000000, status="pending", expires_at=now() + 100
        )
        db.add(payment)
        db.flush()
        payment_id = payment.id
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: credit_payment(payment_id), range(12)))
    assert sum(results) == 1
    assert balance(database) == 3000000


def test_concurrent_orders_cannot_overspend(database):
    fund()

    def purchase(index):
        try:
            return reserve_order(123, 1, "buy-" + str(index), 1000000)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=6) as pool:
        result = list(pool.map(purchase, range(10)))
    assert len([item for item in result if item]) == 2
    assert balance(database) == 0


def test_repeated_purchase_is_idempotent(database):
    fund()
    with ThreadPoolExecutor(max_workers=6) as pool:
        orders = list(pool.map(lambda _: reserve_order(123, 1, "same-purchase", 1000000), range(10)))
    assert len({order.id for order in orders}) == 1
    assert balance(database) == 1000000


@pytest.mark.asyncio
async def test_cancel_refunds_once(database):
    fund()
    order = reserve_order(123, 1, "cancel-test", 1000000)
    await allocate_order(order.id)
    await refresh_order(order.id, 123, cancel=True)
    await refresh_order(order.id, 123, cancel=True)
    assert balance(database) == 2000000
    with database() as db:
        assert db.get(Order, order.id).status == "refunded"
        assert (
            db.scalar(select(func.count()).select_from(Ledger).where(Ledger.event == "refund:" + order.id))
            == 1
        )


@pytest.mark.asyncio
async def test_ambiguous_supplier_response_holds_funds(database, monkeypatch):
    from app import services

    class TimeoutSupplier:
        ready = True

        async def allocate(self, *_):
            raise TimeoutError()

    monkeypatch.setattr(services, "supplier", TimeoutSupplier())
    fund()
    order = reserve_order(123, 1, "timeout-test", 1000000)
    await allocate_order(order.id)
    await allocate_order(order.id)
    with database() as db:
        assert db.get(Order, order.id).status == "review"
    assert balance(database) == 1000000


@pytest.mark.asyncio
async def test_catalog_timeout_refunds_without_buying(database, monkeypatch):
    from app import services

    class CatalogTimeoutSupplier:
        ready = True
        called = False

        async def check_cost(self, *_):
            raise TimeoutError()

        async def allocate(self, *_):
            self.called = True
            raise AssertionError("Buy must not run after a catalog failure")

    supplier = CatalogTimeoutSupplier()
    monkeypatch.setattr(services, "supplier", supplier)
    fund()
    order = reserve_order(123, 1, "catalog-timeout", 1000000)
    await allocate_order(order.id)
    assert not supplier.called
    assert balance(database) == 2000000
    with database() as db:
        assert db.get(Order, order.id).status == "refunded"


def test_same_onchain_transaction_cannot_credit_two_invoices(database):
    from sqlalchemy.exc import IntegrityError

    with database.begin() as db:
        p1 = Payment(user_id=123, provider="ton", amount=1000000, status="pending", expires_at=now() + 100)
        p2 = Payment(user_id=123, provider="ton", amount=2000000, status="pending", expires_at=now() + 100)
        db.add_all([p1, p2])
        db.flush()
    credit_payment(p1.id, "same-transaction")
    with pytest.raises(IntegrityError):
        credit_payment(p2.id, "same-transaction")
    assert balance(database) == 1000000
    with database() as db:
        assert db.get(Payment, p2.id).status == "pending"


def test_price_change_requires_new_confirmation(database):
    fund()
    with pytest.raises(ValueError, match="Цена изменилась"):
        reserve_order(123, 1, "price-test", 999999)
    assert balance(database) == 2000000


def test_blocked_user_cannot_buy(database):
    fund()
    with database.begin() as db:
        db.get(User, 123).blocked = True
    with pytest.raises(ValueError):
        reserve_order(123, 1, "blocked-test", 1000000)


def test_ledger_reconciles(database):
    fund()
    reserve_order(123, 1, "ledger-test", 1000000)
    with database() as db:
        total = db.scalar(select(func.sum(Ledger.delta)).where(Ledger.user_id == 123))
    assert total == balance(database)


def test_concurrent_admin_adjustment_returns_same_success(database):
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: adjust_balance(123, 2000000, "same-adjustment", "test"), range(16)))
    assert balance(database) == 2000000
