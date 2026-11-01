"""Durable inbox: acknowledge quickly, verify payments independently, retry safely."""

import hashlib
import hmac
import json
import logging
from urllib.parse import urlsplit

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import IntegrityError

from app import db
from app.config import settings
from app.models import AppSetting, Audit, Payment, WebhookEvent, now

log = logging.getLogger("smena.webhooks")


def configuration():
    with db.Session() as session:
        values = {
            row.key: row.value
            for row in session.scalars(select(AppSetting).where(AppSetting.key.startswith("webhook_")))
        }
    return {
        "base_url": values.get("webhook_base_url", ""),
        "telegram_enabled": values.get("webhook_telegram_enabled") == "true",
        "telegram_status": values.get("webhook_telegram_status", "polling"),
    }


def save_configuration(base_url, telegram_enabled):
    base_url = base_url.strip().rstrip("/")
    parsed = urlsplit(base_url)
    if base_url and (
        parsed.scheme != "https"
        or not parsed.hostname
        or "." not in parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.port not in (None, 443)
    ):
        raise ValueError("Укажите HTTPS-домен без пути, параметров и пароля")
    if telegram_enabled and (not base_url or not settings.bot_token or settings.demo_mode):
        raise ValueError("Для вебхука Telegram нужны HTTPS-домен и подключённый рабочий бот")
    with db.Session.begin() as session:
        for key, value in {
            "webhook_base_url": base_url,
            "webhook_telegram_enabled": "true" if telegram_enabled else "false",
        }.items():
            session.merge(AppSetting(key=key, value=value))
        session.add(
            Audit(action="webhooks.configure", detail=f"domain={base_url}; telegram={telegram_enabled}")
        )


def telegram_secret():
    # Distinct from the bot token, deterministic across processes and restarts.
    return hmac.new(settings.bot_token.encode(), b"smena:telegram-webhook:v1", hashlib.sha256).hexdigest()


def set_telegram_status(value):
    with db.Session.begin() as session:
        session.merge(AppSetting(key="webhook_telegram_status", value=value))


def enqueue(provider, event_key, payload):
    try:
        with db.Session.begin() as session:
            session.add(WebhookEvent(provider=provider, event_key=event_key, payload=json.dumps(payload)))
        return True
    except IntegrityError:
        with db.Session() as session:
            if session.scalar(
                select(WebhookEvent.id).where(
                    WebhookEvent.provider == provider, WebhookEvent.event_key == event_key
                )
            ):
                return False
        raise


def claim(providers):
    with db.Session.begin() as session:
        # Safe to reprocess: Telegram purchase callbacks and ledger entries are idempotent.
        session.execute(
            update(WebhookEvent)
            .where(
                WebhookEvent.provider.in_(providers),
                WebhookEvent.status == "processing",
                WebhookEvent.updated_at < now() - 120,
            )
            .values(status="pending", available_at=now())
        )
        row = session.scalar(
            select(WebhookEvent)
            .where(
                WebhookEvent.provider.in_(providers),
                WebhookEvent.status == "pending",
                WebhookEvent.available_at <= now(),
            )
            .order_by(WebhookEvent.id)
            .limit(1)
        )
        if row is None:
            return None
        changed = session.execute(
            update(WebhookEvent)
            .where(WebhookEvent.id == row.id, WebhookEvent.status == "pending")
            .values(status="processing", attempts=WebhookEvent.attempts + 1, updated_at=now())
        ).rowcount
        if not changed:
            return None
        session.refresh(row)
        return row


def finish(event, failed=False):
    with db.Session.begin() as session:
        if failed:
            session.execute(
                update(WebhookEvent)
                .where(WebhookEvent.id == event.id)
                .values(
                    status="review" if event.attempts >= 10 else "pending",
                    available_at=now() + min(300, 2 ** min(event.attempts, 8)),
                    updated_at=now(),
                )
            )
        else:
            # Retain the identity for redeliveries; remove customer message content.
            session.execute(
                update(WebhookEvent)
                .where(WebhookEvent.id == event.id)
                .values(
                    status="done",
                    payload="{}",
                    updated_at=now(),
                )
            )


async def process_payments(limit=20):
    from app.payments import check_payment

    for _ in range(limit):
        event = claim(["cryptopay", "xrocket"])
        if event is None:
            break
        try:
            payload = json.loads(event.payload)
            with db.Session() as session:
                query = select(Payment.id).where(
                    Payment.provider == event.provider,
                    Payment.status.in_(["pending", "expired"]),
                )
                if event.provider == "cryptopay":
                    query = query.where(Payment.external_id == payload["external_id"])
                else:
                    # Legacy envelope is not documented. A signed event wakes reconciliation;
                    # no money is credited from its fields or an assumed event layout.
                    query = query.where(Payment.created_at > now() - 30 * 86400)
                payment_ids = session.scalars(query).all()
                creating = session.scalar(
                    select(Payment.id)
                    .where(Payment.provider == event.provider, Payment.status == "creating")
                    .limit(1)
                )
            if not payment_ids and creating:
                raise RuntimeError("Invoice creation in progress")
            for payment_id in payment_ids:
                await check_payment(payment_id)
        except Exception as exc:
            log.warning("Webhook processing failed: id=%s type=%s", event.id, type(exc).__name__)
            finish(event, failed=True)
        else:
            finish(event)


def queue_status():
    with db.Session() as session:
        return dict(
            session.execute(select(WebhookEvent.status, func.count()).group_by(WebhookEvent.status)).all()
        )


def cleanup():
    with db.Session.begin() as session:
        session.execute(
            delete(WebhookEvent).where(
                WebhookEvent.status == "done", WebhookEvent.updated_at < now() - 30 * 86400
            )
        )
