from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app import bot
from app.catalog_policy import permitted_code
from app.config import Settings
from app.models import Product, User
from app.services import adjust_balance, reserve_order


@pytest.mark.parametrize("code", ["2:999:1", "5:711:1", "8:74:1", "6:74:1", "demo:gb:tg", "demo:us:wa"])
def test_other_services_countries_and_physical_numbers_are_forbidden(code):
    assert not permitted_code(code)


def test_old_enabled_button_cannot_buy_another_service(database):
    with database.begin() as db:
        db.get(Product, 1).supplier_code = "2:999:1"
    adjust_balance(123, 2000000, "policy-fund", "test")
    with pytest.raises(ValueError, match="Telegram"):
        reserve_order(123, 1, "old-wrong-service", 1000000)
    with database() as db:
        assert db.get(User, 123).balance == 2000000


@pytest.mark.asyncio
async def test_search_goes_straight_to_telegram_catalog_without_other_countries(database):
    message = SimpleNamespace(text="/search Google", answer=AsyncMock())
    await bot.search_command(message)
    text = message.answer.call_args.args[0]
    assert "Telegram" in text and "США" in text
    assert "Google" not in text and "Выберите страну" not in text


def test_missing_key_does_not_silently_enable_fake_numbers():
    config = Settings(_env_file=None, apicc_api_key="", demo_mode=None)
    assert config.demo_mode is False
