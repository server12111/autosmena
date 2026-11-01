import base64
import json
from decimal import ROUND_UP, Decimal, InvalidOperation
from urllib.parse import quote

import httpx
from pytoniq_core import Address, Cell
from sqlalchemy import select, update

from app.config import settings
from app.db import Session
from app.models import Audit, Payment, User, now
from app.money import SCALE, display
from app.services import credit_payment


async def request_json(method, url, **kwargs):
    # No automatic retries for invoice creation.
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.request(method, url, **kwargs)
        response.raise_for_status()
        return json.loads(response.text, parse_float=Decimal)


async def crypto_call(method, params=None):
    data = await request_json(
        "POST",
        f"{settings.cryptopay_url}/{method}",
        headers={"Crypto-Pay-API-Token": settings.cryptopay_token},
        json=params or {},
    )
    if not data.get("ok"):
        raise ValueError("Crypto Pay временно недоступен")
    return data["result"]


async def rocket_call(method, path, body=None):
    kwargs = {"headers": {"Rocket-Pay-Key": settings.xrocket_token}}
    if body is not None:
        kwargs["json"] = body
    data = await request_json(method, settings.xrocket_url + path, **kwargs)
    if not data.get("success"):
        raise ValueError("xRocket временно недоступен")
    return data["data"]


def available_methods():
    if settings.demo_mode:
        return ["demo"]
    if not settings.apicc_api_key:
        return []
    return (
        (["cryptopay"] if settings.cryptopay_token else [])
        + (["xrocket"] if settings.xrocket_token else [])
        + (["ton"] if settings.ton_wallet and settings.toncenter_api_key and settings.cryptopay_token else [])
    )


async def create_payment(user_id, provider, amount):
    if provider not in available_methods():
        raise ValueError("Способ оплаты не подключён")
    if not 1 * SCALE <= amount <= 10000 * SCALE:
        raise ValueError("Пополнение от $1 до $10000")
    with Session.begin() as db:
        # Serialize invoice creation per user, including on SQLite (FOR UPDATE is ignored there).
        changed = db.execute(
            update(User).where(User.id == user_id, User.blocked.is_(False)).values(balance=User.balance)
        ).rowcount
        if changed != 1:
            raise ValueError("Аккаунт недоступен")
        if db.scalar(
            select(Payment.id)
            .where(
                Payment.user_id == user_id,
                Payment.status.in_(["creating", "pending"]),
                Payment.expires_at > now(),
            )
            .limit(1)
        ):
            raise ValueError("У вас уже есть активный счёт. Откройте /payments")
        payment = Payment(
            user_id=user_id, provider=provider, amount=amount, expires_at=now() + settings.payment_ttl_seconds
        )
        db.add(payment)
        db.flush()
    try:
        external_id, url, asset, crypto_amount = None, "", "USD", ""
        if provider == "cryptopay":
            result = await crypto_call(
                "createInvoice",
                {
                    "currency_type": "fiat",
                    "fiat": "USD",
                    "amount": display(amount),
                    "accepted_assets": "USDT,TON",
                    "payload": payment.reference,
                    "description": "Пополнение баланса",
                    "expires_in": settings.payment_ttl_seconds,
                },
            )
            external_id, url = str(result["invoice_id"]), result["bot_invoice_url"]
        elif provider == "xrocket":
            # Legacy API requires JSON numbers; amounts are limited to exact cents.
            result = await rocket_call(
                "POST",
                "/tg-invoices",
                {
                    "amount": float(Decimal(amount) / SCALE),
                    "currency": "USDT",
                    "numPayments": 1,
                    "payload": payment.reference,
                    "expiredIn": settings.payment_ttl_seconds,
                    "description": "Пополнение баланса",
                },
            )
            external_id, url, asset, crypto_amount = (
                str(result["id"]),
                result["link"],
                "USDT",
                display(amount),
            )
        elif provider == "ton":
            rates = await crypto_call("getExchangeRates")
            rate = next(
                Decimal(str(r["rate"]))
                for r in rates
                if r["source"] == "TON" and r["target"] == "USD" and r.get("is_valid")
            )
            if not rate.is_finite() or rate <= 0:
                raise ValueError("Не удалось получить курс TON")
            nano = int((Decimal(amount) / SCALE / rate * 10**9).to_integral_value(rounding=ROUND_UP))
            asset, crypto_amount = "TON", str(Decimal(nano) / 10**9)
            # Address validates checksum; normalize before constructing a payment URI.
            address = Address(settings.ton_wallet).to_str(
                is_bounceable=False, is_test_only=settings.ton_testnet
            )
            scheme = "ton-testnet" if settings.ton_testnet else "ton"
            url = f"{scheme}://transfer/{address}?amount={nano}&text={quote(payment.reference)}"
        with Session.begin() as db:
            current = db.get(Payment, payment.id)
            current.external_id, current.url = external_id, url
            current.asset, current.crypto_amount, current.status = asset, crypto_amount, "pending"
            return current
    except Exception:
        with Session.begin() as db:
            db.get(Payment, payment.id).status = "review"
        raise ValueError("Не удалось создать счёт. Обратитесь в поддержку; списания с баланса нет.") from None


def invoice_identity_matches(payment, data, external_id=None):
    external_id = external_id if external_id is not None else payment.external_id
    try:
        if payment.provider == "cryptopay":
            return (
                str(data.get("invoice_id")) == external_id
                and data.get("currency_type") == "fiat"
                and data.get("fiat") == "USD"
                and Decimal(str(data.get("amount", 0))) == Decimal(payment.amount) / SCALE
                and data.get("payload") == payment.reference
            )
        if payment.provider == "xrocket":
            return (
                str(data.get("id")) == external_id
                and data.get("currency") == "USDT"
                and Decimal(str(data.get("amount", 0))) == Decimal(payment.amount) / SCALE
                and data.get("payload") == payment.reference
            )
    except (InvalidOperation, ValueError, TypeError):
        return False
    return False


def provider_matches(payment, data):
    if not invoice_identity_matches(payment, data) or data.get("status") != "paid":
        return False
    if payment.provider == "cryptopay":
        return True
    if payment.provider == "xrocket":
        try:
            return any(
                Decimal(str(p.get("paymentAmount", 0))) >= Decimal(payment.amount) / SCALE
                for p in data.get("payments", [])
            )
        except (InvalidOperation, ValueError, TypeError):
            return False
    return False


def recover_stale_payments():
    with Session.begin() as db:
        db.execute(
            update(Payment)
            .where(Payment.status == "creating", Payment.created_at < now() - 120)
            .values(status="review")
        )


async def recover_invoice(payment_id, external_id):
    """Attach an invoice after a crashed creation, verifying ownership with the stored random payload."""
    with Session() as db:
        payment = db.get(Payment, payment_id)
        if (
            not payment
            or payment.status not in ("creating", "review")
            or payment.provider not in ("cryptopay", "xrocket")
        ):
            raise ValueError("Этот счёт не ожидает сверки")
    try:
        if payment.provider == "cryptopay":
            result = await crypto_call("getInvoices", {"invoice_ids": external_id})
            data = next((p for p in result["items"] if str(p["invoice_id"]) == external_id), {})
            url = data.get("bot_invoice_url", "")
        else:
            data = await rocket_call("GET", f"/tg-invoices/{external_id}")
            url = data.get("link", "")
    except Exception:
        raise ValueError("Не удалось прочитать счёт у провайдера. Попробуйте позже") from None
    if not invoice_identity_matches(payment, data, external_id):
        raise ValueError("Не совпали сумма, валюта или payload платежа")
    with Session.begin() as db:
        claimed = db.execute(
            update(Payment)
            .where(Payment.id == payment_id, Payment.status.in_(["creating", "review"]))
            .values(
                external_id=external_id,
                url=url,
                status="pending",
                asset="USDT" if payment.provider == "xrocket" else "USD",
                crypto_amount=display(payment.amount) if payment.provider == "xrocket" else "",
            )
        ).rowcount
        if not claimed:
            raise ValueError("Счёт уже обработан. Обновите историю")
        db.add(Audit(action="payment.recover", detail=f"payment={payment_id}; invoice={external_id}"))
        payment = db.get(Payment, payment_id)
    if provider_matches(payment, data):
        credit_payment(payment_id)
    return {"ok": True}


def raw_address(value):
    return Address(value).to_str(is_user_friendly=False).lower()


def ton_matches(payment, tx, final_seqno):
    try:
        message = tx["in_msg"]
        if not message.get("source") or message.get("bounced", False):
            return False
        if raw_address(tx["account"]) != raw_address(settings.ton_wallet):
            return False
        if raw_address(message["destination"]) != raw_address(settings.ton_wallet):
            return False
        if tx["description"]["aborted"] is not False or tx["mc_block_seqno"] > final_seqno - 1:
            return False
        if not payment.created_at <= tx["now"] <= payment.expires_at:
            return False
        if int(message["value"]) < int(Decimal(payment.crypto_amount) * 10**9):
            return False
        content = message["message_content"]
        body = Cell.one_from_boc(base64.b64decode(content["body"])).begin_parse()
        if body.load_uint(32) != 0:
            return False
        return body.load_snake_string() == payment.reference
    except (KeyError, ValueError, TypeError, IndexError, AttributeError):
        return False


async def check_payment(payment_id, user_id=None):
    with Session() as db:
        payment = db.get(Payment, payment_id)
        if not payment or (user_id is not None and payment.user_id != user_id):
            raise ValueError("Счёт не найден")
        if payment.status not in ("pending", "expired"):
            return payment.status
    if payment.provider == "cryptopay":
        data = await crypto_call("getInvoices", {"invoice_ids": payment.external_id})
        if any(provider_matches(payment, item) for item in data["items"]):
            credit_payment(payment.id)
    elif payment.provider == "xrocket":
        data = await rocket_call("GET", f"/tg-invoices/{payment.external_id}")
        if provider_matches(payment, data):
            credit_payment(payment.id)
    elif payment.provider == "ton":
        headers = {"X-API-Key": settings.toncenter_api_key}
        chain = await request_json("GET", settings.toncenter_url + "/masterchainInfo", headers=headers)
        offset = 0
        while True:
            data = await request_json(
                "GET",
                settings.toncenter_url + "/transactions",
                headers=headers,
                params={
                    "account": settings.ton_wallet,
                    "start_utime": payment.created_at,
                    "end_utime": payment.expires_at,
                    "limit": 100,
                    "offset": offset,
                    "sort": "asc",
                },
            )
            for tx in data["transactions"]:
                if ton_matches(payment, tx, chain["last"]["seqno"]):
                    credit_payment(payment.id, tx["hash"])
                    return "paid"
            if len(data["transactions"]) < 100:
                break
            offset += 100
    with Session.begin() as db:
        # A concurrent webhook may already have credited this invoice. Never overwrite 'paid'.
        db.execute(
            update(Payment)
            .where(Payment.id == payment.id, Payment.status == "pending", Payment.expires_at < now())
            .values(status="expired")
        )
        return db.get(Payment, payment.id).status
