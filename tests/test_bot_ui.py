from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from app import bot_ui


def test_colored_buttons_have_native_telegram_styles():
    markup = bot_ui.keyboard(
        [[("Buy", "catalog:0"), ("Pay", "buy:1:1:x"), ("Top up", "deposit:demo"), ("Menu", "home")]]
    )
    assert [b.style for b in markup.inline_keyboard[0]] == ["primary", "success", "success", None]
    plain = bot_ui.keyboard([[("Back", "catalog:0", "catalog", "")]])
    assert plain.inline_keyboard[0][0].style is None


def test_copy_buttons_and_phone_formatting():
    markup = bot_ui.keyboard([[("Number", "+17752953734", "copy")]])
    assert markup.inline_keyboard[0][0].copy_text.text == "+17752953734"
    assert bot_ui.format_phone("17752953734") == "+1 775 295 3734"
    assert bot_ui.copy_phone("+1 775 295 3734") == "+17752953734"
    assert bot_ui.format_date("2027-01-26 10:00:00") == "26.01.2027"


@pytest.mark.asyncio
async def test_icons_are_matched_by_emoji_ignoring_variation_selector(monkeypatch):
    monkeypatch.setattr(bot_ui, "ICONS", {})
    stickers = [
        SimpleNamespace(custom_emoji_id="1", emoji="✈"),
        SimpleNamespace(custom_emoji_id="2", emoji="🛒"),
        SimpleNamespace(custom_emoji_id="3", emoji="🏠"),
    ]
    await bot_ui.load_icons(SimpleNamespace(get_forum_topic_icon_stickers=AsyncMock(return_value=stickers)))
    assert bot_ui.ICONS == {"brand": "1", "catalog": "2", "home": "3"}
    markup = bot_ui.keyboard([[("Buy", "catalog:0"), ("Menu", "home")]])
    assert [b.icon_custom_emoji_id for b in markup.inline_keyboard[0]] == ["2", "3"]


def test_premium_text_upgrades_every_known_emoji_once(monkeypatch):
    monkeypatch.setattr(bot_ui, "ICONS", {"brand": "1", "confirm": "2"})
    text = bot_ui.premium("✈️ <b>SMENA</b> ✅ ok 🙂")
    assert text == (
        '<tg-emoji emoji-id="1">✈️</tg-emoji> <b>SMENA</b> <tg-emoji emoji-id="2">✅</tg-emoji> ok 🙂'
    )
    assert bot_ui.premium(text) == text


@pytest.mark.asyncio
async def test_edit_errors_do_not_disable_premium_icons(monkeypatch):
    monkeypatch.setattr(bot_ui, "ICONS", {"catalog": "12345"})
    method = SendMessage(chat_id=1, text="x", reply_markup=bot_ui.keyboard([[("Buy", "catalog:0")]]))
    request = AsyncMock(
        side_effect=TelegramBadRequest(method=method, message="Bad Request: message is not modified")
    )
    with pytest.raises(TelegramBadRequest):
        await bot_ui.PremiumFallback()(request, None, method)
    assert bot_ui.ICONS == {"catalog": "12345"}


@pytest.mark.asyncio
async def test_rejected_premium_markup_falls_back_without_losing_button_action(monkeypatch):
    monkeypatch.setattr(bot_ui, "ICONS", {"catalog": "12345", "brand": "12345"})
    method = SendMessage(
        chat_id=123,
        text=bot_ui.premium("✈️ Title"),
        parse_mode="HTML",
        reply_markup=bot_ui.keyboard([[("Buy", "catalog:0")]]),
    )
    assert "<tg-emoji" in method.text
    request = AsyncMock(
        side_effect=[TelegramBadRequest(method=method, message="CUSTOM_EMOJI_INVALID"), "sent"]
    )
    assert await bot_ui.PremiumFallback()(request, None, method) == "sent"
    retry = request.call_args.args[1]
    assert "<tg-emoji" not in retry.text
    assert retry.reply_markup.inline_keyboard[0][0].callback_data == "catalog:0"
    assert retry.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id is None
    assert retry.reply_markup.inline_keyboard[0][0].style == "primary"
