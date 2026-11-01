import asyncio
import json
import logging
import secrets
import time
from collections import defaultdict
from datetime import datetime, timezone
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import CallbackQuery, LinkPreviewOptions, Message
from sqlalchemy import or_, select

from app import webhooks
from app.bot_ui import (
    PROVIDER_LABELS,
    STATUS_ICONS,
    STATUS_LABELS,
    PremiumFallback,
    copy_phone,
    format_date,
    format_phone,
    icon,
    keyboard,
    load_icons,
)
from app.catalog_policy import allowed_codes, permitted_code
from app.config import settings
from app.db import Session
from app.models import Order, Payment, Product, User, now
from app.money import display, sale_price, usd
from app.payments import available_methods, check_payment, create_payment
from app.services import allocate_order, ensure_user, mark_notice_sent, refresh_order, reserve_order
from app.supplier import RENTAL_LABEL, supplier

router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
last_action = defaultdict(float)
NO_PREVIEW = LinkPreviewOptions(is_disabled=True)
QUICK_AMOUNTS = (1, 3, 5, 10, 25, 50)
METHOD_LABELS = {
    "cryptopay": "Crypto Bot · USDT / TON",
    "xrocket": "xRocket · USDT",
    "ton": "TON · перевод на кошелёк",
    "demo": "Тестовое пополнение",
}
HOME = ("Меню", "home", "plain")


class Deposit(StatesGroup):
    amount = State()


async def show(message, text, rows, edit=False):
    """Navigation edits the same message, so the chat does not fill up with old screens."""
    markup = keyboard(rows)
    if edit and hasattr(message, "edit_text"):
        try:
            await message.edit_text(
                text, parse_mode="HTML", reply_markup=markup, link_preview_options=NO_PREVIEW
            )
            return
        except TelegramBadRequest as error:
            if "not modified" in str(error).lower():
                return
    await message.answer(text, parse_mode="HTML", reply_markup=markup, link_preview_options=NO_PREVIEW)


def balance_of(user_id):
    with Session() as db:
        user = db.get(User, user_id)
        return user.balance if user else 0


def storefront():
    with Session() as db:
        return db.scalars(
            select(Product)
            .where(
                Product.enabled.is_(True), Product.kind != "renew", Product.supplier_code.in_(allowed_codes())
            )
            .order_by(Product.cost, Product.id)
        ).all()


def can_sell():
    return settings.demo_mode or supplier.ready


def support_row():
    return (
        [("Поддержка", settings.support_url, "support")] if "your_support" not in settings.support_url else []
    )


async def send_menu(message, user_id, edit=False):
    balance = balance_of(user_id)
    products = storefront() if can_sell() else []
    cheapest = min((sale_price(p) for p in products), default=None)
    prefix = "🧪 <b>Тестовый режим</b> — номера и коды ненастоящие.\n\n" if settings.demo_mode else ""
    if not can_sell():
        status = "\n<i>Магазин готовится к запуску — покупки скоро откроются.</i>"
    elif not products:
        status = "\n<i>Номера временно закончились — загляните чуть позже.</i>"
    else:
        status = ""
    buy = "Купить номер" + (f" · ${display(cheapest)}" if cheapest is not None else "")
    await show(
        message,
        f"{prefix}{icon('brand')} <b>SMENA</b> · номера Telegram США\n\n"
        "Виртуальные номера <b>+1</b> для входа в Telegram.\n"
        f"• срок аренды — {escape(RENTAL_LABEL.lower())}\n"
        "• код входа приходит прямо в этот чат\n"
        "• оплата с баланса, пополнение криптовалютой\n\n"
        f"<blockquote>Баланс: <b>${display(balance)}</b></blockquote>{status}",
        [
            [(buy, "catalog:0", "catalog")],
            [("Мои номера", "orders", "orders"), ("Кошелёк", "wallet", "wallet")],
            [("Пополнения", "payments", "payments"), ("Как это работает", "help", "plain")],
            support_row(),
        ],
        edit,
    )


async def help_screen(message, edit=False):
    await show(
        message,
        f"{icon('brand')} <b>Как это работает</b>\n\n"
        "1. Пополните <b>Кошелёк</b> — Crypto Bot, xRocket или TON.\n"
        "2. Нажмите <b>Купить номер</b> и подтвердите покупку.\n"
        "3. В Telegram: Настройки → <b>Добавить аккаунт</b> → введите номер.\n"
        "4. Код входа придёт в этот чат автоматически, обычно за 1–2 минуты.\n\n"
        "Номер остаётся вашим весь срок аренды: повторные коды тоже придут сюда. "
        "Срок можно продлить в карточке номера.\n\n"
        "<i>Выданный номер нельзя отменить или вернуть.</i>",
        [[("Купить номер", "catalog:0", "catalog")], [HOME]],
        edit,
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user.id, message.from_user.username or "")
    await send_menu(message, message.from_user.id)


KIND_LABELS = {"new": "Новый номер", "restart": "Повторный номер", "renew": "Продление"}


def product_label(product):
    return f"{KIND_LABELS[product.kind]} · ${display(sale_price(product))}"


async def catalog(message, offset=0, country_id=None, edit=False):
    offset = max(0, offset)
    products = storefront() if can_sell() else []
    page = products[offset : offset + 10]
    rows = [[(product_label(product), f"product:{product.id}")] for product in page]
    navigation = []
    if offset:
        navigation.append(("←", f"catalog:{max(0, offset - 10)}", "plain"))
    if len(products) > offset + 10:
        navigation.append(("→", f"catalog:{offset + 10}", "plain"))
    rows += [navigation, [HOME]]
    if not can_sell():
        description = "Покупки пока недоступны — магазин готовится к запуску."
    elif page:
        description = "Выберите номер:"
    else:
        description = "Сейчас номеров нет в наличии. Загляните немного позже."
    await show(
        message,
        f"{icon('catalog')} <b>Номера Telegram · США</b>\n\n"
        f"Виртуальные номера +1, срок аренды — {escape(RENTAL_LABEL.lower())}.\n"
        f"Код входа придёт в этот чат.\n\n{description}",
        rows,
        edit,
    )


async def product_screen(message, user_id, product_id, edit=False):
    with Session() as db:
        product = db.get(Product, product_id)
        if not product or not product.enabled or not permitted_code(product.supplier_code):
            raise ValueError("Этот номер больше недоступен. Откройте каталог заново")
        price = sale_price(product)
        kind = product.kind
    balance = balance_of(user_id)
    rows = []
    if not can_sell():
        footer = "<i>Покупки временно недоступны.</i>"
    elif balance >= price:
        footer = "<i>Выданный номер нельзя отменить или вернуть.</i>"
        rows.append([(f"Купить за ${display(price)}", f"buy:{product_id}:{price}:{secrets.token_hex(12)}")])
    else:
        footer = f"Не хватает <b>${display(price - balance)}</b> — пополните кошелёк."
        rows.append([("Пополнить кошелёк", "wallet", "wallet")])
    rows.append([("← Каталог", "catalog:0", "plain"), HOME])
    await show(
        message,
        f"{icon('catalog')} <b>{KIND_LABELS[kind]} · США</b>\n\n"
        "Виртуальный номер +1 для Telegram\n"
        f"Срок аренды: {escape(RENTAL_LABEL.lower())}\n"
        "Код входа: приходит в этот чат\n\n"
        f"<blockquote>Цена: <b>${display(price)}</b>\nВаш баланс: ${display(balance)}</blockquote>\n"
        f"{footer}",
        rows,
        edit,
    )


def order_screen(order, archived, checked=False):
    payload = json.loads(order.supplier_payload or "{}")
    status = STATUS_LABELS.get(order.status, order.status)
    until = format_date(payload.get("end_time"))
    title = "Архивный заказ" if archived else "Номер Telegram · США"
    lines = [f"{STATUS_ICONS.get(order.status, '•')} <b>{title}</b>", ""]
    if order.phone:
        lines.append(f"<code>{escape(format_phone(order.phone))}</code>")
    lines.append(
        f"Статус: {status}"
        + (f" · до {escape(until)}" if until and order.status in ("waiting", "active") else "")
    )
    live = order.status in ("waiting", "active") and not archived
    if order.sms:
        lines += ["", f"<blockquote>Последний код: <code>{escape(order.sms)}</code></blockquote>"]
    if live and not order.sms:
        lines += [
            "",
            "<b>Как войти</b>",
            "1. Telegram → Настройки → «Добавить аккаунт»",
            "2. Введите номер и запросите код",
            "3. Код придёт в этот чат сам, обычно за 1–2 минуты",
            "",
            "<i>Если Telegram предлагает другой способ — выберите SMS.</i>",
        ]
    elif live:
        lines += ["", "Новые коды тоже придут сюда автоматически."]
    if order.status == "refunded":
        lines += ["", f"${display(order.price)} возвращены на баланс."]
    elif order.status == "review":
        lines += ["", "Проверяем заказ у поставщика. Деньги не потеряются — мы сообщим результат в этот чат."]
    elif order.status in ("reserved", "allocating"):
        lines += ["", "Получаем номер, это занимает несколько секунд."]
    elif order.status == "expired":
        lines += ["", "Срок аренды закончился."]
    lines += ["", f"<i>Заказ {order.id[:8]} · ${display(order.price)}</i>"]
    if checked:
        lines.append(f"<i>Проверено в {datetime.now(timezone.utc):%H:%M:%S} UTC</i>")
    rows = []
    if order.phone and live:
        copies = [("Номер", copy_phone(order.phone), "copy")]
        if order.sms:
            copies.append(("Код", order.sms, "copy"))
        rows.append(copies)
        rows.append([("Проверить SMS", "sms:" + order.id)])
    if order.status in ("reserved", "allocating"):
        rows.append([("Обновить", "order:" + order.id, "refresh")])
    if order.status == "waiting" and getattr(supplier, "can_cancel", False):
        rows.append([("Отменить заказ", "cancelask:" + order.id)])
    if order.status in ("active", "expired") and not order.parent_id and not archived:
        rows.append([("Продлить номер", "renew:" + order.id)])
    rows.append([("← Мои номера", "orders", "plain"), HOME])
    return "\n".join(lines), rows


async def order_card(message, user_id, order_id, edit=False, checked=False):
    with Session() as db:
        order = db.get(Order, order_id)
        if not order or order.user_id != user_id:
            raise ValueError("Заказ не найден")
        product = db.get(Product, order.product_id)
        archived = not (product and permitted_code(product.supplier_code))
    await show(message, *order_screen(order, archived, checked), edit)


async def orders(message, user_id, offset=0, edit=False):
    offset = max(0, offset)
    with Session() as db:
        rows = db.scalars(
            select(Order)
            .where(Order.user_id == user_id, or_(Order.parent_id.is_(None), Order.status != "completed"))
            .order_by(Order.created_at.desc(), Order.id.desc())
            .offset(offset)
            .limit(11)
        ).all()
    if not rows and not offset:
        await show(
            message,
            f"{icon('orders')} <b>Мои номера</b>\n\nЗдесь появятся купленные номера.",
            [[("Купить номер", "catalog:0", "catalog")], [HOME]],
            edit,
        )
        return
    buttons = [
        [
            (
                f"{STATUS_ICONS.get(o.status, '•')} {'Продление ' if o.parent_id else ''}"
                f"{format_phone(o.phone) if o.phone else 'Заказ ' + o.id[:8]}"
                f" · {STATUS_LABELS.get(o.status, o.status).lower()}",
                "order:" + o.id,
                "plain",
            )
        ]
        for o in rows[:10]
    ]
    navigation = []
    if offset:
        navigation.append(("←", f"orders:{max(0, offset - 10)}", "plain"))
    if len(rows) > 10:
        navigation.append(("→", f"orders:{offset + 10}", "plain"))
    await show(
        message,
        f"{icon('orders')} <b>Мои номера</b>\n\nОткройте номер, чтобы скопировать его, проверить SMS или продлить.",
        buttons + [navigation, [HOME]],
        edit,
    )


def paid_time(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime("%d.%m %H:%M")


async def payments(message, user_id, offset=0, edit=False):
    offset = max(0, offset)
    with Session() as db:
        rows = db.scalars(
            select(Payment)
            .where(Payment.user_id == user_id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .offset(offset)
            .limit(11)
        ).all()
    if not rows and not offset:
        await show(
            message,
            f"{icon('payments')} <b>Пополнения</b>\n\nПополнений пока нет.",
            [[("Пополнить кошелёк", "wallet", "wallet")], [HOME]],
            edit,
        )
        return
    lines = [
        f"{STATUS_ICONS.get(p.status, '•')} <b>${display(p.amount)}</b> · "
        f"{PROVIDER_LABELS.get(p.provider, p.provider)} · {paid_time(p.created_at)} · "
        f"{STATUS_LABELS.get(p.status, p.status).lower()}"
        for p in rows[:10]
    ]
    buttons = [
        [(f"Счёт ${display(p.amount)} · {PROVIDER_LABELS.get(p.provider, p.provider)}", "pay:" + p.id)]
        for p in rows[:10]
        if p.status in ("pending", "expired")
    ]
    navigation = []
    if offset:
        navigation.append(("←", f"payments:{max(0, offset - 10)}", "plain"))
    if len(rows) > 10:
        navigation.append(("→", f"payments:{offset + 10}", "plain"))
    await show(
        message,
        f"{icon('payments')} <b>Пополнения</b>\n\n" + "\n".join(lines) + "\n\n<i>Время UTC</i>",
        buttons + [navigation, [("Кошелёк", "wallet", "wallet"), HOME]],
        edit,
    )


async def payment_message(message, payment, edit=False):
    rows = []
    payable = payment.status == "pending" and payment.expires_at > now()
    provider = PROVIDER_LABELS.get(payment.provider, payment.provider)
    lines = [
        f"{icon('payments')} <b>Счёт {provider}</b>",
        "",
        f"<blockquote>На баланс: <b>${display(payment.amount)}</b>\n"
        f"Статус: {STATUS_LABELS.get(payment.status, payment.status)}</blockquote>",
    ]
    if payable and payment.url.startswith("https://"):
        rows.append([("Оплатить", payment.url, "confirm")])
    if payment.provider == "ton" and payable:
        lines += [
            "",
            f"Отправьте ровно <code>{escape(payment.crypto_amount)}</code> TON "
            f"({'TESTNET' if settings.ton_testnet else 'MAINNET'})",
            f"Адрес: <code>{escape(settings.ton_wallet)}</code>",
            f"Комментарий (обязательно): <code>{escape(payment.reference)}</code>",
            "",
            f'<a href="{escape(payment.url)}">Открыть в TON-кошельке</a>',
            "<i>Курс зафиксирован на время счёта. Комиссия сети оплачивается отдельно.</i>",
        ]
        rows.append(
            [
                ("Сумма", payment.crypto_amount, "copy"),
                ("Адрес", settings.ton_wallet, "copy"),
                ("Комментарий", payment.reference, "copy"),
            ]
        )
    elif payment.provider == "ton" and payment.status != "paid":
        lines += [
            "",
            "Этот счёт сейчас недоступен для оплаты. Если перевод уже отправлен — "
            "проверьте статус или напишите в поддержку.",
        ]
    elif payment.provider == "demo":
        lines += ["", "Демо: подтвердите этот счёт в админке → Пополнения."]
    if payment.status == "pending":
        lines.append("")
        lines.append(f"Оплатить до {datetime.fromtimestamp(payment.expires_at, timezone.utc):%H:%M} UTC")
    if payment.status in ("pending", "expired"):
        rows.append([("Проверить оплату", "checkpay:" + payment.id)])
    rows.append([("← Пополнения", "payments", "plain"), HOME])
    await show(message, "\n".join(lines), rows, edit)


async def wallet(message, user_id, edit=False):
    methods = available_methods()
    rows = [[(METHOD_LABELS[name], "deposit:" + name)] for name in methods]
    await show(
        message,
        f"{icon('wallet')} <b>Кошелёк</b>\n\n"
        f"<blockquote>Баланс: <b>${display(balance_of(user_id))}</b></blockquote>\n"
        + ("Выберите способ пополнения:" if rows else "Пополнение временно недоступно."),
        rows + [[("Пополнения", "payments", "payments"), HOME]],
        edit,
    )


@router.message(Command("orders"))
async def orders_command(message: Message, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user.id, message.from_user.username or "")
    await orders(message, message.from_user.id)


@router.message(Command("search", "catalog"))
async def search_command(message: Message, state: FSMContext | None = None):
    # Keep old /search links working, but this is now a single-service store.
    if state:
        await state.clear()
    await catalog(message)


@router.message(Command("payments"))
async def payments_command(message: Message, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user.id, message.from_user.username or "")
    await payments(message, message.from_user.id)


@router.message(Command("wallet"))
async def wallet_command(message: Message, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user.id, message.from_user.username or "")
    await wallet(message, message.from_user.id)


@router.message(Command("help"))
async def help_command(message: Message, state: FSMContext):
    await state.clear()
    await help_screen(message)


@router.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user.id, message.from_user.username or "")
    await send_menu(message, message.from_user.id)


async def deposit(message, state, user_id, provider, amount):
    payment = await create_payment(user_id, provider, amount)
    await state.clear()
    return payment


@router.callback_query()
async def callback(query: CallbackQuery, state: FSMContext):
    await query.answer()
    user_id, data = query.from_user.id, query.data or ""
    if time.monotonic() - last_action[user_id] < 0.7:
        return
    last_action[user_id] = time.monotonic()
    ensure_user(user_id, query.from_user.username or "")
    # Navigation cancels amount entry without discarding renewal confirmation data.
    await state.set_state(None)
    message = query.message
    action, _, argument = data.partition(":")
    try:
        if action == "home":
            await state.clear()
            await send_menu(message, user_id, edit=True)
        elif action == "help":
            await help_screen(message, edit=True)
        elif action == "catalog":
            await catalog(message, int(argument.split(":")[0] or 0), edit=True)
        elif action == "product":
            await product_screen(message, user_id, int(argument), edit=True)
        elif action == "buy":
            product_id, price, token = argument.split(":")
            order = reserve_order(user_id, int(product_id), f"{user_id}:{token}", int(price))
            await show(message, "⏳ <b>Получаем номер…</b>\n\nЭто занимает несколько секунд.", [], edit=True)
            await allocate_order(order.id)
            mark_notice_sent("allocated:" + order.id)
            await order_card(message, user_id, order.id, edit=True)
        elif action == "renew":
            with Session() as db:
                parent = db.get(Order, argument)
                if not parent or parent.user_id != user_id or parent.status not in ("active", "expired"):
                    raise ValueError("Номер недоступен для продления")
                payload = json.loads(parent.supplier_payload)
                code = f"{payload.get('cate_id')}:{payload.get('app_id')}:3"
                product = db.scalar(
                    select(Product).where(Product.supplier_code == code, Product.enabled.is_(True))
                )
                if not product or not permitted_code(code):
                    raise ValueError("Продление для этого номера пока недоступно. Напишите в поддержку")
                price, product_id, phone = sale_price(product), product.id, parent.phone
            balance = balance_of(user_id)
            token = secrets.token_hex(12)
            await state.update_data(
                **{"renew_" + token: {"product_id": product_id, "parent_id": argument, "price": price}}
            )
            if balance >= price:
                rows = [[(f"Продлить за ${display(price)}", "renewbuy:" + token)]]
                footer = "<i>Оплата спишется с баланса сразу.</i>"
            else:
                rows = [[("Пополнить кошелёк", "wallet", "wallet")]]
                footer = f"Не хватает <b>${display(price - balance)}</b> — пополните кошелёк."
            await show(
                message,
                f"{icon('orders')} <b>Продление номера</b>\n\n"
                f"<code>{escape(format_phone(phone))}</code>\nСрок: +1 месяц\n\n"
                f"<blockquote>Цена: <b>${display(price)}</b>\nВаш баланс: ${display(balance)}</blockquote>\n{footer}",
                rows + [[("← Назад", "order:" + argument, "plain")]],
                edit=True,
            )
        elif action == "renewbuy":
            info = (await state.get_data()).get("renew_" + argument)
            if not info:
                raise ValueError("Подтверждение устарело. Откройте продление заново")
            order = reserve_order(
                user_id, info["product_id"], f"{user_id}:renew:{argument}", info["price"], info["parent_id"]
            )
            await show(message, "⏳ <b>Продлеваем номер…</b>", [], edit=True)
            await allocate_order(order.id)
            with Session() as db:
                renewed = db.get(Order, order.id)
            if renewed.status == "completed":
                await order_card(message, user_id, info["parent_id"], edit=True)
            else:
                await order_card(message, user_id, order.id, edit=True)
        elif action == "orders":
            await orders(message, user_id, int(argument or 0), edit=True)
        elif action == "order":
            await order_card(message, user_id, argument, edit=True)
        elif action == "card":
            # Buttons under notifications open a fresh card and keep the code message intact.
            await order_card(message, user_id, argument)
        elif action == "sms":
            await refresh_order(argument, user_id)
            await order_card(message, user_id, argument, edit=True, checked=True)
        elif action == "cancelask":
            await show(
                message,
                "Отменить заказ? Возврат зависит от подтверждения поставщика.",
                [[("Да, отменить", "cancel:" + argument)], [("← Назад", "order:" + argument, "plain")]],
                edit=True,
            )
        elif action == "cancel":
            await refresh_order(argument, user_id, cancel=True)
            await order_card(message, user_id, argument, edit=True)
        elif action == "wallet":
            await wallet(message, user_id, edit=True)
        elif action == "deposit":
            if argument not in available_methods():
                raise ValueError("Способ оплаты не подключён")
            await state.set_state(Deposit.amount)
            await state.update_data(provider=argument)
            await show(
                message,
                f"{icon('wallet')} <b>Пополнение · {PROVIDER_LABELS.get(argument, argument)}</b>\n\n"
                "Выберите сумму или отправьте свою сообщением — от $1 до $10000, например <code>7.50</code>.",
                [
                    [(f"${value}", f"amount:{argument}:{value}", "plain") for value in QUICK_AMOUNTS[:3]],
                    [(f"${value}", f"amount:{argument}:{value}", "plain") for value in QUICK_AMOUNTS[3:]],
                    [("← Кошелёк", "wallet", "plain")],
                ],
                edit=True,
            )
        elif action == "amount":
            provider, value = argument.split(":")
            if int(value) not in QUICK_AMOUNTS:
                raise ValueError("Выберите сумму заново")
            payment = await deposit(message, state, user_id, provider, usd(value))
            await payment_message(message, payment, edit=True)
        elif action == "payments":
            await payments(message, user_id, int(argument or 0), edit=True)
        elif action == "pay":
            await payment_message(message, payment_of(user_id, argument), edit=True)
        elif action == "checkpay":
            await check_payment(argument, user_id)
            await payment_message(message, payment_of(user_id, argument), edit=True)
        else:
            await send_menu(message, user_id, edit=True)
    except ValueError as exc:
        await error_screen(message, str(exc))
    except Exception as exc:
        logging.getLogger("smena.bot").warning("Action failed: action=%s type=%s", action, type(exc).__name__)
        await error_screen(
            message,
            "Сервис временно недоступен. "
            + (
                "Проверьте «Мои номера» перед повторной покупкой."
                if action in ("buy", "renewbuy")
                else "Попробуйте ещё раз."
            ),
        )


def payment_of(user_id, payment_id):
    with Session() as db:
        payment = db.get(Payment, payment_id)
    if not payment or payment.user_id != user_id:
        raise ValueError("Счёт не найден")
    return payment


async def error_screen(message, text):
    rows = []
    if "Недостаточно средств" in text:
        rows.append([("Пополнить кошелёк", "wallet", "wallet")])
    if "активный счёт" in text:
        rows.append([("Открыть пополнения", "payments", "payments")])
    rows.append([HOME])
    # Errors are a new message: the screen the user acted on stays visible above.
    await show(message, "⚠️ " + escape(text), rows)


@router.message(Deposit.amount)
async def deposit_amount(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        payment = await deposit(
            message,
            state,
            message.from_user.id,
            data["provider"],
            usd((message.text or "").strip().replace(",", ".").lstrip("$")),
        )
        await payment_message(message, payment)
    except ValueError as exc:
        await message.answer(
            "⚠️ " + escape(str(exc)) + "\nОтправьте сумму ещё раз или /cancel.", parse_mode="HTML"
        )


@router.message()
async def anything_else(message: Message, state: FSMContext):
    # Free text outside a dialog used to be silently ignored.
    await state.clear()
    ensure_user(message.from_user.id, message.from_user.username or "")
    await send_menu(message, message.from_user.id)


async def process_telegram_event(bot, dispatcher):
    from aiogram.types import Update

    event = webhooks.claim(["telegram"])
    if event is None:
        return False
    try:
        update = Update.model_validate(json.loads(event.payload), context={"bot": bot})
        await dispatcher.feed_update(bot, update)
    except Exception as exc:
        logging.getLogger("smena.bot").warning(
            "Telegram event failed: id=%s type=%s", event.id, type(exc).__name__
        )
        webhooks.finish(event, failed=True)
    else:
        webhooks.finish(event)
    return True


async def configure_transport(bot, dispatcher):
    config = webhooks.configuration()
    if config["telegram_enabled"]:
        await bot.set_webhook(
            config["base_url"] + "/webhooks/telegram",
            secret_token=webhooks.telegram_secret(),
            allowed_updates=dispatcher.resolve_used_update_types(),
            max_connections=1,
            drop_pending_updates=False,
        )
        webhooks.set_telegram_status("webhook")
        return True
    await bot.delete_webhook(drop_pending_updates=False)
    webhooks.set_telegram_status("polling")
    return False


async def run():
    settings.validate_runtime()
    if not settings.bot_token:
        raise SystemExit("Set BOT_TOKEN in .env")
    bot, dispatcher = Bot(settings.bot_token), Dispatcher(events_isolation=SimpleEventIsolation())
    bot.session.middleware(PremiumFallback())
    await load_icons(bot)
    dispatcher.include_router(router)
    await bot.set_my_commands(
        [
            {"command": "start", "description": "Главное меню"},
            {"command": "catalog", "description": "Купить номер Telegram США"},
            {"command": "orders", "description": "Мои номера"},
            {"command": "wallet", "description": "Кошелёк и пополнение"},
            {"command": "payments", "description": "История пополнений"},
            {"command": "help", "description": "Как это работает"},
        ]
    )

    async def consume_webhooks():
        while True:
            if not await process_telegram_event(bot, dispatcher):
                await asyncio.sleep(0.25)

    consumer = None
    try:
        if await configure_transport(bot, dispatcher):
            await consume_webhooks()
        else:
            consumer = asyncio.create_task(consume_webhooks())
            await dispatcher.start_polling(bot, close_bot_session=False)
    finally:
        if consumer:
            consumer.cancel()
            await asyncio.gather(consumer, return_exceptions=True)
        await bot.session.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
