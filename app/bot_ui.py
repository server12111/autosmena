"""Native Telegram colors, premium icons, and a safe plain-emoji fallback."""

import logging
import re

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

ICONS = {}
STATUS_LABELS = {
    "creating": "Создаём счёт",
    "pending": "Ожидает оплаты",
    "paid": "Оплачено",
    "review": "На проверке",
    "reserved": "Принят",
    "allocating": "Получаем номер",
    "waiting": "Ожидание SMS",
    "active": "Номер активен",
    "expired": "Срок истёк",
    "completed": "Завершён",
    "refunded": "Возврат на баланс",
}
PROVIDER_LABELS = {
    "cryptopay": "Crypto Bot",
    "xrocket": "xRocket",
    "ton": "TON",
    "demo": "Тестовое пополнение",
}
FALLBACK = {
    "brand": "✈️",
    "catalog": "📱",
    "wallet": "💳",
    "orders": "📂",
    "payments": "🧾",
    "confirm": "✅",
    "cancel": "✖️",
}


def role_for(value):
    action = value.split(":", 1)[0]
    if action in ("buy", "renewbuy"):
        return "confirm"
    if action in ("cancel", "cancelask"):
        return "cancel"
    if action in ("catalog", "country", "product"):
        return "catalog"
    if action in ("wallet", "deposit"):
        return "wallet"
    if action in ("orders", "order", "renew"):
        return "orders"
    if action in ("payments", "checkpay"):
        return "payments"
    return None


def keyboard(rows):
    output = []
    for row in rows:
        buttons = []
        for label, value in row:
            role = role_for(value)
            if value.startswith("https://") and label == "Оплатить":
                role = "confirm"
            style = (
                "danger"
                if role == "cancel"
                else "success"
                if role in ("confirm", "wallet")
                else "primary"
                if role == "catalog"
                else None
            )
            options = (
                {"url": value}
                if value.startswith(("https://", "http://", "tg://"))
                else {"callback_data": value}
            )
            if role in ICONS:
                options["icon_custom_emoji_id"] = ICONS[role]
            elif role and not any(label.startswith(icon) for icon in FALLBACK.values()):
                label = FALLBACK[role] + " " + label
            buttons.append(InlineKeyboardButton(text=label, style=style, **options))
        output.append(buttons)
    return InlineKeyboardMarkup(inline_keyboard=output)


def icon(role):
    fallback = FALLBACK[role]
    return f'<tg-emoji emoji-id="{ICONS[role]}">{fallback}</tg-emoji>' if role in ICONS else fallback


async def load_icons(bot):
    try:
        stickers = [
            s
            for s in await bot.get_forum_topic_icon_stickers()
            if s.custom_emoji_id and s.custom_emoji_id.isdigit()
        ]
        if not stickers:
            return
        choices = {
            "brand": ["✈️", "✈", "⭐️", "⭐"],
            "catalog": ["📱", "🔵"],
            "wallet": ["💳", "💰", "🟢"],
            "orders": ["📂", "📁", "🟣"],
            "payments": ["🧾", "💵", "🟡"],
            "confirm": ["✅", "🟢"],
            "cancel": ["❌", "🔴"],
        }
        for index, (role, emoji) in enumerate(choices.items()):
            selected = next((s for s in stickers if s.emoji in emoji), stickers[index % len(stickers)])
            ICONS[role] = selected.custom_emoji_id
    except Exception:
        logging.getLogger("smena.bot").warning("Premium icons unavailable; using standard emoji")


class PremiumFallback(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        try:
            return await make_request(bot, method)
        except TelegramBadRequest:
            changes = {}
            markup = getattr(method, "reply_markup", None)
            if isinstance(markup, InlineKeyboardMarkup) and any(
                b.icon_custom_emoji_id for row in markup.inline_keyboard for b in row
            ):
                changes["reply_markup"] = markup.model_copy(
                    update={
                        "inline_keyboard": [
                            [b.model_copy(update={"icon_custom_emoji_id": None}) for b in row]
                            for row in markup.inline_keyboard
                        ]
                    }
                )
            text = getattr(method, "text", None)
            if isinstance(text, str) and "<tg-emoji" in text:
                changes["text"] = re.sub(r"</?tg-emoji\b[^>]*>", "", text)
            if not changes:
                raise
            ICONS.clear()
            logging.getLogger("smena.bot").warning(
                "Telegram rejected premium markup; retrying with standard icons"
            )
            return await make_request(bot, method.model_copy(update=changes))
