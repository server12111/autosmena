"""Native Telegram colors, premium icons, and a safe plain-emoji fallback."""

import logging
import re
from html import unescape

from aiogram.client.session.middlewares.base import BaseRequestMiddleware
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    CopyTextButton,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputMediaPhoto,
    LinkPreviewOptions,
)

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
# Every role maps to an emoji present in Telegram's forum-topic icon set, so every button
# and message icon can be upgraded to its premium version.
FALLBACK = {
    "brand": "✈️",
    "catalog": "🛒",
    "phone": "📱",
    "wallet": "💰",
    "orders": "📁",
    "payments": "📝",
    "confirm": "✅",
    "cancel": "❗️",
    "copy": "✍️",
    "refresh": "🔎",
    "support": "💬",
    "home": "🏠",
    "admin": "👑",
    "money": "💸",
    "stats": "📈",
    "user": "🪪",
    "price": "💎",
    "wait": "⚡️",
    "date": "📆",
    "test": "🧪",
    "star": "⭐️",
    "send": "📣",
    "fire": "🔥",
    "idea": "💡",
    "trophy": "🏆",
    "bullet": "🔴",
}
# Custom emoji chosen by the owner; they are used even if the forum icon set is unavailable.
FIXED_ICONS = {"bullet": "5375308238466074564"}
STATUS_ROLES = {
    "reserved": "wait",
    "allocating": "wait",
    "creating": "wait",
    "pending": "wait",
    "waiting": "confirm",
    "active": "confirm",
    "completed": "confirm",
    "paid": "confirm",
    "expired": "date",
    "refunded": "money",
    "review": "cancel",
}
ROLES_BY_ACTION = {
    "buy": "confirm",
    "renewbuy": "confirm",
    "product": "phone",
    "catalog": "catalog",
    "deposit": "money",
    "amount": "money",
    "wallet": "wallet",
    "order": "phone",
    "card": "phone",
    "orders": "orders",
    "renew": "date",
    "checkpay": "refresh",
    "pay": "payments",
    "payments": "payments",
    "sms": "refresh",
    "again": "refresh",
    "profile": "user",
    "ref": "star",
    "api": "send",
    "apidocs": "payments",
    "apikey": "confirm",
    "home": "home",
    "admin": "admin",
    "cancel": "cancel",
    "cancelask": "cancel",
    "apikeyask": "cancel",
}
# Every button is coloured: red for destructive, green for paying/confirming, blue otherwise.
STYLES = {"cancel": "danger", "confirm": "success", "money": "success", "catalog": "success"}


def _bare(emoji):
    return emoji.replace("\ufe0f", "")


def status_icon(status):
    return FALLBACK[STATUS_ROLES.get(status, "star")]


def role_for(value):
    return ROLES_BY_ACTION.get(value.split(":", 1)[0])


def button(label, value, role=None, style=None):
    """role=None picks a role from the callback; role="plain" draws a button without an icon.
    style overrides the colour; empty means the default blue."""
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
    style = style or STYLES.get(role, "primary")
    return InlineKeyboardButton(text=label, style=style, **options)


def keyboard(rows):
    return InlineKeyboardMarkup(inline_keyboard=[[button(*item) for item in row] for row in rows if row])


def icon(role):
    return FALLBACK[role]


_REVERSE = {}
for _role, _emoji in FALLBACK.items():
    _REVERSE.setdefault(_emoji, _role)
_PATTERN = re.compile("|".join(re.escape(e) for e in sorted(_REVERSE, key=len, reverse=True)))


def premium(text):
    """Upgrade known emoji in HTML text to premium custom emoji in one pass."""
    if not ICONS or not text or "<tg-emoji" in text:
        return text

    def replace(match):
        role = _REVERSE[match[0]]
        return f'<tg-emoji emoji-id="{ICONS[role]}">{match[0]}</tg-emoji>' if role in ICONS else match[0]

    return _PATTERN.sub(replace, text)


NO_PREVIEW = LinkPreviewOptions(is_disabled=True)


CAPTION_LIMIT = 1024
FILE_IDS = {}  # banner key -> Telegram file_id, so each picture is uploaded once per process


class Banner:
    """A screen picture sent as a photo with the text as caption (appears together with the text).

    key identifies the exact picture; file() builds the upload when Telegram has no copy yet."""

    def __init__(self, key, file):
        self.key, self.file = key, file

    def media(self):
        return FILE_IDS.get(self.key) or self.file()


def visible_length(html):
    return len(unescape(re.sub(r"<[^>]+>", "", html)))


def remember(sent, banner):
    photo = getattr(sent, "photo", None)
    file_id = photo[-1].file_id if photo else None
    if isinstance(file_id, str):
        FILE_IDS[banner.key] = file_id


async def drop(message):
    try:
        await message.delete()
    except Exception:
        pass  # Older than 48 hours or already gone: leaving it is harmless.


async def show(message, text, rows, edit=False, banner=None):
    """Navigation edits the same message, so the chat does not fill up with old screens.

    Screens with a banner are photo messages (picture and text arrive together); others are
    text. Switching between the two kinds sends the new screen and deletes the old one."""
    markup = keyboard(rows)
    text = premium(text)
    has_photo = bool(getattr(message, "photo", None))
    if banner is not None and visible_length(text) <= CAPTION_LIMIT:
        if edit and has_photo:
            try:
                sent = await message.edit_media(
                    InputMediaPhoto(media=banner.media(), caption=text, parse_mode="HTML"),
                    reply_markup=markup,
                )
                remember(sent, banner)
                return
            except TelegramBadRequest as error:
                if "not modified" in str(error).lower():
                    return
        sent = await message.answer_photo(
            banner.media(), caption=text, parse_mode="HTML", reply_markup=markup
        )
        remember(sent, banner)
        if edit:
            await drop(message)
        return
    if edit and has_photo:
        await message.answer(text, parse_mode="HTML", reply_markup=markup, link_preview_options=NO_PREVIEW)
        await drop(message)
        return
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


async def load_icons(bot):
    ICONS.update(FIXED_ICONS)
    try:
        by_emoji = {}
        for sticker in await bot.get_forum_topic_icon_stickers():
            if sticker.custom_emoji_id and sticker.custom_emoji_id.isdigit():
                by_emoji.setdefault(_bare(sticker.emoji or ""), sticker.custom_emoji_id)
        for role, emoji in FALLBACK.items():
            if _bare(emoji) in by_emoji:
                ICONS[role] = by_emoji[_bare(emoji)]
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
            strip = re.compile(r"</?tg-emoji\b[^>]*>")
            for field in ("text", "caption"):
                value = getattr(method, field, None)
                if isinstance(value, str) and "<tg-emoji" in value:
                    changes[field] = strip.sub("", value)
            media = getattr(method, "media", None)
            caption = getattr(media, "caption", None)
            if isinstance(caption, str) and "<tg-emoji" in caption:
                changes["media"] = media.model_copy(update={"caption": strip.sub("", caption)})
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


def user_error(exc):
    """Our own errors are written in Russian; anything else (int(), unpacking) is a stale or
    malformed button, and Python's message must not reach the customer."""
    text = str(exc)
    return text if re.search("[А-Яа-яЁё]", text) else "Кнопка устарела. Откройте меню заново."
