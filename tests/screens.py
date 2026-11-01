"""Test double for an aiogram Message that records every screen the bot shows on it."""

from types import SimpleNamespace
from unittest.mock import AsyncMock


class FakeMessage:
    def __init__(self, photo=False, user_id=123, username="tester"):
        self.log = []
        self.photo = [SimpleNamespace(file_id="old")] if photo else None
        self.from_user = SimpleNamespace(id=user_id, username=username, first_name="T")
        self.chat = SimpleNamespace(type="private")
        for name in ("answer", "edit_text", "answer_photo", "edit_media", "delete"):
            setattr(self, name, AsyncMock(side_effect=self._recorder(name)))

    def _recorder(self, name):
        async def record(*args, **kwargs):
            self.log.append((name, args, kwargs))
            return SimpleNamespace(photo=[SimpleNamespace(file_id=f"fid-{len(self.log)}")])

        return record

    def screens(self):
        return [entry for entry in self.log if entry[0] != "delete"]

    def last(self):
        """(kind, text, markup) of the latest screen."""
        name, args, kwargs = self.screens()[-1]
        if name == "edit_media":
            return name, args[0].caption, kwargs.get("reply_markup")
        if name == "answer_photo":
            return name, kwargs.get("caption"), kwargs.get("reply_markup")
        return name, args[0], kwargs.get("reply_markup")

    def text(self):
        return self.last()[1]

    def buttons(self):
        markup = self.last()[2]
        return [
            b.callback_data or (b.copy_text and b.copy_text.text) or b.url
            for row in markup.inline_keyboard
            for b in row
        ]
