"""White-box security test harness for user and admin Telegram flows."""

from __future__ import annotations

import os
import time
from typing import Any

from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.ext import Application

from tests.helpers.admin_e2e_harness import RecordingFakeBot, build_admin_application
from vpn_bot.handler_registry import register_all_handlers
from vpn_bot.utils import LanguageManager


def bind_bot(update: Update, bot) -> None:
    """Attach bot to update tree so PTB shortcuts work in tests."""
    update.set_bot(bot)
    if update.message:
        update.message.set_bot(bot)
    if update.callback_query:
        update.callback_query.set_bot(bot)
        if update.callback_query.message:
            update.callback_query.message.set_bot(bot)


def _callback_update(user_id: int, chat_id: int, data: str, *, msg_id: int = 10) -> Update:
    user = User(id=user_id, is_bot=False, first_name="SecTest")
    chat = Chat(id=chat_id, type="private")
    msg = Message(message_id=msg_id, date=int(time.time()), text="x", chat=chat, from_user=user)
    query = CallbackQuery(
        id=str(int(time.time() * 1000) % 10_000_000),
        from_user=user,
        chat_instance="sec",
        data=data,
        message=msg,
    )
    return Update(update_id=int(time.time()), callback_query=query)


def _text_update(user_id: int, chat_id: int, text: str, *, msg_id: int = 1) -> Update:
    user = User(id=user_id, is_bot=False, first_name="SecTest")
    chat = Chat(id=chat_id, type="private")
    msg = Message(message_id=msg_id, date=int(time.time()), text=text, chat=chat, from_user=user)
    return Update(update_id=int(time.time()), message=msg)


class UserSecurityDriver:
    """Drive user-facing handlers through Application.process_update."""

    def __init__(
        self,
        app: Application,
        bot: RecordingFakeBot,
        user_id: int,
        *,
        chat_id: int | None = None,
    ):
        self.app = app
        self.bot = bot
        self.user_id = user_id
        self.chat_id = chat_id or user_id
        self._message_id = 1
        self._last_message: Message | None = None

    def _user(self) -> User:
        return User(id=self.user_id, is_bot=False, first_name="SecTest")

    def _chat(self) -> Chat:
        return Chat(id=self.chat_id, type="private")

    def _bind_bot(self, update: Update) -> None:
        update.set_bot(self.bot)
        if update.message:
            update.message.set_bot(self.bot)
        if update.callback_query:
            update.callback_query.set_bot(self.bot)
            if update.callback_query.message:
                update.callback_query.message.set_bot(self.bot)

    async def process(self, update: Update) -> None:
        self._bind_bot(update)
        await self.app.process_update(update)

    async def tap(self, callback_data: str) -> None:
        update = _callback_update(self.user_id, self.chat_id, callback_data, msg_id=self._message_id)
        self._message_id += 1
        self._last_message = update.callback_query.message
        await self.process(update)

    async def send_text(self, text: str) -> None:
        update = _text_update(self.user_id, self.chat_id, text, msg_id=self._message_id)
        self._message_id += 1
        self._last_message = update.message
        await self.process(update)

    def calls_of(self, method: str) -> list[dict[str, Any]]:
        return [c.kwargs for c in self.bot.calls if c.method == method]

    def last_edit_text(self) -> str | None:
        for rec in reversed(self.bot.calls):
            if rec.method == "edit_message_text":
                return rec.kwargs.get("text")
        return None

    def last_send_text(self) -> str | None:
        for rec in reversed(self.bot.calls):
            if rec.method == "send_message":
                return rec.kwargs.get("text")
        return None

    def all_outbound_text(self) -> str:
        parts: list[str] = []
        for rec in self.bot.calls:
            t = rec.kwargs.get("text") or rec.kwargs.get("caption")
            if t:
                parts.append(str(t))
        return "\n".join(parts)


async def build_user_application(
    bot: RecordingFakeBot | None = None,
) -> tuple[Application, RecordingFakeBot]:
    if not LanguageManager._loaded:
        LanguageManager.load_locales()
    token = os.getenv("BOT_TOKEN", "123456789:AAHdE2ETestTokenForSecurityWhitebox")
    app = Application.builder().token(token).build()
    register_all_handlers(app, include_user_handlers=True)
    await app.initialize()
    recording = bot or RecordingFakeBot()
    app.bot = recording
    return app, recording


async def build_admin_security_app(
    bot: RecordingFakeBot | None = None,
) -> tuple[Application, RecordingFakeBot]:
    return await build_admin_application(bot=bot, include_user_handlers=True)
