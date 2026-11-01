from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from app import bot_ui


def test_colored_buttons_have_native_telegram_styles():
    markup = bot_ui.keyboard([[("Buy", "catalog:0"), ("Wallet", "wallet"), ("Cancel", "cancel:abc")]])
    assert [b.style for b in markup.inline_keyboard[0]] == ["primary", "success", "danger"]


@pytest.mark.asyncio
async def test_premium_icons_are_loaded_from_telegram_not_invented(monkeypatch):
    monkeypatch.setattr(bot_ui, "ICONS", {})
    bot = SimpleNamespace(
        get_forum_topic_icon_stickers=AsyncMock(
            return_value=[SimpleNamespace(custom_emoji_id="12345", emoji="⭐")]
        )
    )
    await bot_ui.load_icons(bot)
    markup = bot_ui.keyboard([[("Buy", "catalog:0")]])
    assert markup.inline_keyboard[0][0].icon_custom_emoji_id == "12345"
    assert "12345" in bot_ui.icon("brand")


@pytest.mark.asyncio
async def test_rejected_premium_markup_falls_back_without_losing_button_action(monkeypatch):
    monkeypatch.setattr(bot_ui, "ICONS", {"catalog": "12345", "brand": "12345"})
    method = SendMessage(
        chat_id=123,
        text=bot_ui.icon("brand") + " Title",
        parse_mode="HTML",
        reply_markup=bot_ui.keyboard([[("Buy", "catalog:0")]]),
    )
    request = AsyncMock(
        side_effect=[TelegramBadRequest(method=method, message="CUSTOM_EMOJI_INVALID"), "sent"]
    )
    assert await bot_ui.PremiumFallback()(request, None, method) == "sent"
    retry = request.call_args.args[1]
    assert "<tg-emoji" not in retry.text
    assert retry.reply_markup.inline_keyboard[0][0].callback_data == "catalog:0"
    assert retry.reply_markup.inline_keyboard[0][0].icon_custom_emoji_id is None
    assert retry.reply_markup.inline_keyboard[0][0].style == "primary"
