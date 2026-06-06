"""
Telegram Application E2E harness for admin panel tests.

Uses process_update with a recording bot (no real Telegram API calls).
"""

from __future__ import annotations

import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from telegram import (
    CallbackQuery,
    Chat,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    Update,
    User,
)
from telegram.ext import Application

from vpn_bot.admin_menu import MENU_TREE
from vpn_bot.handler_registry import register_all_handlers
from vpn_bot.utils import LanguageManager

# Callbacks that must not be auto-tapped during BFS (destructive or special setup).
BFS_SKIP_CALLBACKS = frozenset({
    "notify_broadcast",
    "backup_import",
    "add_wg_interface_start",
    "admin_tickets_create",
    "admin_tickets_search",
    "set_default_shared",
    "edit_shared_user",
    "upload_ovpn",
    "add_wg_profile",
    "admin_mgmt_add",
    "admin_add_start",
    "admin_remove_start",
    "admin_pf_save",
    "admin_ps_full",
    "admin_ps_limited",
    "force_clean_expired_subs",
    "force_clean_expired_wg_subs",
    "clean_expired_subs",
    "clean_expired_wg_subs",
    "clean_pending_receipts",
    "clean_old_transactions",
    "clean_closed_tickets",
    "clean_inactive_users",
    "clean_mt_orphans",
    "clear_mt_sessions",
})

BFS_SKIP_PREFIXES = (
    "admin_pe_",
    "admin_pt_",
    "server_test_",
    "view_receipt_",
    "receipt_",
    "edit_wg_interface_",
    "edit_wg_prof_",
    "del_wg_profile_",
    "wg_delete_",
    "wg_migrate_",
    "manage_sub_",
    "manage_wg_",
    "admin_user_hub_",
    "delete_",
    "ban_user_",
    "unban_user_",
    "admin_ticket_",
    "admin_reply_",
    "admin_close_",
)


def production_ack_required() -> None:
    import pytest

    if os.getenv("LIVE_ADMIN_ACK_PRODUCTION") != "1":
        pytest.skip("Set LIVE_ADMIN_ACK_PRODUCTION=1 to run admin panel E2E on real MikroTik")


def destructive_allowed() -> bool:
    return os.getenv("LIVE_ADMIN_FULL_DESTRUCTIVE") == "1"


@dataclass
class BotRecord:
    method: str
    kwargs: dict
    result: Any = None


class RecordingFakeBot:
    """Captures outbound Telegram API calls from handlers."""

    def __init__(self):
        self.calls: list[BotRecord] = []
        self._msg_id = 1000

    def _next_msg_id(self) -> int:
        self._msg_id += 1
        return self._msg_id

    def _record(self, method: str, **kwargs) -> Message:
        chat = kwargs.get("chat") or kwargs.get("chat_id")
        if isinstance(chat, int):
            chat = Chat(id=chat, type="private")
        msg = Message(
            message_id=self._next_msg_id(),
            date=int(time.time()),
            chat=chat,
            text=kwargs.get("text"),
        )
        self.calls.append(BotRecord(method=method, kwargs=kwargs, result=msg))
        return msg

    async def send_message(self, chat_id=None, text=None, reply_markup=None, parse_mode=None, **kwargs):
        chat = Chat(id=chat_id, type="private") if isinstance(chat_id, int) else chat_id
        return self._record("send_message", chat=chat, text=text, reply_markup=reply_markup, parse_mode=parse_mode, **kwargs)

    async def edit_message_text(
        self, text=None, chat_id=None, message_id=None, reply_markup=None, parse_mode=None, **kwargs
    ):
        chat = Chat(id=chat_id, type="private") if isinstance(chat_id, int) else chat_id
        msg = Message(message_id=message_id or self._msg_id, date=int(time.time()), chat=chat, text=text)
        self.calls.append(
            BotRecord(
                method="edit_message_text",
                kwargs={
                    "text": text,
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "reply_markup": reply_markup,
                    "parse_mode": parse_mode,
                    **kwargs,
                },
                result=msg,
            )
        )
        return True

    async def answer_callback_query(self, callback_query_id=None, text=None, show_alert=False, **kwargs):
        self.calls.append(BotRecord(method="answer_callback_query", kwargs=locals()))
        return True

    async def send_document(self, chat_id=None, document=None, filename=None, caption=None, **kwargs):
        self.calls.append(BotRecord(method="send_document", kwargs=locals()))
        return Message(message_id=self._next_msg_id(), date=int(time.time()), chat=Chat(id=chat_id, type="private"))

    async def send_photo(self, chat_id=None, photo=None, caption=None, **kwargs):
        self.calls.append(BotRecord(method="send_photo", kwargs=locals()))
        return Message(message_id=self._next_msg_id(), date=int(time.time()), chat=Chat(id=chat_id, type="private"))

    async def edit_message_reply_markup(self, reply_markup=None, chat_id=None, message_id=None, **kwargs):
        self.calls.append(BotRecord(method="edit_message_reply_markup", kwargs=locals()))
        return True

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    def last_markup(self) -> InlineKeyboardMarkup | None:
        for rec in reversed(self.calls):
            mk = rec.kwargs.get("reply_markup")
            if mk:
                return mk
        return None


def extract_callbacks(markup: InlineKeyboardMarkup | None) -> list[str]:
    if not markup:
        return []
    out = []
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.callback_data:
                out.append(btn.callback_data)
    return out


def should_skip_bfs_callback(cb: str) -> bool:
    if cb in BFS_SKIP_CALLBACKS:
        return True
    return any(cb.startswith(p) for p in BFS_SKIP_PREFIXES)


@dataclass
class CrawlResult:
    visited: set[str] = field(default_factory=set)
    errors: list[str] = field(default_factory=list)
    taps: list[str] = field(default_factory=list)


class AdminE2EDriver:
    """Drive admin panel through Application.process_update."""

    def __init__(
        self,
        app: Application,
        bot: RecordingFakeBot,
        admin_user_id: int,
        *,
        chat_id: int | None = None,
        permissions: set[str] | None = None,
        super_admin: bool | None = None,
    ):
        self.app = app
        self.bot = bot
        self.admin_user_id = admin_user_id
        self.chat_id = chat_id or admin_user_id
        self._message_id = 1
        self._last_message: Message | None = None
        self.errors: list[Exception] = []
        self._permissions = permissions
        self._super_admin = super_admin

    def _permission_patches(self):
        from contextlib import contextmanager
        from unittest.mock import AsyncMock, patch

        perms = self._permissions
        is_super = self._super_admin

        @contextmanager
        def ctx():
            patches = [
                patch(
                    "vpn_bot.admin_management.is_user_admin",
                    new_callable=AsyncMock,
                    return_value=True,
                ),
            ]
            if perms is None and is_super is None:
                yield
                return
            if is_super is not None:
                patches.append(
                    patch(
                        "vpn_bot.admin_management.is_super_admin",
                        new_callable=AsyncMock,
                        return_value=is_super,
                    )
                )
                patches.append(
                    patch(
                        "vpn_bot.admin_menu.is_super_admin",
                        new_callable=AsyncMock,
                        return_value=is_super,
                    )
                )
            if perms is not None:
                async def _get(_tid):
                    return set(perms)

                async def _has(_tid, p):
                    from vpn_bot.admin_permissions import has_perm_in_set

                    return has_perm_in_set(perms, p)

                patches.extend([
                    patch(
                        "vpn_bot.admin_management.get_admin_permissions",
                        new_callable=AsyncMock,
                        side_effect=_get,
                    ),
                    patch(
                        "vpn_bot.admin_permissions.get_admin_permissions",
                        new_callable=AsyncMock,
                        side_effect=_get,
                    ),
                    patch(
                        "vpn_bot.admin_management.has_admin_perm",
                        new_callable=AsyncMock,
                        side_effect=_has,
                    ),
                    patch(
                        "vpn_bot.admin_permissions.has_admin_perm",
                        new_callable=AsyncMock,
                        side_effect=_has,
                    ),
                ])
            started = [p.start() for p in patches]
            try:
                yield
            finally:
                for p in patches:
                    p.stop()

        return ctx()

    def _user(self) -> User:
        return User(id=self.admin_user_id, is_bot=False, first_name="AdminE2E")

    def _chat(self) -> Chat:
        return Chat(id=self.chat_id, type="private")

    def _next_message_id(self) -> int:
        self._message_id += 1
        return self._message_id

    def _bind_bot(self, update: Update) -> None:
        """Attach recording bot so Message.reply_text / edit_text shortcuts work."""
        bot = self.bot
        update.set_bot(bot)
        if update.message:
            update.message.set_bot(bot)
        if update.callback_query:
            update.callback_query.set_bot(bot)
            if update.callback_query.message:
                update.callback_query.message.set_bot(bot)

    async def process(self, update: Update) -> None:
        self._bind_bot(update)
        try:
            with self._permission_patches():
                await self.app.process_update(update)
        except Exception as exc:
            self.errors.append(exc)
            raise

    async def send_command(self, command: str) -> None:
        text = command if command.startswith("/") else f"/{command}"
        msg = Message(
            message_id=self._next_message_id(),
            date=int(time.time()),
            text=text,
            chat=self._chat(),
            from_user=self._user(),
        )
        self._last_message = msg
        self.bot.calls.clear()
        await self.process(Update(update_id=int(time.time()), message=msg))

    async def send_text(self, text: str) -> None:
        msg = Message(
            message_id=self._next_message_id(),
            date=int(time.time()),
            text=text,
            chat=self._chat(),
            from_user=self._user(),
        )
        self._last_message = msg
        await self.process(Update(update_id=int(time.time()), message=msg))

    def clear_processing_lock(self) -> None:
        """Clear safe_response is_processing lock between E2E steps."""
        from vpn_bot.utils import clear_user_processing

        for key in (self.chat_id, self.admin_user_id):
            try:
                bucket = self.app.user_data.get(key)
                if isinstance(bucket, dict):
                    clear_user_processing(type("_Ctx", (), {"user_data": bucket})())
            except Exception:
                pass
        try:
            for bucket in self.app.user_data.values():
                if isinstance(bucket, dict) and bucket.get("is_processing"):
                    clear_user_processing(type("_Ctx", (), {"user_data": bucket})())
        except Exception:
            pass

    async def tap(self, callback_data: str) -> None:
        msg = self._last_message or Message(
            message_id=self._next_message_id(),
            date=int(time.time()),
            text="menu",
            chat=self._chat(),
            from_user=self._user(),
        )
        query = CallbackQuery(
            id=str(int(time.time() * 1000) % 10_000_000),
            from_user=self._user(),
            chat_instance="e2e",
            data=callback_data,
            message=msg,
        )
        await self.process(Update(update_id=int(time.time()), callback_query=query))

    def callbacks_on_screen(self) -> list[str]:
        return extract_callbacks(self.bot.last_markup())

    async def open_admin_menu(self) -> None:
        await self.send_command("admin")
        await self.tap("admin_start")

    async def bfs_crawl(
        self,
        *,
        start: str = "admin_start",
        max_depth: int = 3,
        extra_roots: list[str] | None = None,
    ) -> CrawlResult:
        result = CrawlResult()
        await self.open_admin_menu()

        queue: deque[tuple[str, int]] = deque()
        if start != "admin_start":
            queue.append((start, 0))
        for child in MENU_TREE.get("admin_start", []):
            if child not in result.visited:
                queue.append((child, 1))
        if extra_roots:
            for r in extra_roots:
                queue.append((r, 1))

        while queue:
            cb, depth = queue.popleft()
            if cb in result.visited or depth > max_depth:
                continue
            if should_skip_bfs_callback(cb):
                result.visited.add(cb)
                continue
            result.visited.add(cb)
            try:
                await self.tap(cb)
                result.taps.append(cb)
            except Exception as exc:
                result.errors.append(f"{cb}: {exc}")
                continue

            for next_cb in self.callbacks_on_screen():
                if next_cb == "admin_start" or next_cb in result.visited:
                    continue
                if should_skip_bfs_callback(next_cb):
                    continue
                if depth + 1 <= max_depth:
                    queue.append((next_cb, depth + 1))

            if depth > 0 and "admin_start" in self.callbacks_on_screen():
                try:
                    await self.tap("admin_start")
                except Exception:
                    pass

        return result

    async def crawl_allowed_only(self, *, max_depth: int = 2) -> CrawlResult:
        """BFS only through callbacks visible on screen (RBAC-filtered menus)."""
        from vpn_bot.admin_permissions import callback_allowed_for_permissions

        granted = self._permissions or set()
        is_super = bool(self._super_admin)
        result = CrawlResult()
        await self.open_admin_menu()
        queue: deque[tuple[str, int]] = deque()
        for child in MENU_TREE.get("admin_start", []):
            if child not in result.visited:
                queue.append((child, 1))

        while queue:
            cb, depth = queue.popleft()
            if cb in result.visited or depth > max_depth:
                continue
            if should_skip_bfs_callback(cb):
                result.visited.add(cb)
                continue
            if not callback_allowed_for_permissions(cb, granted, is_super=is_super):
                result.visited.add(cb)
                continue
            result.visited.add(cb)
            try:
                await self.tap(cb)
                result.taps.append(cb)
            except Exception as exc:
                result.errors.append(f"{cb}: {exc}")
                continue
            for next_cb in self.callbacks_on_screen():
                if next_cb == "admin_start" or next_cb in result.visited:
                    continue
                if should_skip_bfs_callback(next_cb):
                    continue
                if not callback_allowed_for_permissions(next_cb, granted, is_super=is_super):
                    continue
                if depth + 1 <= max_depth:
                    queue.append((next_cb, depth + 1))
            if depth > 0 and "admin_start" in self.callbacks_on_screen():
                try:
                    await self.tap("admin_start")
                except Exception:
                    pass
        return result


async def build_admin_application(
    bot: RecordingFakeBot | None = None,
    *,
    include_user_handlers: bool = True,
) -> tuple[Application, RecordingFakeBot, Any]:
    """Build initialized Application with production handler registration."""
    if not LanguageManager._loaded:
        LanguageManager.load_locales()

    token = os.getenv("BOT_TOKEN", "123456789:AAHdE2ETestTokenForAdminPanelE2EOnly")
    app = Application.builder().token(token).build()
    register_all_handlers(app, include_user_handlers=include_user_handlers)

    await app.initialize()
    real_bot = app.bot
    recording = bot or RecordingFakeBot()
    app.bot = recording
    return app, recording, real_bot


def documented_menu_callbacks(*, include_super_admin: bool = False) -> list[str]:
    """Flat list of callbacks from MENU_TREE for coverage checks."""
    out: list[str] = []
    for parent, children in MENU_TREE.items():
        if parent == "admin_mgmt_menu" and not include_super_admin:
            continue
        out.append(parent)
        out.extend(children)
    return out
