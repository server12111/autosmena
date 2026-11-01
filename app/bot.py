import asyncio
import json
import logging
import secrets
import time
from collections import defaultdict
from datetime import datetime, timezone
from functools import lru_cache
from hashlib import sha256
from html import escape
from pathlib import Path
from urllib.parse import urlencode

from aiogram import BaseMiddleware, Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import SimpleEventIsolation
from aiogram.types import BotCommand, BotCommandScopeChat, CallbackQuery, Message
from sqlalchemy import func, or_, select

from app import bot_admin, subscription, webhooks
from app.bot_admin import is_admin
from app.bot_ui import (
    PROVIDER_LABELS,
    STATUS_LABELS,
    STATUS_ROLES,
    PremiumFallback,
    copy_phone,
    format_date,
    format_phone,
    icon,
    load_icons,
    show,
    status_icon,
    user_error,
)
from app.catalog_policy import allowed_codes, permitted_code
from app.config import settings
from app.db import Session
from app.models import Order, Payment, Product, User, now
from app.money import display, sale_price, usd
from app.payments import available_methods, check_payment, create_payment
from app.reseller import issue_key, key_info, public_url
from app.services import (
    allocate_order,
    ensure_user,
    mark_notice_sent,
    referral_stats,
    refresh_order,
    reserve_order,
)
from app.supplier import RENTAL_LABEL, supplier

router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
last_action = defaultdict(float)
QUICK_AMOUNTS = (1, 3, 5, 10, 25, 50)
METHOD_LABELS = {
    "cryptopay": "Crypto Bot · USDT / TON",
    "xrocket": "xRocket · USDT",
    "ton": "TON · перевод на кошелёк",
    "demo": "Тестовое пополнение",
}
HOME = ("Меню", "home")
DOT = "🔴"
BOT_USERNAME = "SmenaMarketBot"


async def gate_screen(message, absent, edit=False):
    rows = [[(item["title"][:40], item["url"], "send")] for item in absent]
    await show(
        message,
        "📣 <b>Подпишитесь на канал</b>\n\n"
        f"{DOT} Чтобы пользоваться ботом, подпишитесь на {'канал' if len(absent) == 1 else 'каналы'} ниже\n"
        f"{DOT} Затем нажмите «Я подписался»",
        rows + [[("Я подписался", "subcheck", "confirm")]],
        edit,
    )


class SubscriptionGate(BaseMiddleware):
    """Blocks the shop until the user joins every required channel (admins are exempt)."""

    async def __call__(self, handler, event, data):
        user = event.from_user
        chat = getattr(event, "chat", None) or getattr(getattr(event, "message", None), "chat", None)
        # Outer middleware runs before the router's private-chat filter: never answer in groups.
        private = chat is None or chat.type == ChatType.PRIVATE
        if user is None or not private or is_admin(user.id) or not subscription.channels():
            return await handler(event, data)
        bot_instance = data["bot"]
        if isinstance(event, CallbackQuery) and event.data == "subcheck":
            absent = await subscription.missing(bot_instance, user.id, fresh=True)
            if absent:
                await event.answer("Подписка не найдена. Подпишитесь и нажмите ещё раз", show_alert=True)
                return None
            await event.answer("Спасибо за подписку!")
            ensure_user(user.id, user.username or "")
            await send_menu(event.message, user.id, edit=True)
            return None
        absent = await subscription.missing(bot_instance, user.id)
        if not absent:
            return await handler(event, data)
        if isinstance(event, Message):
            # Keep the referral from the invite link even though the shop is closed for now.
            args = (event.text or "").split(maxsplit=1)
            payload = args[1] if len(args) > 1 and args[0].startswith("/start") else ""
            referrer = int(payload[4:]) if payload.startswith("ref_") and payload[4:].isdigit() else None
            ensure_user(user.id, user.username or "", referrer)
            await gate_screen(event, absent)
        else:
            await event.answer()
            await gate_screen(event.message, absent, edit=True)
        return None


router.message.outer_middleware(SubscriptionGate())
router.callback_query.outer_middleware(SubscriptionGate())


class Deposit(StatesGroup):
    amount = State()


BRAND = Path(__file__).parent / "static" / "brand"


@lru_cache(maxsize=None)
def banner(name):
    """Public URL of a section banner; the content hash makes Telegram refetch a changed image."""
    path = BRAND / f"{name}.png"
    if not path.exists():
        return None
    version = sha256(path.read_bytes()).hexdigest()[:10]
    base = (settings.banner_base_url or public_url() + "/static/brand").rstrip("/")
    return f"{base}/{name}.png?v={version}"


def personal_banner(kind, user_id):
    """Profile/wallet card with live data, served by this app; plain artwork when images come
    from an external static host (BANNER_BASE_URL) that cannot render per-user cards."""
    from app import banners

    data = banners.user_data(user_id)
    if settings.banner_base_url or data is None:
        return banner(kind)
    return f"{public_url()}/banner/{kind}/{user_id}/{banners.signature(user_id)}.png?v={banners.data_version(data)}"


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
        status = "\n⚡️ <i>Магазин готовится к запуску — покупки скоро откроются.</i>"
    elif not products:
        status = "\n⚡️ <i>Номера временно закончились — загляните чуть позже.</i>"
    else:
        status = ""
    buy = "Купить номер" + (f" · ${display(cheapest)}" if cheapest is not None else "")
    await show(
        message,
        f"{prefix}✈️ <b>SMENA</b> — номера Telegram США\n\n"
        "Американские номера <b>+1</b>: купил, вошёл — код уже в чате 🔥\n\n"
        f"{DOT} Аренда <b>{escape(RENTAL_LABEL.lower())}</b>\n"
        f"{DOT} Код входа приходит <b>прямо сюда</b>\n"
        f"{DOT} Повторные коды — весь срок аренды\n"
        f"{DOT} Пополнение криптой: USDT и TON\n\n"
        f"<blockquote>💰 Баланс: <b>${display(balance)}</b></blockquote>{status}",
        [
            [(buy, "catalog:0", "catalog")],
            [("Профиль", "profile"), ("Кошелёк", "wallet")],
            support_row(),
            [("Админка", "adm:home", "admin")] if is_admin(user_id) else [],
        ],
        edit,
        banner=banner("main"),
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext, command: CommandObject | None = None):
    await state.clear()
    args = (command.args or "") if command else ""
    referrer = int(args[4:]) if args.startswith("ref_") and args[4:].isdigit() else None
    ensure_user(message.from_user.id, message.from_user.username or "", referrer)
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
        description = "⚡️ Покупки пока недоступны — магазин готовится к запуску."
    elif page:
        description = "⚡️ Выберите вариант:"
    else:
        description = "⚡️ Сейчас номеров нет в наличии. Загляните немного позже."
    await show(
        message,
        "🛒 <b>Купить номер · США</b>\n\n"
        f"{DOT} Виртуальный номер <b>+1</b> под Telegram\n"
        f"{DOT} Аренда {escape(RENTAL_LABEL.lower())}, продление в пару нажатий\n"
        f"{DOT} Номер выдаётся сразу, код придёт в этот чат\n\n{description}",
        rows,
        edit,
        banner=banner("buy"),
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
        footer = "⚡️ <i>Покупки временно недоступны.</i>"
    elif balance >= price:
        footer = "❗️ <i>Выданный номер нельзя отменить или вернуть.</i>"
        rows.append([(f"Купить за ${display(price)}", f"buy:{product_id}:{price}:{secrets.token_hex(12)}")])
    else:
        footer = f"💸 Не хватает <b>${display(price - balance)}</b> — пополните кошелёк."
        rows.append([("Пополнить кошелёк", "wallet", "money")])
    rows.append([("Каталог", "catalog:0", "catalog", ""), HOME])
    await show(
        message,
        f"📱 <b>{KIND_LABELS[kind]} · США</b>\n\n"
        f"{DOT} Номер <b>+1</b> под Telegram\n"
        f"{DOT} Аренда: {escape(RENTAL_LABEL.lower())}\n"
        f"{DOT} Код входа: приходит в этот чат\n\n"
        f"<blockquote>💎 Цена: <b>${display(price)}</b>\n💰 Баланс: ${display(balance)}</blockquote>\n"
        f"{footer}",
        rows,
        edit,
    )


def order_screen(order, archived, checked=False, again=False):
    payload = json.loads(order.supplier_payload or "{}")
    status = STATUS_LABELS.get(order.status, order.status)
    until = format_date(payload.get("end_time"))
    title = "Архивный заказ" if archived else "Номер Telegram · США"
    lines = [f"{status_icon(order.status)} <b>{title}</b>", ""]
    if order.phone:
        lines.append(f"📱 <code>{escape(format_phone(order.phone))}</code>")
    lines.append(f"{DOT} Статус: {status}")
    if until and order.status in ("waiting", "active"):
        lines.append(f"{DOT} Аренда до: {escape(until)}")
    live = order.status in ("waiting", "active") and not archived
    if order.sms:
        lines += ["", f"<blockquote>💬 Последний код: <code>{escape(order.sms)}</code></blockquote>"]
    if live and not order.sms:
        lines += ["", "⚡️ Код придёт в этот чат автоматически."]
    elif live and not again:
        lines += ["", "⚡️ Новые коды тоже придут сюда автоматически."]
    if order.status == "refunded":
        lines += ["", f"💸 ${display(order.price)} возвращены на баланс."]
    elif order.status == "review":
        lines += ["", "🔎 Проверяем заказ. Деньги не потеряются — результат придёт в этот чат."]
    elif order.status in ("reserved", "allocating"):
        lines += ["", "⚡️ Получаем номер, это занимает несколько секунд."]
    elif order.status == "expired":
        lines += ["", "📆 Срок аренды закончился."]
    if again and live:
        lines += [
            "",
            f"{icon('refresh')} <b>Ждём новый код.</b> Нажмите «Отправить код ещё раз» в Telegram — "
            "он придёт в этот чат автоматически.",
        ]
    lines += ["", f"<i>📝 Заказ {order.id[:8]} · ${display(order.price)}</i>"]
    if checked:
        lines.append(f"<i>Проверено в {datetime.now(timezone.utc):%H:%M:%S} UTC</i>")
    rows = []
    if order.phone and live:
        copies = [("Номер", copy_phone(order.phone), "copy")]
        if order.sms:
            copies.append(("Код", order.sms, "copy"))
        rows.append(copies)
        rows.append(
            [("Получить код ещё раз", "again:" + order.id)]
            if order.sms
            else [("Проверить SMS", "sms:" + order.id)]
        )
    if order.status in ("reserved", "allocating"):
        rows.append([("Обновить", "order:" + order.id, "refresh")])
    if order.status in ("active", "expired") and not order.parent_id and not archived:
        rows.append([("Продлить номер", "renew:" + order.id)])
    rows.append([("Мои номера", "orders"), ("Профиль", "profile", "user", "")])
    return "\n".join(lines), rows


async def order_card(message, user_id, order_id, edit=False, checked=False, again=False):
    with Session() as db:
        order = db.get(Order, order_id)
        if not order or order.user_id != user_id:
            raise ValueError("Заказ не найден")
        product = db.get(Product, order.product_id)
        archived = not (product and permitted_code(product.supplier_code))
    await show(message, *order_screen(order, archived, checked, again), edit)


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
            "📁 <b>Мои номера</b>\n\n⚡️ Здесь появятся купленные номера — первый можно взять прямо сейчас.",
            [[("Купить номер", "catalog:0")], [("Профиль", "profile", "user", ""), HOME]],
            edit,
            banner=banner("numbers"),
        )
        return
    buttons = [
        [
            (
                f"{'Продление ' if o.parent_id else ''}"
                f"{format_phone(o.phone) if o.phone else 'Заказ ' + o.id[:8]}"
                f" · {STATUS_LABELS.get(o.status, o.status).lower()}",
                "order:" + o.id,
                STATUS_ROLES.get(o.status, "phone"),
                "",
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
        "📁 <b>Мои номера</b>\n\n"
        f"{DOT} Нажмите на номер, чтобы открыть карточку\n"
        f"{DOT} Там: скопировать, получить код, продлить",
        buttons + [navigation, [("Профиль", "profile", "user", ""), HOME]],
        edit,
        banner=banner("numbers"),
    )


def bot_link(payload=""):
    return f"https://t.me/{BOT_USERNAME}" + (f"?start={payload}" if payload else "")


async def profile(message, user_from, edit=False):
    user_id = user_from.id
    with Session() as db:
        user = db.get(User, user_id)
        numbers = db.scalar(
            select(func.count())
            .select_from(Order)
            .where(
                Order.user_id == user_id, Order.parent_id.is_(None), Order.status.in_(["waiting", "active"])
            )
        )
        spent = db.scalar(
            select(func.coalesce(func.sum(Order.price), 0)).where(
                Order.user_id == user_id, Order.status.in_(["waiting", "active", "expired", "completed"])
            )
        )
    invited, earned = referral_stats(user_id)
    name = escape(f"@{user_from.username}" if user_from.username else f"@user{user_id}")
    await show(
        message,
        "🪪 <b>Профиль</b>\n\n"
        f"{DOT} Аккаунт: <b>{name}</b>\n"
        f"{DOT} ID: <code>{user_id}</code>\n"
        f"{DOT} Активных номеров: <b>{numbers}</b>\n"
        f"{DOT} Потрачено: <b>${display(spent)}</b>\n\n"
        f"<blockquote>💰 Баланс: <b>${display(user.balance if user else 0)}</b>\n"
        f"⭐️ Рефералы: <b>{invited}</b> · заработано <b>${display(earned)}</b></blockquote>",
        [
            [("Мои номера", "orders")],
            [("Рефералы", "ref", "star"), ("API для реселлеров", "api", "send")],
            [HOME],
        ],
        edit,
        banner=personal_banner("profile", user_id),
    )


async def referrals(message, user_id, edit=False):
    invited, earned = referral_stats(user_id)
    link = bot_link(f"ref_{user_id}")
    share = "https://t.me/share/url?" + urlencode(
        {"url": link, "text": "Номера Telegram США с кодом прямо в чат"}
    )
    percent = settings.referral_percent
    await show(
        message,
        "⭐️ <b>Реферальная программа</b>\n\n"
        f"Приглашайте друзей и получайте <b>{percent}%</b> с каждого их пополнения 🔥\n\n"
        f"{DOT} Бонус приходит на баланс сразу\n"
        f"{DOT} Без лимитов и навсегда\n"
        f"{DOT} Тратьте на номера, как обычные деньги\n\n"
        f"✍️ Ваша ссылка:\n<code>{escape(link)}</code>\n\n"
        f"<blockquote>🪪 Приглашено: <b>{invited}</b>\n💸 Заработано: <b>${display(earned)}</b></blockquote>",
        [
            [("Скопировать ссылку", link, "copy"), ("Поделиться", share, "send")],
            [("Профиль", "profile", "user", ""), HOME],
        ],
        edit,
        banner=banner("ref"),
    )


API_DOCS = """🔴 <b>Адрес:</b> <code>{url}</code>
🔴 <b>Ключ:</b> заголовок <code>Authorization: Bearer КЛЮЧ</code>
🔴 <b>Лимит:</b> 60 запросов в минуту, оплата с баланса бота

<b>Методы</b>
💰 <code>GET  /reseller/v1/balance</code> — баланс
🛒 <code>GET  /reseller/v1/products</code> — товары и цены
✅ <code>POST /reseller/v1/orders</code> — купить номер
<code>{{"product_id": 1, "request_id": "ваш-id"}}</code>
📁 <code>GET  /reseller/v1/orders</code> — история
📱 <code>GET  /reseller/v1/orders/ID</code> — статус, номер и код

💡 Повтор <code>request_id</code> не создаёт второй заказ. Код приходит в поле <code>code</code>."""


async def api_screen(message, user_id, edit=False, new_key=None):
    info = key_info(user_id)
    if new_key:
        text = (
            f"🔥 <b>Ваш API-ключ готов</b>\n\n<code>{escape(new_key)}</code>\n\n"
            f"{DOT} Сохраните его сейчас — больше он показан не будет\n"
            f"{DOT} Если ключ утёк, перевыпустите: старый сразу перестанет работать"
        )
        rows = [[("Скопировать ключ", new_key, "copy")], [("Документация", "apidocs", "payments")]]
    elif info:
        used = paid_time(info.last_used_at) + " UTC" if info.last_used_at else "ещё не использовался"
        text = (
            "📣 <b>API для реселлеров</b>\n\n"
            f"{DOT} Ключ: <code>{escape(info.prefix)}…</code>\n"
            f"{DOT} Создан: {paid_time(info.created_at)} UTC\n"
            f"{DOT} Последний запрос: {used}\n\n"
            "💡 Покупайте номера и получайте коды из своего сервиса или бота — по ценам магазина."
        )
        rows = [
            [("Документация", "apidocs", "payments")],
            [("Перевыпустить ключ", "apikeyask", "cancel", "")],
        ]
    else:
        text = (
            "📣 <b>API для реселлеров</b>\n\n"
            "Подключите магазин к своему сервису или боту 🤖\n\n"
            f"{DOT} Покупка номеров и коды по HTTP\n"
            f"{DOT} Оплата с баланса этого аккаунта\n"
            f"{DOT} Цены как в магазине, 60 запросов в минуту"
        )
        rows = [[("Создать ключ", "apikey", "confirm")], [("Документация", "apidocs", "payments")]]
    await show(message, text, rows + [[("Профиль", "profile", "user", ""), HOME]], edit, banner=banner("api"))


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
            "📝 <b>Пополнения</b>\n\n⚡️ Пополнений пока нет.",
            [[("Пополнить кошелёк", "wallet")], [HOME]],
            edit,
        )
        return
    lines = [
        f"{status_icon(p.status)} <b>${display(p.amount)}</b> · "
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
        buttons + [navigation, [("Кошелёк", "wallet"), HOME]],
        edit,
    )


async def payment_message(message, payment, edit=False):
    rows = []
    payable = payment.status == "pending" and payment.expires_at > now()
    provider = PROVIDER_LABELS.get(payment.provider, payment.provider)
    lines = [
        f"📝 <b>Счёт {provider}</b>",
        "",
        f"<blockquote>💸 На баланс: <b>${display(payment.amount)}</b>\n"
        f"{status_icon(payment.status)} Статус: {STATUS_LABELS.get(payment.status, payment.status)}</blockquote>",
    ]
    if payable and payment.url.startswith("https://"):
        rows.append([("Оплатить", payment.url, "confirm")])
    if payment.provider == "ton" and payable:
        lines += [
            "",
            f"{DOT} Сумма: ровно <code>{escape(payment.crypto_amount)}</code> TON "
            f"({'TESTNET' if settings.ton_testnet else 'MAINNET'})",
            f"{DOT} Адрес: <code>{escape(settings.ton_wallet)}</code>",
            f"{DOT} Комментарий (обязательно): <code>{escape(payment.reference)}</code>",
            "",
            f'💰 <a href="{escape(payment.url)}">Открыть в TON-кошельке</a>',
            "💡 <i>Курс зафиксирован на время счёта. Комиссия сети оплачивается отдельно.</i>",
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
            "❗️ Этот счёт сейчас недоступен для оплаты. Если перевод уже отправлен — "
            "проверьте статус или напишите в поддержку.",
        ]
    elif payment.provider == "demo":
        lines += ["", "🧪 Тестовый счёт подтверждается в админке."]
    if payment.status == "pending":
        lines.append("")
        lines.append(f"📆 Оплатить до {datetime.fromtimestamp(payment.expires_at, timezone.utc):%H:%M} UTC")
    if payment.status in ("pending", "expired"):
        rows.append([("Проверить оплату", "checkpay:" + payment.id)])
    rows.append([("Кошелёк", "wallet"), HOME])
    await show(message, "\n".join(lines), rows, edit)


async def wallet(message, user_id, edit=False):
    methods = available_methods()
    rows = [[(METHOD_LABELS[name], "deposit:" + name)] for name in methods]
    await show(
        message,
        "💰 <b>Кошелёк</b>\n\n"
        f"<blockquote>💰 Баланс: <b>${display(balance_of(user_id))}</b></blockquote>\n"
        f"{DOT} Баланс в долларах, оплата криптой\n"
        f"{DOT} Зачисление сразу после оплаты\n\n"
        + ("💸 Выберите способ пополнения:" if rows else "⚡️ Пополнение временно недоступно."),
        rows + [[HOME]],
        edit,
        banner=personal_banner("wallet", user_id),
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


@router.message(Command("profile"))
async def profile_command(message: Message, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user.id, message.from_user.username or "")
    await profile(message, message.from_user)


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
        elif action == "catalog":
            await catalog(message, int(argument.split(":")[0] or 0), edit=True)
        elif action == "product":
            await product_screen(message, user_id, int(argument), edit=True)
        elif action == "buy":
            product_id, price, token = argument.split(":")
            order = reserve_order(user_id, int(product_id), f"{user_id}:{token}", int(price))
            await show(
                message,
                "⚡️ <b>Получаем номер…</b>\n\n🔴 Это занимает несколько секунд",
                [],
                edit=True,
            )
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
                footer = "💡 <i>Оплата спишется с баланса сразу.</i>"
            else:
                rows = [[("Пополнить кошелёк", "wallet", "money")]]
                footer = f"💸 Не хватает <b>${display(price - balance)}</b> — пополните кошелёк."
            await show(
                message,
                "📆 <b>Продление номера</b>\n\n"
                f"📱 <code>{escape(format_phone(phone))}</code>\n{DOT} Срок: +1 месяц\n\n"
                f"<blockquote>💎 Цена: <b>${display(price)}</b>\n💰 Баланс: ${display(balance)}</blockquote>\n{footer}",
                rows + [[("Назад к номеру", "order:" + argument, "phone", "")]],
                edit=True,
            )
        elif action == "renewbuy":
            info = (await state.get_data()).get("renew_" + argument)
            if not info:
                raise ValueError("Подтверждение устарело. Откройте продление заново")
            order = reserve_order(
                user_id, info["product_id"], f"{user_id}:renew:{argument}", info["price"], info["parent_id"]
            )
            await show(message, "⚡️ <b>Продлеваем номер…</b>", [], edit=True)
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
        elif action == "again":
            await refresh_order(argument, user_id)
            await order_card(message, user_id, argument, edit=True, checked=True, again=True)
        elif action == "profile":
            await profile(message, query.from_user, edit=True)
        elif action == "ref":
            await referrals(message, user_id, edit=True)
        elif action == "api":
            await api_screen(message, user_id, edit=True)
        elif action == "apidocs":
            await show(
                message,
                "📝 <b>Документация API</b>\n\n" + API_DOCS.format(url=escape(public_url())),
                [[("API", "api", "send"), HOME]],
                edit=True,
            )
        elif action == "apikeyask":
            await show(
                message,
                f"{icon('cancel')} <b>Перевыпустить ключ?</b>\n\nСтарый ключ сразу перестанет работать.",
                [[("Да, перевыпустить", "apikey", "confirm")], [("Назад", "api", "send", "")]],
                edit=True,
            )
        elif action == "apikey":
            await api_screen(message, user_id, edit=True, new_key=issue_key(user_id))
        elif action == "wallet":
            await wallet(message, user_id, edit=True)
        elif action == "deposit":
            if argument not in available_methods():
                raise ValueError("Способ оплаты не подключён")
            await state.set_state(Deposit.amount)
            await state.update_data(provider=argument)
            await show(
                message,
                f"💸 <b>Пополнение · {PROVIDER_LABELS.get(argument, argument)}</b>\n\n"
                f"{DOT} Выберите сумму кнопкой ниже\n"
                f"{DOT} Или отправьте свою сообщением — от $1 до $10000, например <code>7.50</code>",
                [
                    [(f"${value}", f"amount:{argument}:{value}", "money", "") for value in QUICK_AMOUNTS[:3]],
                    [(f"${value}", f"amount:{argument}:{value}", "money", "") for value in QUICK_AMOUNTS[3:]],
                    [("Кошелёк", "wallet")],
                ],
                edit=True,
                banner=personal_banner("wallet", user_id),
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
        await error_screen(message, user_error(exc))
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
        rows.append([("Пополнить кошелёк", "wallet", "money")])
    if "активный счёт" in text:
        rows.append([("Открыть счета", "payments")])
    rows.append([HOME])
    # Errors are a new message: the screen the user acted on stays visible above.
    await show(message, f"{icon('cancel')} " + escape(text), rows)


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
        await show(
            message, f"{icon('cancel')} " + escape(str(exc)) + "\nОтправьте сумму ещё раз или /cancel.", []
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
    global BOT_USERNAME
    BOT_USERNAME = (await bot.me()).username or BOT_USERNAME
    dispatcher.include_router(bot_admin.router)
    dispatcher.include_router(router)
    commands = [
        BotCommand(command="start", description="Главное меню"),
        BotCommand(command="catalog", description="Купить номер Telegram США"),
        BotCommand(command="profile", description="Профиль"),
        BotCommand(command="orders", description="Мои номера"),
        BotCommand(command="wallet", description="Кошелёк"),
    ]
    await bot.set_my_commands(commands)
    for admin_id in settings.admin_id_set:
        try:
            await bot.set_my_commands(
                commands + [BotCommand(command="admin", description="Админка")],
                scope=BotCommandScopeChat(chat_id=admin_id),
            )
        except Exception:
            logging.getLogger("smena.bot").warning("Admin commands not set: chat has not started the bot")

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
