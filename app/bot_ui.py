"""Native Telegram colors, premium icons, and a safe plain-emoji fallback."""

import logging
import re

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CopyTextButton, InlineKeyboardButton, InlineKeyboardMarkup

ICONS = {}
STATUS_LABELS = {
    "creating": "Создаём счёт",
    "pending": "Ожидает оплаты",
    "paid": "Оплачено",
    "review": "На проверке",
    "reserved": "Принят",
    "allocating": "Получаем номер",
    "waiting": "Активен",
    "active": "Активен",
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
    "copy": "📋",
    "refresh": "🔄",
    "support": "💬",
}
STATUS_ICONS = {
    "reserved": "⏳",
    "allocating": "⏳",
    "waiting": "🟢",
    "active": "🟢",
    "expired": "⚪️",
    "completed": "✅",
    "refunded": "↩️",
    "review": "🟡",
    "creating": "⏳",
    "pending": "🟡",
    "paid": "✅",
}
# Telegram forum-topic stickers are the only custom emoji a bot can rely on. Only exact
# matches are used: a random icon on a button looks broken.
ICON_CHOICES = {
    "brand": ["✈️", "✈", "⭐️", "⭐"],
    "catalog": ["📱", "📲"],
    "wallet": ["💳", "💰", "💵"],
    "orders": ["📂", "📁", "🗂"],
    "payments": ["🧾", "📝"],
    "confirm": ["✅", "✔️"],
    "cancel": ["❌", "✖️"],
    "copy": ["📋"],
    "refresh": ["🔄", "🔁"],
    "support": ["💬", "🗨"],
}


def role_for(value):
    action = value.split(":", 1)[0]
    if action in ("buy", "renewbuy", "amount"):
        return "confirm"
    if action in ("cancel", "cancelask"):
        return "cancel"
    if action in ("product",):
        return "catalog"
    if action in ("deposit",):
        return "wallet"
    if action == "order":
        return "orders"
    if action in ("checkpay", "pay"):
        return "payments"
    if action == "sms":
        return "refresh"
    return None


STYLES = {"cancel": "danger", "confirm": "success", "wallet": "success", "catalog": "primary"}


def button(label, value, role=None):
    """role=None picks a role from the callback; role="plain" draws a neutral button."""
    if role is None:
        role = role_for(value)
    if role == "plain":
        role = None
    if role == "copy":
        options = {"copy_text": CopyTextButton(text=value[:256])}
    elif value.startswith(("https://", "http://", "tg://")):
        options = {"url": value}
    else:
        options = {"callback_data": value}
    if role in ICONS:
        options["icon_custom_emoji_id"] = ICONS[role]
    elif role in FALLBACK and not any(label.startswith(icon) for icon in FALLBACK.values()):
        label = FALLBACK[role] + " " + label
    return InlineKeyboardButton(text=label, style=STYLES.get(role), **options)


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[button(*item) for item in row] for row in rows if row])


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
        for role, emoji in ICON_CHOICES.items():
            selected = next((s for s in stickers if s.emoji in emoji), None)
            if selected:
                ICONS[role] = selected.custom_emoji_id
    except Exception:
        logging.getLogger("smena.bot").warning("Premium icons unavailable; using standard emoji")


NOT_EMOJI_ERRORS = (
    "message is not modified",
    "message to edit not found",
    "message can't be edited",
    "query is too old",
    "chat not found",
    "bot was blocked",
)


class PremiumFallback(BaseRequestMiddleware):
    async def __call__(self, make_request, bot, method):
        try:
            return await make_request(bot, method)
        except TelegramBadRequest as error:
            # Ordinary edit errors must not disable premium icons for every later message.
            if any(text in str(error).lower() for text in NOT_EMOJI_ERRORS):
                raise
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


def format_phone(phone):
    """+17752953734 -> +1 775 295 3734; anything unexpected is shown as is."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) == 11 and digits.startswith("1"):
        return f"+1 {digits[1:4]} {digits[4:7]} {digits[7:]}"
    return phone or "—"


def copy_phone(phone):
    digits = re.sub(r"\D", "", phone or "")
    return "+" + digits if digits else ""


def format_date(value):
    """Supplier dates look like 2027-01-26 12:00:00; customers read 26.01.2027."""
    match = re.match(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return f"{match[3]}.{match[2]}.{match[1]}" if match else str(value or "")
