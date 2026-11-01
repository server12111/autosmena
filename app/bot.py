import asyncio
import json
import logging
import secrets
import time
from collections import defaultdict
from html import escape

from aiogram import Bot, Dispatcher, F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select

from app.bot_ui import PROVIDER_LABELS, STATUS_LABELS, PremiumFallback, icon, keyboard, load_icons
from app.catalog_policy import allowed_codes, permitted_code
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


def menu():
    rows = [
        [("Telegram · США", "catalog:0")],
        [("Кошелёк", "wallet"), ("Мои номера", "orders")],
        [("История пополнений", "payments")],
    ]
    if "your_support" not in settings.support_url:
        rows.append([("Поддержка", settings.support_url)])
    return keyboard(rows)


async def send_menu(message, user_id):
    with Session() as db:
        user = db.get(User, user_id)
        balance = display(user.balance) if user else "0.00"
    prefix = "<b>Тестовый режим</b> · номера и SMS ненастоящие\n\n" if settings.demo_mode else ""
    availability = (
        ""
        if settings.demo_mode or supplier.ready
        else "\n<i>Магазин готовится к запуску. Покупки пока недоступны.</i>"
    )
    await message.answer(
        f"{prefix}{icon('brand')} <b>SMENA</b>\n<i>Telegram-номера США · +1</i>\n\n"
        f"<blockquote>Ваш баланс\n<b>${balance} USD</b></blockquote>\n"
        f"Номера со сроком <b>больше 60 дней</b>.\nВыберите действие ниже.{availability}",
        parse_mode="HTML",
        reply_markup=menu(),
    )


@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    ensure_user(message.from_user.id, message.from_user.username or "")
    await send_menu(message, message.from_user.id)


def product_label(product):
    kind = {"new": "новый", "restart": "повторный", "renew": "продление"}[product.kind]
    return f"Telegram +1 · {kind} · ${display(sale_price(product))}"


async def catalog(message, offset=0, country_id=None):
    offset = max(0, offset)
    with Session() as db:
        products = db.scalars(
            select(Product)
            .where(
                Product.enabled.is_(True), Product.kind != "renew", Product.supplier_code.in_(allowed_codes())
            )
            .order_by(Product.cost, Product.id)
            .offset(offset)
            .limit(11)
        ).all()
    rows = [[(product_label(product), f"product:{product.id}")] for product in products[:10]]
    navigation = []
    if offset:
        navigation.append(("← Назад", f"catalog:{max(0, offset - 10)}"))
    if len(products) > 10:
        navigation.append(("Далее →", f"catalog:{offset + 10}"))
    if navigation:
        rows.append(navigation)
    rows.append([("Главное меню", "home")])
    description = (
        "Выберите подходящий номер ниже."
        if products
        else "Сейчас доступных номеров нет. Загляните немного позже."
    )
    await message.answer(
        f"{icon('catalog')} <b>Telegram · США</b>\n"
        f"Номера +1 со сроком <b>больше 60 дней</b>.\n\n{description}",
        parse_mode="HTML",
        reply_markup=keyboard(rows),
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
        archive = {
            order.id: not permitted_code(db.get(Product, order.product_id).supplier_code) for order in rows
        }
    if not rows:
        await message.answer("Заказов пока нет.", reply_markup=menu())
    for order in rows[:10]:
        buttons = [[("Проверить SMS", "order:" + order.id)]]
        if order.status == "waiting" and getattr(supplier, "can_cancel", False):
            buttons.append([("Отменить заказ", "cancelask:" + order.id)])
        payload = json.loads(order.supplier_payload)
        if order.status in ("active", "expired") and not order.parent_id and not archive[order.id]:
            buttons.append([("Продлить номер", "renew:" + order.id)])
        extra = ""
        if payload:
            extra = f"\nДействует до: {payload.get('end_time', '—')}\nSMS: {payload.get('sms_count', 0)}"
        await message.answer(
            f"{'Архивный заказ' if archive[order.id] else 'Telegram · США'}\n{order.phone}\nЗаказ {order.id[:8]} · {STATUS_LABELS.get(order.status, order.status)}\n"
            f"${display(order.price)}{extra}\n"
            f"{('Код Telegram: ' + order.sms) if order.sms else order.error or 'Ожидаем SMS. Код придёт в этот чат автоматически.'}",
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
    text = f"{PROVIDER_LABELS.get(payment.provider, payment.provider)} · счёт {payment.id[:8]}\nНа баланс: ${display(payment.amount)} USD\n{STATUS_LABELS.get(payment.status, payment.status)}"
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


@router.message(Command("search", "catalog"))
async def search_command(message: Message):
    # Keep old /search links working, but this is now a single-service store.
    await catalog(message)


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
                if not product or not product.enabled or not permitted_code(product.supplier_code):
                    raise ValueError("Товар недоступен")
                price = sale_price(product)
            token = secrets.token_hex(12)
            await message.answer(
                f"{icon('brand')} <b>Telegram · США</b>\n\n"
                "Виртуальный номер +1\n"
                f"Тип: {'первичный' if product.kind == 'new' else 'повторное использование'}\n"
                f"Остаток срока: {escape(RENTAL_LABEL)}\n\n"
                f"<blockquote>Стоимость\n<b>${display(price)} USD</b></blockquote>\n"
                "Номер появится после подтверждения.\n<i>После выдачи автоматической отмены и возврата нет.</i>",
                parse_mode="HTML",
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
                f"Продлить {parent.phone}\nЦена: ${display(price)}\nПродление номера США — на 1 месяц.",
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
            rows = [
                [
                    (
                        {
                            "cryptopay": "Crypto Bot",
                            "xrocket": "xRocket",
                            "ton": "TON · прямой перевод",
                            "demo": "Тестовое пополнение",
                        }[name],
                        "deposit:" + name,
                    )
                ]
                for name in available_methods()
            ]
            await message.answer(
                f"{icon('wallet')} <b>Кошелёк</b>\n\n"
                f"<blockquote>Доступно\n<b>${display(user.balance)} USD</b></blockquote>\n"
                + ("Выберите способ пополнения:" if rows else "Пополнение временно недоступно."),
                parse_mode="HTML",
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
            await message.answer("Статус оплаты: " + STATUS_LABELS.get(status, status))
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
    bot.session.middleware(PremiumFallback())
    await load_icons(bot)
    dispatcher.include_router(router)
    await bot.set_my_commands(
        [
            {"command": "start", "description": "Главное меню"},
            {"command": "orders", "description": "Мои заказы"},
            {"command": "catalog", "description": "Telegram-номера США"},
            {"command": "payments", "description": "Пополнения"},
            {"command": "cancel", "description": "Отменить ввод"},
        ]
    )
    await dispatcher.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run())
