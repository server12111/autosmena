import json
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from html import escape
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError

from app import webhooks
from app.catalog_policy import US_LABELS, allowed_codes, permitted_code
from app.config import settings
from app.db import Session
from app.models import AdminSession, Audit, Ledger, Notice, Order, Payment, Product, User, now
from app.money import sale_price, usd
from app.payments import recover_invoice
from app.preferences import save_supplier_pricing, supplier_pricing
from app.security import digest, verify_crypto_signature, verify_password
from app.services import adjust_balance, credit_payment, refund_order
from app.supplier import RENTAL_LABEL, Rejected, supplier, sync_catalog


@asynccontextmanager
async def lifespan(app):
    settings.validate_runtime()
    yield


app = FastAPI(title="Smena", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
static = Path(__file__).parent / "static"
app.mount("/static", StaticFiles(directory=static), name="static")
login_attempts = defaultdict(deque)


@app.middleware("http")
async def headers(request, call_next):
    response = await call_next(request)
    response.headers.update(
        {
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "no-referrer",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; frame-ancestors 'none'; base-uri 'self'; form-action 'self'",
        }
    )
    return response


@app.exception_handler(ValueError)
async def invalid(request, exception):
    from fastapi.responses import JSONResponse

    return JSONResponse({"detail": str(exception)}, status_code=400)


@app.exception_handler(IntegrityError)
async def database_conflict(request, exception):
    from fastapi.responses import JSONResponse

    return JSONResponse(
        {"detail": "Операция уже выполнена или конфликтует с другой записью. Обновите историю."},
        status_code=409,
    )


def authenticated(request: Request):
    token = request.cookies.get("smena_session", "")
    with Session() as db:
        session = db.get(AdminSession, digest(token))
        if not session or session.expires_at < now():
            raise HTTPException(401, "Войдите в админку")
        if request.method not in ("GET", "HEAD"):
            if not secrets.compare_digest(request.headers.get("X-CSRF-Token", ""), session.csrf):
                raise HTTPException(403, "Ошибка CSRF. Обновите страницу")
        return session


@app.get("/")
def index():
    return FileResponse(static / "index.html")


@app.get("/health")
def health():
    with Session() as db:
        db.execute(select(1))
    return {"status": "ok"}


class Login(BaseModel):
    password: str = Field(max_length=256)


@app.post("/api/login")
def login(body: Login, request: Request, response: Response):
    host = request.client.host if request.client else "unknown"
    attempts = login_attempts[host]
    while attempts and attempts[0] < time.monotonic() - 900:
        attempts.popleft()
    if len(attempts) >= 5:
        raise HTTPException(429, "Слишком много попыток. Подождите 15 минут")
    attempts.append(time.monotonic())
    if not settings.admin_password_hash:
        raise HTTPException(503, "Создайте пароль: python -m app.cli password")
    if not verify_password(body.password, settings.admin_password_hash):
        raise HTTPException(401, "Неверный пароль")
    attempts.clear()
    token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with Session.begin() as db:
        db.add(AdminSession(token_hash=digest(token), csrf=csrf, expires_at=now() + 8 * 3600))
        db.add(Audit(action="admin.login", detail="Успешный вход"))
    response.set_cookie(
        "smena_session",
        token,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="strict",
        max_age=8 * 3600,
    )
    return {"csrf": csrf}


@app.get("/api/session")
def session(session=Depends(authenticated)):
    return {"csrf": session.csrf, "demo": settings.demo_mode, "supplier_ready": supplier.ready}


@app.post("/api/logout")
def logout(response: Response, session=Depends(authenticated)):
    with Session.begin() as db:
        db.delete(db.get(AdminSession, session.token_hash))
    response.delete_cookie("smena_session")
    return {"ok": True}


def serialize(row):
    return {
        column.name: getattr(row, column.name)
        for column in row.__table__.columns
        if column.name != "supplier_payload"
    }


@app.get("/api/overview", dependencies=[Depends(authenticated)])
def overview():
    with Session() as db:
        paid = db.scalar(select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "paid"))
        revenue = db.scalar(
            select(func.coalesce(func.sum(Order.price), 0)).where(
                Order.status.in_(["completed", "active", "expired"])
            )
        )
        margin = db.scalar(
            select(func.coalesce(func.sum(Order.price - Order.cost), 0)).where(
                Order.status.in_(["completed", "active", "expired"])
            )
        )
        return {
            "users": db.scalar(select(func.count()).select_from(User)),
            "deposits": paid,
            "revenue": revenue,
            "margin": margin,
            "review": db.scalar(select(func.count()).select_from(Order).where(Order.status == "review")),
            "demo": settings.demo_mode,
            "supplier_ready": supplier.ready,
        }


@app.get("/api/table/{name}", dependencies=[Depends(authenticated)])
def table(name: str, offset: int = 0, q: str = ""):
    models = {
        "products": Product,
        "orders": Order,
        "payments": Payment,
        "users": User,
        "ledger": Ledger,
        "audit": Audit,
    }
    model = models.get(name)
    if not model or offset < 0:
        raise HTTPException(404)
    statement = select(model)
    if model is Product:
        statement = statement.where(Product.supplier_code.in_(allowed_codes()))
    if q:
        q = q[:128]
        if model is Product:
            statement = statement.where(Product.name.icontains(q) | Product.country.icontains(q))
        elif model is User:
            statement = statement.where(User.username.icontains(q))
        elif model in (Order, Payment):
            statement = statement.where(model.id.startswith(q))
    with Session() as db:
        total = db.scalar(select(func.count()).select_from(statement.subquery()))
        ordering = (
            [model.created_at.desc(), model.id.desc()] if hasattr(model, "created_at") else [model.id.desc()]
        )
        rows = db.scalars(statement.order_by(*ordering).offset(offset).limit(50)).all()
        result = [serialize(row) for row in rows]
        if model is Product:
            for item, row in zip(result, rows, strict=True):
                item["price"] = sale_price(row)
        return {"rows": result, "total": total}


class ProductChange(BaseModel):
    markup_percent: int = Field(ge=0, le=10000)
    fixed_price: str | None = None
    enabled: bool
    name: str | None = Field(default=None, min_length=1, max_length=128)


class CatalogSync(BaseModel):
    category: int | None = Field(default=None, gt=0)
    label: str = Field(default="США", min_length=1, max_length=64)


class SupplierPricing(BaseModel):
    currency: str = Field(pattern=r"^[A-Z]{3,6}$")
    usd_rate: str = Field(max_length=32)


@app.get("/api/supplier/pricing", dependencies=[Depends(authenticated)])
def pricing():
    return supplier_pricing()


@app.put("/api/supplier/pricing", dependencies=[Depends(authenticated)])
def change_pricing(body: SupplierPricing):
    save_supplier_pricing(body.currency, body.usd_rate)
    return {"ok": True}


@app.get("/api/connections", dependencies=[Depends(authenticated)])
def connections():
    return {
        "supplier": supplier.ready,
        "bot": bool(settings.bot_token),
        "cryptopay": bool(settings.cryptopay_token),
        "xrocket": bool(settings.xrocket_token),
        "ton": bool(settings.ton_wallet and settings.toncenter_api_key and settings.cryptopay_token),
        "demo": settings.demo_mode,
        "rental_term": RENTAL_LABEL,
    }


class OrderReview(BaseModel):
    action: str = Field(pattern="^(attach|refund|renewed)$")
    reason: str = Field(min_length=10, max_length=256)
    supplier_order: str = Field(default="", max_length=128)
    refund_confirmed: bool = False
    renewal_confirmed: bool = False


@app.post("/api/orders/{order_id}/review", dependencies=[Depends(authenticated)])
async def review_order(order_id: str, body: OrderReview):
    with Session() as db:
        order = db.get(Order, order_id)
        if not order or order.status != "review":
            raise ValueError("Заказ не ожидает сверки")
        product = db.get(Product, order.product_id)
    if body.action == "refund":
        if not body.refund_confirmed:
            raise ValueError("Подтвердите отсутствие списания или возврат у поставщика")
        with Session.begin() as db:
            changed = db.execute(
                update(Order).where(Order.id == order_id, Order.status == "review").values(status="refunding")
            ).rowcount
            if changed:
                refund_order(db, db.get(Order, order_id), body.reason)
                db.add(Audit(action="order.review.refund", detail=f"order={order_id}; reason={body.reason}"))
    elif body.action == "renewed":
        if not order.parent_id or not body.renewal_confirmed or settings.demo_mode:
            raise ValueError("Нужно подтвердить успешное продление у поставщика")
        with Session() as db:
            parent = db.get(Order, order.parent_id)
            if not parent or parent.user_id != order.user_id:
                raise ValueError("Исходный номер не найден")
        state = await supplier.status(parent)
        if state.status != "active" or not state.payload.get("end_time"):
            raise ValueError("Поставщик не подтвердил действующую аренду")
        with Session.begin() as db:
            changed = db.execute(
                update(Order)
                .where(Order.id == order_id, Order.status == "review")
                .values(
                    status="completed",
                    supplier_payload=json.dumps(state.payload, ensure_ascii=False, default=str),
                    error="",
                )
            ).rowcount
            if changed:
                db.execute(
                    update(Order)
                    .where(Order.id == parent.id, Order.status.in_(["active", "expired"]))
                    .values(status="active")
                )
                db.add(
                    Audit(
                        action="order.review.renewed",
                        detail=f"order={order_id}; end_time={state.payload['end_time']}; reason={body.reason}",
                    )
                )
                db.add(
                    Notice(
                        user_id=order.user_id,
                        event="review-renewed:" + order.id,
                        text=f"✅ Продление {escape(parent.phone)} подтверждено. Новый срок: {escape(str(state.payload['end_time']))}.",
                    )
                )
    else:
        if settings.demo_mode or not body.supplier_order or order.parent_id:
            raise ValueError("Укажите номер заказа поставщика для исходной аренды")
        data = await supplier.call("/api/v1/order/api", {"ordernum": body.supplier_order})
        if len(data["list"]) != 1:
            raise ValueError("Для привязки нужен заказ поставщика ровно на один номер")
        item = data["list"][0]
        category, app_id, card_type = map(int, product.supplier_code.split(":"))
        if (int(item["app_id"]), int(item["cate_id"]), int(item["type"])) != (app_id, category, card_type):
            raise ValueError("Заказ поставщика не соответствует товару")
        with Session.begin() as db:
            changed = db.execute(
                update(Order)
                .where(Order.id == order_id, Order.status == "review")
                .values(
                    status="active" if int(item["status"]) == 0 else "expired",
                    supplier_id=body.supplier_order,
                    phone=item["tel"],
                    supplier_payload=json.dumps(item, ensure_ascii=False, default=str),
                    error="",
                )
            ).rowcount
            if changed:
                db.add(Audit(action="order.review.attach", detail=f"order={order_id}; reason={body.reason}"))
                db.add(
                    Notice(
                        user_id=order.user_id,
                        event="review-attached:" + order.id,
                        text=f"✅ Заказ {order.id[:8]} восстановлен после сверки. Номер: <code>{escape(str(item['tel']))}</code>.",
                    )
                )
    return {"ok": True}


@app.get("/api/supplier/categories", dependencies=[Depends(authenticated)])
async def supplier_categories():
    if settings.demo_mode:
        raise ValueError("Категории доступны после отключения деморежима")
    return {"list": [{"id": key, "name": value} for key, value in US_LABELS.items()], "total": len(US_LABELS)}


@app.post("/api/supplier/sync", dependencies=[Depends(authenticated)])
async def supplier_sync(body: CatalogSync):
    count = 0
    for category in [body.category] if body.category is not None else US_LABELS:
        count += await sync_catalog(category, body.label)
    return {"count": count}


@app.patch("/api/products/{product_id}", dependencies=[Depends(authenticated)])
async def product_change(product_id: int, body: ProductChange):
    if body.enabled and not settings.demo_mode:
        with Session() as db:
            product = db.get(Product, product_id)
            if not product:
                raise HTTPException(404)
            code = product.supplier_code
        if not permitted_code(code):
            raise ValueError("Можно включить только Telegram-номера США")
        try:
            await supplier.check_rental_stock(code)
        except Rejected as exc:
            raise ValueError(str(exc)) from None
    with Session.begin() as db:
        product = db.get(Product, product_id)
        if not product:
            raise HTTPException(404)
        before = serialize(product)
        if body.name is not None:
            if not body.name.strip():
                raise ValueError("Название не может быть пустым")
            product.name = body.name.strip()
        product.markup_bps = body.markup_percent * 100
        product.fixed_price = usd(body.fixed_price) if body.fixed_price is not None else None
        product.enabled = body.enabled
        db.add(
            Audit(
                action="price.update",
                detail=json.dumps({"before": before, "after": serialize(product)}, ensure_ascii=False),
            )
        )
    return {"ok": True}


class BalanceChange(BaseModel):
    amount: str = Field(max_length=32)
    subtract: bool = False
    reason: str = Field(min_length=3, max_length=256)
    request_id: str = Field(min_length=16, max_length=64)


@app.post("/api/users/{user_id}/balance", dependencies=[Depends(authenticated)])
def balance_change(user_id: int, body: BalanceChange):
    adjust_balance(user_id, usd(body.amount) * (-1 if body.subtract else 1), body.request_id, body.reason)
    return {"ok": True}


class BlockChange(BaseModel):
    blocked: bool


class InvoiceRecovery(BaseModel):
    external_id: str = Field(pattern=r"^[0-9]{1,20}$")


@app.post("/api/payments/{payment_id}/recover", dependencies=[Depends(authenticated)])
async def payment_recovery(payment_id: str, body: InvoiceRecovery):
    return await recover_invoice(payment_id, body.external_id)


@app.patch("/api/users/{user_id}", dependencies=[Depends(authenticated)])
def block(user_id: int, body: BlockChange):
    with Session.begin() as db:
        user = db.get(User, user_id)
        if not user:
            raise HTTPException(404)
        user.blocked = body.blocked
        db.add(Audit(action="user.block", detail=f"user={user_id}; blocked={body.blocked}"))
    return {"ok": True}


@app.post("/api/demo/payments/{payment_id}/pay", dependencies=[Depends(authenticated)])
def demo_pay(payment_id: str):
    if not settings.demo_mode:
        raise HTTPException(404)
    with Session() as db:
        payment = db.get(Payment, payment_id)
        if not payment or payment.provider != "demo":
            raise HTTPException(404)
    credit_payment(payment_id)
    return {"ok": True}


@app.post("/webhooks/cryptopay")
async def crypto_webhook(request: Request):
    if not settings.cryptopay_token or settings.demo_mode:
        raise HTTPException(404)
    raw = await webhook_body(request)
    if not verify_crypto_signature(
        raw, request.headers.get("crypto-pay-api-signature", ""), settings.cryptopay_token
    ):
        raise HTTPException(401)
    data = webhook_json(raw)
    if data.get("update_type") == "invoice_paid":
        payload = data.get("payload")
        if not isinstance(payload, dict) or not isinstance(payload.get("invoice_id"), (int, str)):
            raise HTTPException(400)
        # Crypto Pay documents update_id as non-unique. Each paid invoice is
        # one business event, even if its update ID collides with another invoice.
        key = "invoice_paid:" + str(payload["invoice_id"])
        webhooks.enqueue("cryptopay", digest(key), {"external_id": str(payload["invoice_id"])})
    return {"ok": True}


async def webhook_body(request, maximum=65536):
    # Limit the streamed body before parsing; signatures cover the exact raw bytes.
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > maximum:
            raise HTTPException(413)
        chunks.append(chunk)
    return b"".join(chunks)


def webhook_json(raw):
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError
        return data
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "Неверный JSON") from None


@app.post("/webhooks/xrocket")
async def rocket_webhook(request: Request):
    if not settings.xrocket_token or settings.demo_mode:
        raise HTTPException(404)
    raw = await webhook_body(request)
    # Legacy signing scheme, as documented in xRocket's migration guide.
    if not verify_crypto_signature(
        raw, request.headers.get("rocket-pay-signature", ""), settings.xrocket_token
    ):
        raise HTTPException(401)
    data = webhook_json(raw)
    key = str(data.get("id") or digest(raw.hex()))
    webhooks.enqueue("xrocket", digest(key), {})
    return {"ok": True}


@app.post("/webhooks/telegram")
async def telegram_webhook(request: Request):
    if not settings.bot_token or not webhooks.configuration()["telegram_enabled"]:
        raise HTTPException(404)
    supplied = request.headers.get("x-telegram-bot-api-secret-token", "")
    if not secrets.compare_digest(supplied.encode(), webhooks.telegram_secret().encode()):
        raise HTTPException(401)
    data = webhook_json(await webhook_body(request, maximum=1048576))
    from aiogram.types import Update
    from pydantic import ValidationError

    try:
        update = Update.model_validate(data)
        if type(data.get("update_id")) is not int or data["update_id"] < 0:
            raise ValueError
    except (ValueError, ValidationError):
        raise HTTPException(400, "Неверное обновление Telegram") from None
    webhooks.enqueue("telegram", str(update.update_id), data)
    return {"ok": True}


class WebhookConfiguration(BaseModel):
    base_url: str = Field(default="", max_length=256)
    telegram_enabled: bool = False


@app.get("/api/webhooks", dependencies=[Depends(authenticated)])
def webhook_configuration():
    config = webhooks.configuration()
    base = config["base_url"]
    return {
        **config,
        "urls": {
            name: base + "/webhooks/" + name if base else "" for name in ("telegram", "cryptopay", "xrocket")
        },
        "queue": webhooks.queue_status(),
        "restart_required": ("webhook" if config["telegram_enabled"] else "polling")
        != config["telegram_status"],
    }


@app.put("/api/webhooks", dependencies=[Depends(authenticated)])
def configure_webhooks(body: WebhookConfiguration):
    webhooks.save_configuration(body.base_url, body.telegram_enabled)
    return {
        "ok": True,
        "message": "Настройки сохранены. Перезапустите бота на хостинге для переключения Telegram.",
    }
