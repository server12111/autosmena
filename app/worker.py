import asyncio
import logging

from sqlalchemy import select

from app import webhooks
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
                await bot.send_message(notice.user_id, notice.text)
            except Exception as exc:
                log.warning("Notification failed: id=%s type=%s", notice.id, type(exc).__name__)
            else:
                with Session.begin() as db:
                    db.get(Notice, notice.id).sent = True


async def run():
    settings.validate_runtime()
    from aiogram import Bot

    bot = Bot(settings.bot_token) if settings.bot_token else None

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
