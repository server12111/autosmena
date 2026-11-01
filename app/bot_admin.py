"""Owner panel inside the bot: stats, prices, users, balance and order review.

Only Telegram IDs from settings.admin_ids see or reach it.
"""

import logging
import secrets
from datetime import datetime, timezone
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select, update

from app import subscription
from app.bot_ui import STATUS_LABELS, format_phone, icon, show, status_icon, user_error
from app.catalog_policy import allowed_codes, permitted_code
from app.config import settings
from app.db import Session
from app.models import ApiKey, Audit, Ledger, Notice, Order, Payment, Product, User, now
from app.money import display, sale_price, usd
from app.services import adjust_balance, refund_order
from app.supplier import Rejected, supplier

router = Router()
router.message.filter(F.chat.type == ChatType.PRIVATE)
router.callback_query.filter(F.message.chat.type == ChatType.PRIVATE)
log = logging.getLogger("smena.admin")
KINDS = {"new": "новый", "restart": "повторный", "renew": "продление"}
BACK = ("Админка", "adm:home", "admin", "")
SOLD = ["completed", "active", "expired", "waiting"]


class AdminInput(StatesGroup):
    value = State()


def is_admin(user_id):
    return user_id in settings.admin_id_set


def money(value):
    return f"${display(value or 0)}"


def when(value):
    return datetime.fromtimestamp(value, timezone.utc).strftime("%d.%m.%Y %H:%M") if value else "—"


async def home(message, edit=False):
    day = now() - 86400
    with Session() as db:
        users = db.scalar(select(func.count()).select_from(User))
        new_users = db.scalar(select(func.count()).select_from(User).where(User.created_at >= day))
        balances = db.scalar(select(func.coalesce(func.sum(User.balance), 0)))
        deposits = db.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(Payment.status == "paid")
        )
        deposits_day = db.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.status == "paid", Payment.paid_at >= day
            )
        )
        sold = select(Order).where(Order.status.in_(SOLD)).subquery()
        sales = db.scalar(select(func.count()).select_from(sold))
        revenue = db.scalar(select(func.coalesce(func.sum(sold.c.price), 0)))
        margin = db.scalar(select(func.coalesce(func.sum(sold.c.price - sold.c.cost), 0)))
        sales_day = db.scalar(select(func.count()).select_from(sold).where(sold.c.created_at >= day))
        review = db.scalar(select(func.count()).select_from(Order).where(Order.status == "review"))
        referral_paid = db.scalar(
            select(func.coalesce(func.sum(Ledger.delta), 0)).where(Ledger.event.startswith("ref:"))
        )
        referred = db.scalar(select(func.count()).select_from(User).where(User.referrer_id.is_not(None)))
        api_keys = db.scalar(select(func.count()).select_from(ApiKey))
    mode = (
        "🧪 тестовый режим" if settings.demo_mode else ("✅ работает" if supplier.ready else "❗️ не настроен")
    )
    await show(
        message,
        f"{icon('admin')} <b>Админка</b>\n\n"
        f"Поставщик: {mode}\n\n"
        f"<blockquote>{icon('user')} Пользователи: <b>{users}</b> (+{new_users} за сутки)\n"
        f"{icon('money')} Пополнения: <b>{money(deposits)}</b> ({money(deposits_day)} за сутки)\n"
        f"{icon('phone')} Продажи: <b>{sales}</b> ({sales_day} за сутки)\n"
        f"{icon('stats')} Выручка: <b>{money(revenue)}</b> · маржа {money(margin)}\n"
        f"{icon('wallet')} На балансах клиентов: {money(balances)}\n"
        f"{icon('star')} Рефералов: {referred} · выплачено {money(referral_paid)}\n"
        f"{icon('send')} API-ключей: {api_keys}</blockquote>"
        + (f"\n\n{icon('cancel')} Заказов на проверке: <b>{review}</b>" if review else ""),
        [
            [("Запуск магазина", "adm:setup", "confirm" if supplier.ready else "cancel")],
            [("Товары и цены", "adm:products", "price"), ("Пользователь", "adm:find", "user")],
            [("Обязательная подписка", "adm:subs", "send")],
            [(f"На проверке · {review}", "adm:review", "cancel", "")] if review else [],
            [("Обновить", "adm:home", "refresh"), ("Меню", "home")],
        ],
        edit,
    )


async def products(message, edit=False):
    with Session() as db:
        rows = db.scalars(
            select(Product)
            .where(Product.supplier_code.in_(allowed_codes()))
            .order_by(Product.kind, Product.id)
        ).all()
    buttons = [
        [
            (
                f"{'Вкл' if p.enabled else 'Выкл'} · {KINDS.get(p.kind, p.kind)} · {money(sale_price(p))}",
                f"adm:p:{p.id}",
                "confirm" if p.enabled else "cancel",
                "",
            )
        ]
        for p in rows
    ]
    await show(
        message,
        f"{icon('price')} <b>Товары и цены</b>\n\n"
        + ("Выберите товар." if rows else "Товаров нет. Импортируйте каталог в веб-админке."),
        buttons + [[BACK]],
        edit,
    )


async def product(message, product_id, edit=False):
    with Session() as db:
        p = db.get(Product, product_id)
        if not p or not permitted_code(p.supplier_code):
            raise ValueError("Товар не найден")
    price_mode = (
        f"фиксированная {money(p.fixed_price)}"
        if p.fixed_price is not None
        else f"наценка {p.markup_bps / 100:g}%"
    )
    await show(
        message,
        f"{icon('price')} <b>{escape(p.name)}</b> · {KINDS.get(p.kind, p.kind)}\n\n"
        f"<blockquote>Закупка: {money(p.cost)}\n"
        f"Цена: {price_mode}\n"
        f"Продажа: <b>{money(sale_price(p))}</b> · маржа {money(sale_price(p) - p.cost)}\n"
        f"Статус: {'✅ в продаже' if p.enabled else '❗️ выключен'}</blockquote>",
        [
            [
                (
                    "Выключить" if p.enabled else "Включить",
                    f"adm:toggle:{p.id}",
                    "cancel" if p.enabled else "confirm",
                )
            ],
            [
                ("Цена $", f"adm:ask:price:{p.id}", "money", ""),
                ("Наценка %", f"adm:ask:markup:{p.id}", "stats"),
            ],
            [("Название", f"adm:ask:name:{p.id}", "copy")],
            [("Товары", "adm:products", "price", ""), BACK],
        ],
        edit,
    )


async def toggle(product_id, admin_id):
    with Session() as db:
        p = db.get(Product, product_id)
        if not p or not permitted_code(p.supplier_code):
            raise ValueError("Товар не найден")
        enable, code = not p.enabled, p.supplier_code
        if enable and sale_price(p) <= 0:
            raise ValueError("Сначала задайте цену")
    if enable and not settings.demo_mode:
        try:
            await supplier.check_rental_stock(code)
        except Rejected as exc:
            raise ValueError(str(exc)) from None
    with Session.begin() as db:
        db.get(Product, product_id).enabled = enable
        db.add(
            Audit(
                action="bot.product.toggle",
                detail=f"product={product_id}; enabled={enable}; admin={admin_id}",
            )
        )


def rename(product_id, text, admin_id):
    name = " ".join(text.split())
    if not 1 <= len(name) <= 128:
        raise ValueError("Название — от 1 до 128 символов")
    with Session.begin() as db:
        p = db.get(Product, product_id)
        if not p or not permitted_code(p.supplier_code):
            raise ValueError("Товар не найден")
        before, p.name = p.name, name
        db.add(
            Audit(
                action="bot.product.rename",
                detail=f"product={product_id}; before={before}; after={name}; admin={admin_id}",
            )
        )


def set_price(product_id, kind, text, admin_id):
    with Session.begin() as db:
        p = db.get(Product, product_id)
        if not p or not permitted_code(p.supplier_code):
            raise ValueError("Товар не найден")
        before = (p.fixed_price, p.markup_bps)
        if kind == "price":
            if text.lower() in ("авто", "auto", "-", "0"):
                p.fixed_price = None
            else:
                p.fixed_price = usd(text.lstrip("$").replace(",", "."))
        else:
            try:
                percent = float(text.rstrip("%").replace(",", "."))
            except ValueError:
                raise ValueError("Отправьте число, например 30") from None
            if not 0 <= percent <= 1000:
                raise ValueError("Наценка от 0 до 1000%")
            p.markup_bps, p.fixed_price = round(percent * 100), None
        if sale_price(p) < p.cost:
            raise ValueError(f"Цена ниже закупки ({money(p.cost)})")
        db.add(
            Audit(
                action="bot.product.price",
                detail=f"product={product_id}; before={before}; after={(p.fixed_price, p.markup_bps)}; admin={admin_id}",
            )
        )


def find_user(text):
    text = text.strip()
    with Session() as db:
        if text.lstrip("@").isdigit() and not text.startswith("@"):
            user = db.get(User, int(text))
        else:
            user = db.scalar(select(User).where(func.lower(User.username) == text.lstrip("@").lower()))
    if not user:
        raise ValueError("Пользователь не найден. Он должен хотя бы раз открыть бота")
    return user.id


async def user_card(message, user_id, edit=False):
    with Session() as db:
        user = db.get(User, user_id)
        if not user:
            raise ValueError("Пользователь не найден")
        orders = db.scalar(select(func.count()).select_from(Order).where(Order.user_id == user_id))
        spent = db.scalar(
            select(func.coalesce(func.sum(Order.price), 0)).where(
                Order.user_id == user_id, Order.status.in_(SOLD)
            )
        )
        paid = db.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.user_id == user_id, Payment.status == "paid"
            )
        )
    name = f"@{escape(user.username)}" if user.username else "без username"
    await show(
        message,
        f"{icon('user')} <b>Пользователь</b>\n\n"
        f"ID: <code>{user.id}</code> · {name}\n"
        f"С нами с {when(user.created_at)} UTC\n\n"
        f"<blockquote>Баланс: <b>{money(user.balance)}</b>\n"
        f"Пополнил: {money(paid)}\nЗаказов: {orders} · потратил {money(spent)}</blockquote>"
        + ("\n\n❗️ <b>Заблокирован</b>" if user.blocked else ""),
        [
            [
                ("Начислить", f"adm:ask:credit:{user.id}", "money"),
                ("Списать", f"adm:ask:debit:{user.id}", "cancel", ""),
            ],
            [("Разблокировать" if user.blocked else "Заблокировать", f"adm:block:{user.id}", "cancel", "")],
            [("Другой пользователь", "adm:find", "user"), BACK],
        ],
        edit,
    )


def change_balance(user_id, kind, text, admin_id, request_id):
    amount = usd(text.lstrip("$").replace(",", "."))
    delta = amount if kind == "credit" else -amount
    adjust_balance(user_id, delta, request_id, f"Бот-админ {admin_id}")
    if kind == "credit":
        with Session.begin() as db:
            event = "admin:" + request_id
            if not db.scalar(select(Notice.id).where(Notice.event == event)):
                db.add(
                    Notice(
                        user_id=user_id,
                        event=event,
                        text=f"✅ <b>Баланс пополнен на {money(amount)}</b>\n\nТеперь можно купить номер.",
                    )
                )


def setup_state():
    from app.payments import available_methods
    from app.preferences import supplier_pricing

    pricing = supplier_pricing()
    with Session() as db:
        enabled = db.scalar(
            select(func.count())
            .select_from(Product)
            .where(
                Product.enabled.is_(True), Product.kind != "renew", Product.supplier_code.in_(allowed_codes())
            )
        )
        imported = db.scalar(
            select(func.count()).select_from(Product).where(Product.supplier_code.in_(allowed_codes()))
        )
    return {
        "key": bool(settings.apicc_api_key) or settings.demo_mode,
        "currency": pricing["currency"],
        "rate": pricing["usd_rate"],
        "ready": supplier.ready,
        "imported": imported,
        "enabled": enabled,
        "payments": available_methods(),
    }


async def setup_screen(message, edit=False, note=""):
    st = setup_state()

    def mark(ok):
        return "✅" if ok else "❗️"

    currency = f"{st['currency']} (курс {st['rate']})" if st["currency"] else "не выбрана"
    lines = [
        "🚀 <b>Запуск магазина</b>",
        "",
        f"{mark(st['key'])} 1. Ключ api.cc на сервере (<code>APICC_API_KEY</code>)",
        f"{mark(st['ready'])} 2. Валюта кабинета api.cc: {escape(currency)}",
        f"{mark(st['imported'])} 3. Каталог импортирован: {st['imported']} товаров",
        f"{mark(st['enabled'])} 4. В продаже: {st['enabled']} товаров",
        f"{mark(st['payments'])} 5. Способы оплаты: {', '.join(st['payments']) or 'не подключены'}",
        "",
    ]
    if not st["key"]:
        lines.append("💡 Добавьте <code>APICC_API_KEY</code> в переменные Bothost и перезапустите бота.")
    elif not st["ready"]:
        lines.append("💡 Выберите валюту, в которой ведётся баланс вашего кабинета api.cc.")
    elif not st["imported"]:
        lines.append("💡 Импортируйте каталог — товары появятся выключенными.")
    elif not st["enabled"]:
        lines.append("💡 Откройте «Товары и цены», проверьте цену и включите товар.")
    elif not st["payments"]:
        lines.append("💡 Добавьте ключи оплаты (CRYPTOPAY_TOKEN / XROCKET_TOKEN) в переменные Bothost.")
    else:
        lines.append("🔥 Всё готово — магазин продаёт.")
    if note:
        lines += ["", note]
    rows = []
    if st["key"] and not settings.demo_mode:
        rows.append(
            [
                ("Валюта: USD", "adm:cur:USD", "money", ""),
                ("Другая валюта", "adm:ask:currency:0", "money", ""),
            ]
        )
    if st["ready"] and not settings.demo_mode:
        rows.append([("Импортировать каталог", "adm:sync", "confirm")])
    rows.append([("Товары и цены", "adm:products", "price"), ("Обновить", "adm:setup", "refresh")])
    await show(message, "\n".join(lines), rows + [[BACK]], edit)


def set_currency(text, admin_id):
    from app.preferences import save_supplier_pricing

    parts = text.upper().replace(",", ".").split()
    if len(parts) != 2:
        raise ValueError("Отправьте код валюты и курс к доллару, например: CNY 0.14")
    currency, rate = parts
    save_supplier_pricing(currency, rate)
    with Session.begin() as db:
        db.add(
            Audit(
                action="bot.supplier.currency", detail=f"currency={currency}; rate={rate}; admin={admin_id}"
            )
        )


async def subs_screen(message, edit=False):
    items = subscription.channels()
    if items:
        listing = "\n".join(f"🔴 {escape(item['title'])}" for item in items)
        text = (
            "📣 <b>Обязательная подписка</b>\n\n"
            "Пользоваться ботом можно только после подписки на эти каналы:\n\n"
            f"{listing}\n\n"
            "💡 Админы проверку не проходят. Удалить канал — нажмите на него."
        )
    else:
        text = (
            "📣 <b>Обязательная подписка</b>\n\n"
            "Сейчас выключена — ботом может пользоваться любой.\n\n"
            "💡 Добавьте канал, и бот попросит подписаться перед покупкой."
        )
    rows = [[(f"Удалить: {item['title'][:30]}", f"adm:subdel:{item['id']}", "cancel")] for item in items]
    rows.append([("Добавить канал", "adm:ask:channel:0", "confirm")])
    await show(message, text, rows + [[BACK]], edit)


async def review_list(message, edit=False):
    with Session() as db:
        rows = db.scalars(
            select(Order).where(Order.status == "review").order_by(Order.created_at).limit(20)
        ).all()
    await show(
        message,
        f"{icon('cancel')} <b>Заказы на проверке</b>\n\n"
        + (
            "Покупка у поставщика завершилась неясно. Проверьте кабинет api.cc: "
            "если номер не выдан и деньги не списаны — верните клиенту деньги."
            if rows
            else "Всё чисто."
        ),
        [
            [(f"{o.id[:8]} · {money(o.price)} · {when(o.created_at)}", f"adm:o:{o.id}", "cancel", "")]
            for o in rows
        ]
        + [[BACK]],
        edit,
    )


async def order_card(message, order_id, edit=False):
    with Session() as db:
        o = db.get(Order, order_id)
        if not o:
            raise ValueError("Заказ не найден")
    rows = []
    if o.status == "review":
        rows.append([("Вернуть деньги клиенту", f"adm:refundask:{o.id}", "cancel")])
    rows.append([("Клиент", f"adm:u:{o.user_id}", "user"), ("На проверке", "adm:review", "cancel", "")])
    await show(
        message,
        f"{status_icon(o.status)} <b>Заказ {o.id[:8]}</b>\n\n"
        f"Клиент: <code>{o.user_id}</code>\n"
        f"Статус: {STATUS_LABELS.get(o.status, o.status)}\n"
        f"Номер: {escape(format_phone(o.phone)) if o.phone else '—'}\n"
        f"Цена: {money(o.price)} · закупка {money(o.cost)}\n"
        f"Создан: {when(o.created_at)} UTC\n"
        + (f"\n<i>{escape(o.error)}</i>" if o.error else "")
        + ("\n\nЭто продление номера." if o.parent_id else ""),
        rows,
        edit,
    )


def refund(order_id, admin_id):
    with Session.begin() as db:
        changed = db.execute(
            update(Order).where(Order.id == order_id, Order.status == "review").values(status="refunding")
        ).rowcount
        if not changed:
            raise ValueError("Заказ уже не на проверке")
        refund_order(db, db.get(Order, order_id), "Возврат после сверки администратором")
        db.add(Audit(action="bot.order.refund", detail=f"order={order_id}; admin={admin_id}"))


PROMPTS = {
    "price": "Отправьте цену в долларах, например <code>0.45</code>. «авто» — вернуть расчёт по наценке.",
    "markup": "Отправьте наценку в процентах, например <code>30</code>.",
    "name": (
        "Отправьте новое название товара, например <code>Telegram США</code>.\n\n"
        "🔴 Повторный импорт каталога его не перезапишет."
    ),
    "credit": "Сколько начислить? Например <code>5</code> или <code>2.50</code>.",
    "debit": "Сколько списать? Например <code>1</code>.",
    "currency": (
        "Отправьте валюту кабинета api.cc и сколько долларов стоит 1 единица.\n"
        "Например <code>CNY 0.14</code> или <code>RUB 0.011</code>.\n\n"
        "🔴 После смены курса товары выключатся — проверьте цены и включите снова."
    ),
    "channel": (
        "Отправьте @username канала, ссылку t.me/… или ID вида <code>-100…</code>.\n\n"
        "🔴 Сначала добавьте бота в канал администратором — иначе он не увидит подписчиков."
    ),
}


@router.message(Command("admin"))
async def admin_command(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.clear()
    await home(message)


@router.callback_query(F.data.startswith("adm:"))
async def admin_callback(query: CallbackQuery, state: FSMContext):
    await query.answer()
    admin_id = query.from_user.id
    if not is_admin(admin_id):
        return
    message = query.message
    parts = (query.data or "").split(":")[1:]
    action = parts[0] if parts else "home"
    await state.set_state(None)
    try:
        if action == "home":
            await home(message, edit=True)
        elif action == "products":
            await products(message, edit=True)
        elif action == "p":
            await product(message, int(parts[1]), edit=True)
        elif action == "toggle":
            await toggle(int(parts[1]), admin_id)
            await product(message, int(parts[1]), edit=True)
        elif action == "ask":
            kind, target = parts[1], parts[2]
            await state.set_state(AdminInput.value)
            await state.update_data(admin_kind=kind, admin_target=target, admin_request=secrets.token_hex(12))
            back = (
                "Отмена",
                f"adm:p:{target}"
                if kind in ("price", "markup", "name")
                else "adm:subs"
                if kind == "channel"
                else "adm:setup"
                if kind == "currency"
                else f"adm:u:{target}",
                "cancel",
                "",
            )
            await show(message, f"{icon('admin')} {PROMPTS[kind]}", [[back]], edit=True)
        elif action == "find":
            await state.set_state(AdminInput.value)
            await state.update_data(admin_kind="find", admin_target="")
            await show(
                message,
                f"{icon('user')} Отправьте Telegram ID или @username пользователя.",
                [[("Отмена", "adm:home", "cancel", "")]],
                edit=True,
            )
        elif action == "u":
            await user_card(message, int(parts[1]), edit=True)
        elif action == "block":
            user_id = int(parts[1])
            with Session.begin() as db:
                user = db.get(User, user_id)
                if not user:
                    raise ValueError("Пользователь не найден")
                user.blocked = not user.blocked
                db.add(
                    Audit(
                        action="bot.user.block",
                        detail=f"user={user_id}; blocked={user.blocked}; admin={admin_id}",
                    )
                )
            await user_card(message, user_id, edit=True)
        elif action == "setup":
            await setup_screen(message, edit=True)
        elif action == "cur":
            from app.preferences import save_supplier_pricing

            save_supplier_pricing(parts[1], "1")
            await setup_screen(message, edit=True, note="✅ Валюта сохранена.")
        elif action == "sync":
            from app.catalog_policy import US_LABELS
            from app.supplier import sync_catalog

            await show(message, "⚡️ <b>Импортируем каталог…</b>", [], edit=True)
            count = 0
            for category, label in US_LABELS.items():
                count += await sync_catalog(category, label)
            await setup_screen(
                message,
                edit=True,
                note=f"✅ Импортировано товаров: {count}. Включите нужные в «Товары и цены».",
            )
        elif action == "subs":
            await subs_screen(message, edit=True)
        elif action == "subdel":
            subscription.remove(int(parts[1]), admin_id)
            await subs_screen(message, edit=True)
        elif action == "review":
            await review_list(message, edit=True)
        elif action == "o":
            await order_card(message, parts[1], edit=True)
        elif action == "refundask":
            await show(
                message,
                f"{icon('cancel')} <b>Вернуть деньги?</b>\n\n"
                "Подтвердите, только если в кабинете api.cc номер по этому заказу не выдан "
                "или деньги вернулись. Клиент получит сумму на баланс.",
                [
                    [("Да, вернуть", f"adm:refund:{parts[1]}", "confirm")],
                    [("Назад", f"adm:o:{parts[1]}", "phone", "")],
                ],
                edit=True,
            )
        elif action == "refund":
            refund(parts[1], admin_id)
            await order_card(message, parts[1], edit=True)
        else:
            await home(message, edit=True)
    except ValueError as exc:
        await show(message, f"{icon('cancel')} " + escape(user_error(exc)), [[BACK]])
    except Exception as exc:
        log.exception("Admin action failed: action=%s", action)
        # Admins need the reason to act on it (database locked, supplier error...). The text is
        # trimmed and never contains secrets: SQL parameters are hidden below.
        detail = str(getattr(exc, "orig", None) or exc).split("\n")[0][:300]
        await show(
            message,
            f"{icon('cancel')} Не получилось: {escape(type(exc).__name__)}\n<code>{escape(detail)}</code>",
            [[BACK]],
        )


@router.message(AdminInput.value, ~F.text.startswith("/"))
async def admin_input(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        await state.clear()
        return
    data = await state.get_data()
    kind, target, text = data.get("admin_kind"), data.get("admin_target"), (message.text or "").strip()
    try:
        if kind == "find":
            user_id = find_user(text)
            await state.set_state(None)
            await user_card(message, user_id)
        elif kind == "name":
            rename(int(target), text, message.from_user.id)
            await state.set_state(None)
            await product(message, int(target))
        elif kind in ("price", "markup"):
            set_price(int(target), kind, text, message.from_user.id)
            await state.set_state(None)
            await product(message, int(target))
        elif kind == "currency":
            set_currency(text, message.from_user.id)
            await state.set_state(None)
            await setup_screen(message, note="✅ Валюта сохранена.")
        elif kind == "channel":
            await subscription.add(message.bot, text, message.from_user.id)
            await state.set_state(None)
            await subs_screen(message)
        elif kind in ("credit", "debit"):
            change_balance(int(target), kind, text, message.from_user.id, data["admin_request"])
            await state.set_state(None)
            await user_card(message, int(target))
        else:
            await state.set_state(None)
            await home(message)
    except ValueError as exc:
        await show(
            message,
            f"{icon('cancel')} {escape(str(exc))}\nОтправьте ещё раз или нажмите «Отмена».",
            [[("Отмена", "adm:home", "cancel", "")]],
        )
