import asyncio
import json
import logging
import secrets
import time
from collections import defaultdict

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message
from sqlalchemy import func, select

from app.config import settings
from app.db import Session
from app.models import Order, Payment, Product, User, now
from app.money import display, sale_price, usd
from app.payments import available_methods, check_payment, create_payment
from app.services import allocate_order, ensure_user, refresh_order, reserve_order
from app.supplier import RENTAL_LABEL, supplier

router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
last_action = defaultdict(float)


class Deposit(StatesGroup):
    amount = State()


def keyboard(rows):
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    **({"url": value} if value.startswith("https://") else {"callback_data": value}),
                )
                for label, value in row
            ]
            for row in rows
        ]
    )


def menu():
    rows = [
        [("📱 Купить номер", "catalog:0")],
        [("💳 Баланс", "wallet"), ("📦 Мои заказы", "orders")],
        [("Пополнения", "payments")],
    ]
    if "your_support" not in settings.support_url:
        rows.append([("Поддержка", settings.support_url)])
    return keyboard(rows)


async def send_menu(message, user_id):
    with Session() as db:
        user = db.get(User, user_id)
        balance = display(user.balance) if user else "0.00"
    prefix = "🧪 ДЕМО — номера и SMS тестовые.\n\n" if settings.demo_mode else ""
    await message.answer(
        f"{prefix}SMENA · номера и SMS\nБаланс: ${balance}\n\nВыберите действие.", reply_markup=menu()
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user.id, message.from_user.username or "")
    await send_menu(message, message.from_user.id)


def product_label(product):
    kind = {"new": "новый", "restart": "повторный", "renew": "продление"}[product.kind]
    return f"{product.name[:40]} · {kind} · ${display(sale_price(product))}"


async def catalog(message, offset=0, country_id=None):
    if country_id is None:
        with Session() as db:
            countries = db.execute(
                select(Product.country, func.min(Product.id))
                .where(Product.enabled.is_(True), Product.kind != "renew")
                .group_by(Product.country)
                .order_by(Product.country)
            ).all()
        rows = [[(country, f"country:{product_id}")] for country, product_id in countries]
        rows.append([("Главное меню", "home")])
        await message.answer("Выберите страну.\nПоиск: /search название сервиса", reply_markup=keyboard(rows))
        return
    with Session() as db:
        representative = db.get(Product, country_id)
        if not representative:
            raise ValueError("Страна не найдена")
        products = db.scalars(
            select(Product)
            .where(
                Product.enabled.is_(True), Product.kind != "renew", Product.country == representative.country
            )
            .order_by(Product.name)
            .offset(offset)
            .limit(11)
        ).all()
    rows = [[(product_label(p), f"product:{p.id}")] for p in products[:10]]
    nav = []
    if offset > 0:
        nav.append(("← Назад", f"catalog:{max(0, offset - 10)}:{country_id}"))
    if len(products) > 10:
        nav.append(("Далее →", f"catalog:{offset + 10}:{country_id}"))
    if nav:
        rows.append(nav)
    rows.append([("Все страны", "catalog:0"), ("Главное меню", "home")])
    await message.answer(
        "Выберите сервис:" if products else "Каталог пока пуст.", reply_markup=keyboard(rows)
    )


async def orders(message, user_id, offset=0, order_id=None):
    offset = max(0, offset)
    with Session() as db:
        statement = select(Order).where(Order.user_id == user_id)
        if order_id:
            statement = statement.where(Order.id == order_id)
        rows = db.scalars(
            statement.order_by(Order.created_at.desc(), Order.id.desc()).offset(offset).limit(11)
        ).all()
    if not rows:
        await message.answer("Заказов пока нет.", reply_markup=menu())
    for order in rows[:10]:
        buttons = [[("Проверить", "order:" + order.id)]]
        if order.status == "waiting" and getattr(supplier, "can_cancel", False):
            buttons.append([("Отменить заказ", "cancelask:" + order.id)])
        payload = json.loads(order.supplier_payload)
        url = payload.get("api", "")
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        if (
            parsed.scheme == "https"
            and parsed.hostname
            and (parsed.hostname == "api.cc" or parsed.hostname.endswith(".api.cc"))
        ):
            buttons.insert(0, [("Открыть приём SMS", url)])
        if order.status in ("active", "expired") and not order.parent_id:
            buttons.append([("Продлить номер", "renew:" + order.id)])
        extra = ""
        if payload:
            extra = f"\nДействует до: {payload.get('end_time', '—')}\nSMS: {payload.get('sms_count', 0)}"
        await message.answer(
            f"Заказ {order.id[:8]} · {order.status}\n{order.phone}\n"
            f"${display(order.price)}{extra}\n{order.sms or order.error}",
            reply_markup=keyboard(buttons),
        )
    navigation = []
    if not order_id and offset:
        navigation.append(("← Назад", f"orders:{max(0, offset - 10)}"))
    if not order_id and len(rows) > 10:
        navigation.append(("Далее →", f"orders:{offset + 10}"))
    if navigation or order_id:
        await message.answer(
            "История заказов",
            reply_markup=keyboard([navigation] if navigation else [[("Все заказы", "orders")]]),
        )


async def payments(message, user_id, offset=0):
    offset = max(0, offset)
    with Session() as db:
        rows = db.scalars(
            select(Payment)
            .where(Payment.user_id == user_id)
            .order_by(Payment.created_at.desc(), Payment.id.desc())
            .offset(offset)
            .limit(11)
        ).all()
    if not rows:
        await message.answer("Пополнений пока нет.", reply_markup=menu())
    for payment in rows[:10]:
        await payment_message(message, payment)
    navigation = []
    if offset:
        navigation.append(("← Назад", f"payments:{max(0, offset - 10)}"))
    if len(rows) > 10:
        navigation.append(("Далее →", f"payments:{offset + 10}"))
    if navigation:
        await message.answer("История пополнений", reply_markup=keyboard([navigation]))


async def payment_message(message, payment):
    rows = []
    payable = payment.status == "pending" and payment.expires_at > now()
    if payment.status in ("pending", "expired"):
        if payable and payment.url.startswith("https://"):
            rows.append([("Оплатить", payment.url)])
        rows.append([("Проверить оплату", "checkpay:" + payment.id)])
    text = f"Счёт {payment.id[:8]} · {payment.provider}\nНа баланс: ${display(payment.amount)}\nСтатус: {payment.status}"
    if payment.provider == "ton" and payable:
        text += (
            f"\n\nОтправьте ровно {payment.crypto_amount} TON\nСеть: "
            f"{'TESTNET' if settings.ton_testnet else 'MAINNET'}\nАдрес: {settings.ton_wallet}"
            f"\nОбязательный комментарий: {payment.reference}\nСсылка: {payment.url}"
            "\nКурс зафиксирован на время действия счёта. Комиссия сети оплачивается отдельно."
        )
    elif payment.provider == "ton" and payment.status != "paid":
        text += "\nЭтот счёт сейчас недоступен для оплаты. Если перевод уже отправлен — проверьте статус или обратитесь в поддержку."
    elif payment.provider == "demo":
        text += "\nДемо: подтвердите этот счёт в админке → Пополнения."
    if payment.status == "pending":
        from datetime import datetime, timezone

        text += "\nОплатить до " + datetime.fromtimestamp(payment.expires_at, timezone.utc).strftime(
            "%H:%M UTC"
        )
    await message.answer(text, reply_markup=keyboard(rows) if rows else menu())


@router.message(Command("orders"))
async def orders_command(message: Message):
    await orders(message, message.from_user.id)


@router.message(Command("search"))
async def search_command(message: Message):
    query = (message.text or "").partition(" ")[2].strip()[:100]
    if not query:
        await message.answer("Напишите /search Google или /search Telegram США")
        return
    statement = select(Product).where(Product.enabled.is_(True), Product.kind != "renew")
    for term in query.split()[:5]:
        statement = statement.where(
            Product.name.icontains(term, autoescape=True) | Product.country.icontains(term, autoescape=True)
        )
    with Session() as db:
        products = db.scalars(statement.order_by(Product.country, Product.name).limit(21)).all()
    rows = [[(p.country + " · " + product_label(p), f"product:{p.id}")] for p in products[:20]]
    rows.append([("Все страны", "catalog:0")])
    text = "Результаты поиска:" if products else "Ничего не найдено. Попробуйте другое название."
    if len(products) > 20:
        text += "\nПоказаны первые 20. Уточните страну или название."
    await message.answer(text, reply_markup=keyboard(rows))


@router.message(Command("payments"))
async def payments_command(message: Message):
    await payments(message, message.from_user.id)


@router.message(Command("cancel"))
async def cancel_command(message: Message, state: FSMContext):
    await state.clear()
    await send_menu(message, message.from_user.id)


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
    try:
        if data == "home":
            await state.clear()
            await send_menu(message, user_id)
        elif data.startswith("catalog:"):
            parts = data.split(":")
            await catalog(message, max(0, int(parts[1])), int(parts[2]) if len(parts) > 2 else None)
        elif data.startswith("country:"):
            await catalog(message, 0, int(data.split(":")[1]))
        elif data.startswith("product:"):
            with Session() as db:
                product = db.get(Product, int(data.split(":")[1]))
                if not product or not product.enabled:
                    raise ValueError("Товар недоступен")
                price = sale_price(product)
            token = secrets.token_hex(12)
            await message.answer(
                f"{product.name} · {product.country}\n"
                f"Тип: {'первичный номер' if product.kind == 'new' else 'повторное использование'}\nЦена: ${display(price)}\n"
                f"Остаток срока: {RENTAL_LABEL}. Автоматической отмены и возврата после выдачи номера нет.\nСредства будут списаны с баланса после подтверждения.",
                reply_markup=keyboard(
                    [
                        [("Подтвердить покупку", f"buy:{product.id}:{price}:{token}")],
                        [("Назад в каталог", "catalog:0")],
                    ]
                ),
            )
        elif data.startswith("buy:"):
            _, product_id, price, token = data.split(":")
            order = reserve_order(user_id, int(product_id), f"{user_id}:{token}", int(price))
            await message.answer(f"Заказ {order.id[:8]} принят. Получаем номер…")
            await allocate_order(order.id)
            await orders(message, user_id, order_id=order.id)
        elif data.startswith("renew:"):
            parent_id = data.split(":")[1]
            with Session() as db:
                parent = db.get(Order, parent_id)
                if not parent or parent.user_id != user_id or parent.status not in ("active", "expired"):
                    raise ValueError("Номер недоступен для продления")
                payload = json.loads(parent.supplier_payload)
                code = f"{payload.get('cate_id')}:{payload.get('app_id')}:3"
                product = db.scalar(
                    select(Product).where(Product.supplier_code == code, Product.enabled.is_(True))
                )
                if not product:
                    raise ValueError("Продление для этого номера пока не настроено")
                price = sale_price(product)
            token = secrets.token_hex(12)
            await state.update_data(
                **{"renew_" + token: {"product_id": product.id, "parent_id": parent_id, "price": price}}
            )
            await message.answer(
                f"Продлить {parent.phone}\nЦена: ${display(price)}\n"
                "Стандартный срок поставщика: для обычных номеров США — 1 месяц, для Великобритании, Австралии и Гонконга — 3 месяца, для годовых — 12 месяцев.",
                reply_markup=keyboard(
                    [[("Подтвердить продление", "renewbuy:" + token)], [("Мои заказы", "orders")]]
                ),
            )
        elif data.startswith("renewbuy:"):
            token = data.split(":")[1]
            info = (await state.get_data()).get("renew_" + token)
            if not info:
                raise ValueError("Подтверждение устарело. Откройте продление заново")
            order = reserve_order(
                user_id, info["product_id"], f"{user_id}:renew:{token}", info["price"], info["parent_id"]
            )
            await allocate_order(order.id)
            await orders(message, user_id, order_id=order.id)
        elif data == "orders" or data.startswith("orders:"):
            await orders(message, user_id, int(data.split(":")[1]) if ":" in data else 0)
        elif data.startswith("order:"):
            await refresh_order(data.split(":")[1], user_id)
            await orders(message, user_id, order_id=data.split(":")[1])
        elif data.startswith("cancelask:"):
            await message.answer(
                "Отменить заказ? Возврат зависит от подтверждения поставщика.",
                reply_markup=keyboard(
                    [[("Да, отменить", "cancel:" + data.split(":")[1])], [("Мои заказы", "orders")]]
                ),
            )
        elif data.startswith("cancel:"):
            await refresh_order(data.split(":")[1], user_id, cancel=True)
            await orders(message, user_id, order_id=data.split(":")[1])
        elif data == "wallet":
            with Session() as db:
                user = db.get(User, user_id)
            rows = [[(name, "deposit:" + name)] for name in available_methods()]
            await message.answer(
                f"Баланс: ${display(user.balance)}\nВыберите способ пополнения:",
                reply_markup=keyboard(rows + [[("Главное меню", "home")]]),
            )
        elif data.startswith("deposit:"):
            provider = data.split(":")[1]
            if provider not in available_methods():
                raise ValueError("Способ оплаты не подключён")
            await state.set_state(Deposit.amount)
            await state.update_data(provider=provider)
            await message.answer("Введите сумму пополнения в USD, например 5.00 (от $1).\n/cancel — отменить")
        elif data == "payments" or data.startswith("payments:"):
            await payments(message, user_id, int(data.split(":")[1]) if ":" in data else 0)
        elif data.startswith("checkpay:"):
            status = await check_payment(data.split(":")[1], user_id)
            await message.answer("Статус оплаты: " + status)
    except ValueError as exc:
        await message.answer(str(exc))
    except Exception as exc:
        logging.getLogger("smena.bot").warning("Action failed: type=%s", type(exc).__name__)
        await message.answer("Сервис временно недоступен. Проверьте историю перед повторной покупкой.")


@router.message(Deposit.amount)
async def deposit_amount(message: Message, state: FSMContext):
    try:
        data = await state.get_data()
        payment = await create_payment(
            message.from_user.id, data["provider"], usd((message.text or "").replace(",", "."))
        )
        await state.clear()
        await payment_message(message, payment)
    except ValueError as exc:
        await message.answer(str(exc))


async def run():
    settings.validate_runtime()
    if not settings.bot_token:
        raise SystemExit("Set BOT_TOKEN in .env")
    bot, dispatcher = Bot(settings.bot_token), Dispatcher()
    dispatcher.include_router(router)
    await bot.set_my_commands(
        [
            {"command": "start", "description": "Главное меню"},
            {"command": "orders", "description": "Мои заказы"},
            {"command": "search", "description": "Поиск сервиса и страны"},
            {"command": "payments", "description": "Пополнения"},
            {"command": "cancel", "description": "Отменить ввод"},
        ]
    )
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
