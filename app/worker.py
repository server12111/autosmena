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
    for order_id, status in orders:
        try:
            await (allocate_order(order_id) if status == "reserved" else refresh_order(order_id))
        except Exception as exc:
            log.warning("Order check failed: id=%s type=%s", order_id, type(exc).__name__)
    for payment_id in payments:
        try:
            await check_payment(payment_id)
        except Exception as exc:
            log.warning("Payment check failed: id=%s type=%s", payment_id, type(exc).__name__)
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
                # Blocked bot or unknown chat will never succeed: stop retrying every second.
                log.warning("Notification dropped: id=%s type=%s", notice.id, type(exc).__name__)
                with Session.begin() as db:
                    db.get(Notice, notice.id).sent = True
            except Exception as exc:
                log.warning("Notification failed: id=%s type=%s", notice.id, type(exc).__name__)
            else:
                with Session.begin() as db:
                    db.get(Notice, notice.id).sent = True


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
        await bot.send_message(notice.user_id, re.sub(r"</?\w+>", "", notice.text), reply_markup=markup)


async def run():
    settings.validate_runtime()
    from aiogram import Bot

    bot = Bot(settings.bot_token) if settings.bot_token else None
    if bot:
        bot.session.middleware(PremiumFallback())
        await load_icons(bot)

    async def periodic():
        while True:
            await cycle()
            await asyncio.sleep(settings.poll_seconds)

    async def webhook_events():
        cleaned = 0
        while True:
            await webhooks.process_payments()
            if now() - cleaned >= 86400:
                webhooks.cleanup()
                cleaned = now()
            await asyncio.sleep(1)

    async def notifications():
        while True:
            await send_notices(bot)
            await asyncio.sleep(1)

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
