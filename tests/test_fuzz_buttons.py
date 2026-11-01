import pytest

from app import bot
from tests.test_bot import FakeState, query

ACTIONS = [
    "home",
    "catalog:x",
    "catalog:-5",
    "product:999",
    "product:x",
    "buy:1:1:t",
    "buy:x",
    "renew:nope",
    "renewbuy:nope",
    "orders:x",
    "order:nope",
    "card:nope",
    "sms:nope",
    "again:nope",
    "wallet",
    "deposit:nope",
    "amount:demo:7",
    "amount:x",
    "payments:x",
    "pay:nope",
    "checkpay:nope",
    "profile",
    "ref",
    "api",
    "apidocs",
    "apikeyask",
    "help",
    "cancelask:x",
    "country:1",
    "",
    "subcheck",
    "x" * 64,
]


@pytest.mark.asyncio
@pytest.mark.parametrize("data", ACTIONS)
async def test_no_button_crashes_or_leaks_python_errors(database, data):
    bot.last_action.clear()
    event = query(data)
    event.from_user.first_name = "T"
    await bot.callback(event, FakeState())
    for call in event.message.answer.call_args_list + event.message.edit_text.call_args_list:
        text = call.args[0]
        assert (
            "invalid literal" not in text and "Traceback" not in text and "not enough values" not in text
        ), (data, text)
