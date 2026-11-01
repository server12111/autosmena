"""Reseller REST API. Keys are created in the bot: Профиль → API.

Authorization: Bearer <key>. Purchases are paid from the same balance as in the bot.
"""

import json
import secrets
import time
from collections import defaultdict, deque
from hashlib import sha256

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.catalog_policy import allowed_codes
from app.config import settings
from app.db import Session
from app.models import ApiKey, Order, Product, User, now
from app.money import display, sale_price
from app.services import allocate_order, refresh_order, reserve_order

KEY_PREFIX = "smn_"
RATE_LIMIT = 60  # requests per minute per key
hits = defaultdict(deque)


def key_digest(key):
    return sha256(key.encode()).hexdigest()


def issue_key(user_id):
    """Creates or replaces the user's key. The full key is returned once and never stored."""
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    with Session.begin() as db:
        record = db.get(ApiKey, user_id)
        if record is None:
            record = ApiKey(user_id=user_id, key_hash="", prefix="")
            db.add(record)
        record.key_hash, record.prefix, record.created_at, record.last_used_at = (
            key_digest(key),
            key[:10],
            now(),
            None,
        )
    return key


def key_info(user_id):
    with Session() as db:
        return db.get(ApiKey, user_id)


def public_url():
    from app import webhooks

    try:
        configured = webhooks.configuration().get("base_url")
    except Exception:
        configured = ""
    return (configured or settings.public_url).rstrip("/")


def reseller(authorization: str = Header(default=""), x_api_key: str = Header(default="")):
    key = x_api_key or (authorization[7:] if authorization.lower().startswith("bearer ") else "")
    if not key.startswith(KEY_PREFIX):
        raise HTTPException(401, "API key required: Authorization: Bearer <key>")
    with Session.begin() as db:
        record = db.scalar(select(ApiKey).where(ApiKey.key_hash == key_digest(key)))
        if not record:
            raise HTTPException(401, "Invalid API key")
        user = db.get(User, record.user_id)
        if not user or user.blocked:
            raise HTTPException(403, "Account is blocked")
        if not record.last_used_at or now() - record.last_used_at >= 60:
            record.last_used_at = now()
        user_id = record.user_id
    window = hits[user_id]
    moment = time.monotonic()
    while window and moment - window[0] > 60:
        window.popleft()
    if len(window) >= RATE_LIMIT:
        raise HTTPException(429, "Too many requests: 60 per minute")
    window.append(moment)
    return user_id


def order_view(order):
    payload = json.loads(order.supplier_payload or "{}")
    return {
        "id": order.id,
        "status": order.status,
        "phone": order.phone or None,
        "code": order.sms or None,
        "price": display(order.price),
        "expires_at": payload.get("end_time"),
        "created_at": order.created_at,
    }


class Purchase(BaseModel):
    product_id: int
    request_id: str = Field(
        default_factory=lambda: secrets.token_hex(12),
        min_length=8,
        max_length=48,
        pattern=r"^[A-Za-z0-9_-]+$",
    )


def install(app):
    api = APIRouter(prefix="/reseller/v1")

    @api.get("/balance")
    def get_balance(user_id: int = Depends(reseller)):
        with Session() as db:
            return {"balance": display(db.get(User, user_id).balance), "currency": "USD"}

    @api.get("/products")
    def get_products(user_id: int = Depends(reseller)):
        with Session() as db:
            rows = db.scalars(
                select(Product)
                .where(
                    Product.enabled.is_(True),
                    Product.kind != "renew",
                    Product.supplier_code.in_(allowed_codes()),
                )
                .order_by(Product.cost, Product.id)
            ).all()
        return {
            "products": [
                {"id": p.id, "name": "Telegram USA +1", "kind": p.kind, "price": display(sale_price(p))}
                for p in rows
            ]
        }

    @api.post("/orders")
    async def create_order(body: Purchase, user_id: int = Depends(reseller)):
        with Session() as db:
            product = db.get(Product, body.product_id)
            if not product or not product.enabled or product.supplier_code not in allowed_codes():
                raise ValueError("Product is not available")
            price = sale_price(product)
        order = reserve_order(user_id, body.product_id, f"api:{user_id}:{body.request_id}", price)
        await allocate_order(order.id)
        with Session() as db:
            return order_view(db.get(Order, order.id))

    @api.get("/orders")
    def list_orders(offset: int = 0, user_id: int = Depends(reseller)):
        with Session() as db:
            rows = db.scalars(
                select(Order)
                .where(Order.user_id == user_id)
                .order_by(Order.created_at.desc(), Order.id.desc())
                .offset(max(0, offset))
                .limit(50)
            ).all()
        return {"orders": [order_view(o) for o in rows]}

    @api.get("/orders/{order_id}")
    async def get_order(order_id: str, user_id: int = Depends(reseller)):
        with Session() as db:
            order = db.get(Order, order_id)
            if not order or order.user_id != user_id:
                raise HTTPException(404, "Order not found")
        try:
            await refresh_order(order_id, user_id)
        except Exception:
            pass  # Supplier hiccup: return the last known state.
        with Session() as db:
            return order_view(db.get(Order, order_id))

    app.include_router(api)
