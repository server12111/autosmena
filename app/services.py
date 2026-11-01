import json
from hashlib import sha256
from html import escape

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError

from app.bot_ui import format_phone
from app.catalog_policy import permitted_code
from app.db import Session
from app.models import Audit, Ledger, Notice, Order, Payment, Product, User, now
from app.money import display, sale_price
from app.supplier import Rejected, supplier


def ensure_user(user_id, username="", referrer_id=None):
    """Creates the user on first contact. A referrer is attached only to brand-new users."""
    try:
        with Session.begin() as db:
            user = db.get(User, user_id)
            if user is None:
                referrer = (
                    referrer_id
                    if referrer_id and referrer_id != user_id and db.get(User, referrer_id)
                    else None
                )
                db.add(User(id=user_id, username=username, referrer_id=referrer))
                return True
            user.username = username
    except IntegrityError:
        pass  # Simultaneous /start requests may race on the primary key.
    return False


def move_balance(db, user_id, delta, event, note="", require_unblocked=False):
    predicates = [User.id == user_id, User.balance + delta >= 0]
    if require_unblocked:
        predicates.append(User.blocked.is_(False))
    result = db.execute(update(User).where(*predicates).values(balance=User.balance + delta))
    if result.rowcount != 1:
        raise ValueError("Недостаточно средств или аккаунт недоступен")
    db.add(Ledger(user_id=user_id, delta=delta, event=event, note=note))


def adjust_balance(user_id, delta, request_id, note):
    if not note.strip() or not delta:
        raise ValueError("Нужны ненулевая сумма и причина корректировки")
    event = "admin:" + request_id
    try:
        with Session.begin() as db:
            previous = db.scalar(select(Ledger).where(Ledger.event == event))
            if previous:
                if previous.user_id != user_id or previous.delta != delta or previous.note != note:
                    raise ValueError("Этот ключ операции уже использован")
                return
            move_balance(db, user_id, delta, event, note)
            db.add(Audit(action="balance.adjust", detail=f"user={user_id}; delta={delta}; reason={note}"))
    except IntegrityError:
        with Session() as db:
            previous = db.scalar(select(Ledger).where(Ledger.event == event))
            if previous and previous.user_id == user_id and previous.delta == delta and previous.note == note:
                return
        raise ValueError("Этот ключ операции уже использован") from None


def reserve_order(user_id, product_id, request_key, expected_price, parent_id=None):
    try:
        with Session.begin() as db:
            existing = db.scalar(select(Order).where(Order.request_key == request_key))
            if existing:
                if (
                    existing.user_id != user_id
                    or existing.product_id != product_id
                    or existing.parent_id != parent_id
                ):
                    raise ValueError("Ключ заказа уже использован")
                return existing
            if not supplier.ready:
                raise ValueError("Покупки временно недоступны")
            product = db.get(Product, product_id)
            if not product or not product.enabled:
                raise ValueError("Товар недоступен")
            if not permitted_code(product.supplier_code):
                raise ValueError("Доступны только Telegram-номера США")
            if product.kind == "renew":
                parent = db.get(Order, parent_id) if parent_id else None
                if not parent or parent.user_id != user_id or parent.status not in ("active", "expired"):
                    raise ValueError("Выберите свой номер для продления")
            elif parent_id:
                raise ValueError("Этот товар не предназначен для продления")
            price = sale_price(product)
            if price != expected_price:
                raise ValueError("Цена изменилась. Откройте товар заново")
            if price <= 0:
                raise ValueError("Цена товара не настроена")
            order = Order(
                user_id=user_id,
                product_id=product_id,
                request_key=request_key,
                price=price,
                cost=product.cost,
                parent_id=parent_id,
            )
            db.add(order)
            db.flush()
            move_balance(db, user_id, -price, "order:" + order.id, require_unblocked=True)
            return order
    except IntegrityError:
        with Session() as db:
            order = db.scalar(select(Order).where(Order.request_key == request_key))
            if (
                order
                and order.user_id == user_id
                and order.product_id == product_id
                and order.parent_id == parent_id
            ):
                return order
        raise


def refund_order(db, order, reason):
    move_balance(db, order.user_id, order.price, "refund:" + order.id, reason)
    order.status = "refunded"
    order.updated_at = now()
    db.add(
        Notice(
            user_id=order.user_id,
            event="refund:" + order.id,
            text=f"💸 Заказ {order.id[:8]} отменён. <b>${display(order.price)}</b> возвращены на баланс.",
        )
    )


async def allocate_order(order_id):
    # Claim before network I/O; never automatically retry an ambiguous purchase.
    with Session.begin() as db:
        claimed = db.execute(
            update(Order)
            .where(Order.id == order_id, Order.status == "reserved")
            .values(status="allocating", updated_at=now())
        ).rowcount
        if not claimed:
            return
        order = db.get(Order, order_id)
        code = db.get(Product, order.product_id).supplier_code
        parent = db.get(Order, order.parent_id) if order.parent_id else None
    try:
        if hasattr(supplier, "check_cost"):
            try:
                await supplier.check_cost(code, order.cost)
            except Exception:
                # Only catalog reads have happened: refund before calling buy/renew.
                raise Rejected("Проверка наличия и цены не прошла") from None
        result = await supplier.renew(code, parent) if parent else await supplier.allocate(code, order_id)
    except Rejected:
        with Session.begin() as db:
            order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
            if order.status == "allocating":
                refund_order(db, order, "Поставщик подтвердил отказ")
        return
    except Exception:
        with Session.begin() as db:
            changed = db.execute(
                update(Order)
                .where(Order.id == order_id, Order.status == "allocating")
                .values(
                    status="review", error="Неопределённый результат покупки; нужна сверка", updated_at=now()
                )
            ).rowcount
            if changed:
                notify_admins_review(db, [order_id])
        return
    with Session.begin() as db:
        order = db.scalar(select(Order).where(Order.id == order_id).with_for_update())
        if order.status == "allocating":
            order.supplier_id, order.phone, order.status = result.external_id, result.phone, "waiting"
            order.supplier_payload = json.dumps(result.payload, ensure_ascii=False, default=str)
            if parent:
                order.status = "completed"
                db.get(Order, parent.id).status = "active"
            order.updated_at = now()
            db.add(
                Notice(
                    user_id=order.user_id,
                    event="allocated:" + order.id,
                    text=(
                        f"✅ <b>Номер {'продлён' if parent else 'готов'}</b>\n\n"
                        f"<code>{escape(format_phone(order.phone))}</code>\n"
                        + ("" if parent else "Запросите код в Telegram — он придёт в этот чат.")
                    ),
                )
            )


async def refresh_order(order_id, user_id=None, cancel=False):
    with Session() as db:
        order = db.get(Order, order_id)
        if not order or (user_id is not None and order.user_id != user_id):
            raise ValueError("Заказ не найден")
        if order.status not in ("waiting", "active"):
            return
    state = await (supplier.cancel(order) if cancel else supplier.status(order))
    with Session.begin() as db:
        # CAS also protects SQLite, where FOR UPDATE is a no-op.
        target = (
            state.status
            if state.status in ("completed", "active", "expired")
            else "refunding"
            if state.status == "cancelled"
            else None
        )
        if not target:
            return
        claimed = db.execute(
            update(Order)
            .where(
                Order.id == order_id,
                Order.status == order.status,
                Order.supplier_payload == order.supplier_payload,
            )
            .values(status=target, updated_at=now())
        ).rowcount
        if not claimed:
            return
        order = db.get(Order, order_id)
        if target == "completed":
            order.sms = state.sms
            db.add(
                Notice(
                    user_id=order.user_id,
                    event="sms:" + order.id,
                    text=sms_text(order.phone, state.sms),
                )
            )
        elif target == "refunding":
            refund_order(db, order, "Поставщик подтвердил отмену и возврат")
        else:
            previous = json.loads(order.supplier_payload)
            seen_count = int(previous.get("_seen_sms_count", previous.get("sms_count", 0)))
            count = int(state.payload.get("sms_count", 0))
            state.payload["_seen_sms_count"] = max(seen_count, count)
            order.supplier_payload = json.dumps(state.payload, ensure_ascii=False, default=str)
            if state.sms:
                signature = sha256(
                    json.dumps([state.payload.get("_sms_code_time", ""), state.sms]).encode()
                ).hexdigest()[:32]
                event = f"sms:{order.id}:{signature}"
                if not db.scalar(select(Notice.id).where(Notice.event == event)):
                    order.sms = state.sms
                    db.add(
                        Notice(
                            user_id=order.user_id,
                            event=event,
                            text=sms_text(order.phone, state.sms),
                        )
                    )
            elif count > seen_count:
                db.add(
                    Notice(
                        user_id=order.user_id,
                        event=f"sms-count:{order.id}:{count}",
                        text=f"💬 Новое SMS на <code>{escape(format_phone(order.phone))}</code>. Код появится здесь после обработки.",
                    )
                )


def sms_text(phone, code):
    return (
        f"💬 <b>Код входа в Telegram</b>\n\n<code>{escape(code)}</code>\n\n"
        f"Номер: <code>{escape(format_phone(phone))}</code>\n<i>Нажмите на код, чтобы скопировать.</i>"
    )


def mark_notice_sent(event):
    """The bot already showed this result on screen; skip the duplicate notification."""
    with Session.begin() as db:
        db.execute(update(Notice).where(Notice.event == event).values(sent=True))


def referral_bonus(db, payment):
    """The inviter earns a share of every top-up of an invited user, once per payment."""
    from app.config import settings

    user = db.get(User, payment.user_id)
    bonus = payment.amount * settings.referral_percent // 100 // 10000 * 10000
    if not user.referrer_id or bonus <= 0 or not db.get(User, user.referrer_id):
        return
    move_balance(db, user.referrer_id, bonus, "ref:" + payment.id, f"referral user={user.id}")
    db.add(
        Notice(
            user_id=user.referrer_id,
            event="ref:" + payment.id,
            text=f"💸 <b>Реферальный бонус +${display(bonus)}</b>\n\n"
            f"Ваш реферал пополнил баланс на ${display(payment.amount)}.",
        )
    )


def referral_stats(user_id):
    from sqlalchemy import func

    with Session() as db:
        invited = db.scalar(select(func.count()).select_from(User).where(User.referrer_id == user_id))
        earned = db.scalar(
            select(func.coalesce(func.sum(Ledger.delta), 0)).where(
                Ledger.user_id == user_id, Ledger.event.startswith("ref:")
            )
        )
    return invited, earned


def credit_payment(payment_id, transaction_hash=None):
    """Call only after server-side verification of provider, amount, currency and reference."""
    with Session.begin() as db:
        changed = db.execute(
            update(Payment)
            .where(Payment.id == payment_id, Payment.status.in_(["pending", "expired"]))
            .values(status="paid", paid_at=now(), transaction_hash=transaction_hash)
        ).rowcount
        if not changed:
            return False
        payment = db.get(Payment, payment_id)
        move_balance(db, payment.user_id, payment.amount, "payment:" + payment.id)
        referral_bonus(db, payment)
        db.add(
            Notice(
                user_id=payment.user_id,
                event="payment:" + payment.id,
                text=f"✅ <b>Баланс пополнен на ${display(payment.amount)}</b>\n\nТеперь можно купить номер.",
            )
        )
        return True


def notify_admins_review(db, order_ids):
    """Tell every admin who has opened the bot that an order needs reconciliation."""
    from app.config import settings

    admins = [admin_id for admin_id in settings.admin_id_set if db.get(User, admin_id)]
    for order_id in order_ids:
        order = db.get(Order, order_id)
        for admin_id in admins:
            db.add(
                Notice(
                    user_id=admin_id,
                    event=f"review-admin:{order_id}:{admin_id}",
                    text=(
                        f"❗️ <b>Заказ на проверке</b>\n\n"
                        f"🔴 Заказ: <code>{order.id[:8]}</code> · ${display(order.price)}\n"
                        f"🔴 Клиент: <code>{order.user_id}</code>\n"
                        f"🔴 Причина: {escape(order.error or 'неопределённый результат покупки')}\n\n"
                        "💡 Сверьте с кабинетом api.cc: если номер не выдан — верните деньги."
                    ),
                )
            )


def recover_stale_orders():
    with Session.begin() as db:
        # A crashed allocation may already have charged the supplier: manual reconciliation only.
        stale = db.scalars(
            select(Order.id).where(Order.status == "allocating", Order.updated_at < now() - 120)
        ).all()
        if not stale:
            return
        changed = db.execute(
            update(Order)
            .where(Order.id.in_(stale), Order.status == "allocating")
            .values(status="review", error="Процесс прерван во время покупки; нужна сверка")
        ).rowcount
        if changed:
            db.flush()
            notify_admins_review(
                db, db.scalars(select(Order.id).where(Order.id.in_(stale), Order.status == "review")).all()
            )
