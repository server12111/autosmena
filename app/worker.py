import asyncio
import logging
import re
from html import unescape

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError
from sqlalchemy import select

from app import webhooks
from app.bot_ui import PremiumFallback, keyboard, load_icons, premium
from app.config import settings
from app.db import Session
from app.models import Notice, Order, Payment, now
from app.payments import check_payment, recover_stale_payments
from app.services import allocate_order, recover_stale_orders, refresh_order

log = logging.getLogger("smena.worker")
CONCURRENCY = 8


async def cycle(bot=None):
    recover_stale_orders()
    recover_stale_payments()
    with Session() as db:
        orders = [
            (o.id, o.status)
            for o in db.scalars(
                select(Order).where(Order.status.in_(["reserved", "waiting", "active"]))
            ).all()
        ]
        payments = db.scalars(
            select(Payment.id).where(
                Payment.status.in_(["pending", "expired"]),
                Payment.provider != "demo",
                Payment.created_at > now() - 30 * 86400,
            )
        ).all()
    # Active numbers pile up over 60+ days: checking them one by one would delay every code
    # (and every payment behind them) by the whole round. Run them in parallel, bounded.
    limit = asyncio.Semaphore(CONCURRENCY)

    async def order_job(order_id, status):
        async with limit:
            try:
                await (allocate_order(order_id) if status == "reserved" else refresh_order(order_id))
            except Exception as exc:
                log.warning("Order check failed: id=%s type=%s", order_id, type(exc).__name__)

    async def payment_job(payment_id):
        async with limit:
            try:
                await check_payment(payment_id)
            except Exception as exc:
                log.warning("Payment check failed: id=%s type=%s", payment_id, type(exc).__name__)

    await asyncio.gather(
        *(payment_job(payment_id) for payment_id in payments),
        *(order_job(order_id, status) for order_id, status in orders),
    )
    await send_notices(bot)


async def send_notices(bot):
    if bot:
        with Session() as db:
            notices = db.scalars(
                select(Notice).where(Notice.sent.is_(False)).order_by(Notice.id).limit(100)
            ).all()
        for notice in notices:
            try:
                await deliver(bot, notice)
            except (TelegramForbiddenError, TelegramBadRequest) as exc:
                if not undeliverable(exc):
                    # Keep the notice (it may be an SMS code) and retry; log why it failed.
                    log.warning("Notification failed: id=%s error=%s", notice.id, str(exc)[:200])
                    continue
                # Blocked bot or unknown chat will never succeed: stop retrying every second.
                log.warning("Notification dropped: id=%s type=%s", notice.id, type(exc).__name__)
                with Session.begin() as db:
                    db.get(Notice, notice.id).sent = True
            except Exception as exc:
                log.warning("Notification failed: id=%s type=%s", notice.id, type(exc).__name__)
            else:
                with Session.begin() as db:
                    db.get(Notice, notice.id).sent = True


RECIPIENT_GONE = (
    "chat not found",
    "user not found",
    "bot was blocked",
    "user is deactivated",
    "bot can't initiate",
)


def undeliverable(exc):
    """Only errors about the recipient are permanent; anything else may succeed on retry."""
    return isinstance(exc, TelegramForbiddenError) or any(text in str(exc).lower() for text in RECIPIENT_GONE)


def notice_buttons(event, text):
    kind, _, rest = event.partition(":")
    order_id = rest.split(":")[0]
    if kind == "sms":
        code = re.search(r"<code>([^<]+)</code>", text)
        rows = [[("Скопировать код", unescape(code[1]), "copy")]] if code else []
        return rows + [[("Открыть номер", "card:" + order_id)]]
    if kind in ("allocated", "review-attached", "review-renewed", "sms-count"):
        return [[("Открыть номер", "card:" + order_id)]]
    if kind == "review-admin":
        return [[("Открыть заказ", "adm:o:" + order_id, "cancel")]]
    if kind in ("payment", "admin"):
        return [[("Купить номер", "catalog:0", "catalog")]]
    if kind == "refund":
        return [[("Мои номера", "orders", "orders")]]
    return []


async def deliver(bot, notice):
    rows = notice_buttons(notice.event, notice.text)
    markup = keyboard(rows) if rows else None
    try:
        await bot.send_message(notice.user_id, premium(notice.text), parse_mode="HTML", reply_markup=markup)
    except TelegramBadRequest as error:
        if "parse" not in str(error).lower() and "entit" not in str(error).lower():
            raise
        # Old queued notices may predate HTML formatting.
        await bot.send_message(
            notice.user_id,
            unescape(re.sub(r"</?[a-zA-Z][\w-]*(?:\s[^>]*)?>", "", notice.text)),
            reply_markup=markup,
        )


async def guarded(name, step, pause):
    """A transient failure (supplier, Telegram, a locked database) must not stop the loop:
    the supervisor would otherwise restart the whole container."""
    while True:
        try:
            await step()
        except Exception:
            log.exception("Worker loop %s failed; retrying", name)
        await asyncio.sleep(pause)


async def run():
    settings.validate_runtime()
    from aiogram import Bot

    bot = Bot(settings.bot_token) if settings.bot_token else None
    if bot:
        bot.session.middleware(PremiumFallback())
        await load_icons(bot)

    cleaned = {"at": 0}

    async def payment_events():
        await webhooks.process_payments()
        if now() - cleaned["at"] >= 86400:
            webhooks.cleanup()
            cleaned["at"] = now()

    async def periodic():
        await guarded("orders", cycle, settings.poll_seconds)

    async def webhook_events():
        await guarded("payments", payment_events, 1)

    async def notifications():
        await guarded("notices", lambda: send_notices(bot), 1)

    tasks = [asyncio.create_task(job()) for job in (periodic, webhook_events, notifications)]
    try:
        await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if bot:
            await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
