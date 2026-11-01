"""Required channel subscription, configured by admins in the bot.

The bot must be an administrator of each channel, otherwise Telegram does not reveal
membership. A channel the bot cannot check is skipped (fail open) and logged, so a
misconfigured channel never locks every customer out of the shop.
"""

import json
import logging
import time

import httpx

from app import db
from app.models import AppSetting, Audit

KEY = "required_channels"
OK_STATUSES = {"creator", "administrator", "member"}
CACHE_SECONDS = 60
log = logging.getLogger("smena.subscription")
_verified = {}


_client = None


def client():
    # One pooled client per process: checks run on every update of an unverified user.
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=15)
    return _client


async def member_status(bot, chat_id, user_id):
    """Raw getChatMember status. aiogram's typed models reject current Telegram answers for
    channel admins (a field the library marks required is not sent), so only the status
    string is read here. Returns None when Telegram refuses to answer."""
    response = await client().post(
        f"https://api.telegram.org/bot{bot.token}/getChatMember",
        json={"chat_id": chat_id, "user_id": user_id},
    )
    data = response.json()
    if not data.get("ok"):
        log.warning("getChatMember refused: chat=%s description=%s", chat_id, data.get("description", ""))
        return None
    result = data["result"]
    if result.get("status") == "restricted" and result.get("is_member"):
        return "member"
    return result.get("status")


def channels():
    with db.Session() as session:
        row = session.get(AppSetting, KEY)
    try:
        return json.loads(row.value) if row else []
    except ValueError:
        return []


def save(items, admin_id, action):
    with db.Session.begin() as session:
        session.merge(AppSetting(key=KEY, value=json.dumps(items, ensure_ascii=False)))
        session.add(Audit(action="bot.subscription." + action, detail=f"admin={admin_id}; channels={items}"))
    _verified.clear()


async def add(bot, reference, admin_id):
    reference = reference.strip()
    if reference.startswith("https://t.me/"):
        reference = "@" + reference.removeprefix("https://t.me/").split("/")[0]
    if not reference or not (reference.startswith("@") or reference.lstrip("-").isdigit()):
        raise ValueError("Отправьте @username канала или его ID вида -100…")
    try:
        chat = await bot.get_chat(int(reference) if reference.lstrip("-").isdigit() else reference)
    except Exception:
        raise ValueError("Канал не найден. Проверьте @username и что бот добавлен в канал") from None
    try:
        me = await bot.me()
        status = await member_status(bot, chat.id, me.id)
    except Exception as exc:
        log.warning("Bot admin check failed: chat=%s type=%s", chat.id, type(exc).__name__)
        status = None
    if status not in ("administrator", "creator"):
        raise ValueError("Сделайте бота администратором канала — иначе Telegram не показывает подписчиков")
    url = f"https://t.me/{chat.username}" if chat.username else chat.invite_link
    if not url:
        try:
            url = await bot.export_chat_invite_link(chat.id)
        except Exception:
            raise ValueError("У канала нет ссылки. Дайте боту право приглашать участников") from None
    items = [item for item in channels() if item["id"] != chat.id]
    items.append({"id": chat.id, "title": chat.title or reference, "url": url})
    save(items, admin_id, "add")
    return items[-1]


def remove(chat_id, admin_id):
    save([item for item in channels() if item["id"] != chat_id], admin_id, "remove")


async def missing(bot, user_id, fresh=False):
    """Channels the user still has to join. Successful checks are cached for a minute."""
    required = channels()
    if not required:
        return []
    if not fresh and time.monotonic() - _verified.get(user_id, -CACHE_SECONDS) < CACHE_SECONDS:
        return []
    absent = []
    for item in required:
        try:
            status = await member_status(bot, item["id"], user_id)
        except Exception as exc:
            log.warning("Subscription check skipped: channel=%s type=%s", item["id"], type(exc).__name__)
            continue
        if status is None:
            continue  # Telegram refused (bot removed from channel): fail open, logged above.
        if status not in OK_STATUSES:
            absent.append(item)
    if absent:
        _verified.pop(user_id, None)
    else:
        _verified[user_id] = time.monotonic()
    return absent
