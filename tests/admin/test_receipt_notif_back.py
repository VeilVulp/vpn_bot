"""Tests for receipt notification settings back navigation in group context."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Chat, User
from telegram.ext import ConversationHandler

from vpn_bot.admin_panel import (
    list_pending_receipts,
    send_receipt_group_admin_menu,
    toggle_receipt_notif_mode,
)
from vpn_bot.admin_permissions import PERM_RECEIPTS, has_perm_in_set
from vpn_bot.utils import LanguageManager

RECEIPT_GROUP_ID = -100777888999
DB_ADMIN_ID = 900_003

pytestmark = [pytest.mark.security, pytest.mark.db]


def _group_callback_update(user_id: int, chat_id: int, data: str) -> MagicMock:
    user = User(id=user_id, is_bot=False, first_name="Test")
    chat = Chat(id=chat_id, type="supergroup")
    update = MagicMock()
    update.effective_user = user
    update.effective_chat = chat
    update.callback_query = MagicMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    return update


def _back_callback_from_edit(mock_edit) -> str:
    markup = mock_edit.await_args.kwargs.get("reply_markup") or mock_edit.await_args.args[1]
    for row in markup.inline_keyboard:
        for btn in row:
            if btn.text == LanguageManager.get("common.back"):
                return btn.callback_data
    raise AssertionError("back button not found in keyboard")


@pytest.fixture(autouse=True)
def _load_locales():
    LanguageManager.load_locales()


@pytest.mark.asyncio
async def test_notif_back_from_group_hub_returns_to_hub():
    """Group hub → notification settings → back should return to receipt_group_admin."""
    update = _group_callback_update(DB_ADMIN_ID, RECEIPT_GROUP_ID, "receipt_notif_mode")
    context = MagicMock()
    context.user_data = {"receipt_notif_back": "receipt_group_admin"}
    context.bot = MagicMock()
    granted = {PERM_RECEIPTS}

    async def _has(_uid, perm):
        return has_perm_in_set(granted, perm)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch(
            "vpn_bot.admin_settings_service.get_receipt_notif_mode",
            new_callable=AsyncMock,
            return_value="pv",
        ),
    ):
        await toggle_receipt_notif_mode(update, context)

    assert _back_callback_from_edit(update.callback_query.edit_message_text) == "receipt_group_admin"


@pytest.mark.asyncio
async def test_notif_back_from_inbox_returns_to_pending_receipts():
    """Pending inbox → notification settings → back should return to pending_receipts."""
    hub_update = _group_callback_update(DB_ADMIN_ID, RECEIPT_GROUP_ID, "receipt_group_admin")
    hub_context = MagicMock()
    hub_context.user_data = {}
    hub_context.bot = MagicMock()
    granted = {PERM_RECEIPTS}

    async def _has(_uid, perm):
        return has_perm_in_set(granted, perm)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch(
            "vpn_bot.admin_settings_service.get_receipt_group_id",
            new_callable=AsyncMock,
            return_value=RECEIPT_GROUP_ID,
        ),
    ):
        await send_receipt_group_admin_menu(hub_update, hub_context)

    assert hub_context.user_data["receipt_notif_back"] == "receipt_group_admin"

    inbox_update = _group_callback_update(DB_ADMIN_ID, RECEIPT_GROUP_ID, "pending_receipts")
    inbox_context = MagicMock()
    inbox_context.user_data = dict(hub_context.user_data)
    inbox_context.bot = MagicMock()

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch(
            "vpn_bot.admin_settings_service.get_receipt_group_id",
            new_callable=AsyncMock,
            return_value=RECEIPT_GROUP_ID,
        ),
        patch("vpn_bot.admin_panel.count_pending_receipts", new_callable=AsyncMock, return_value=0),
    ):
        await list_pending_receipts(inbox_update, inbox_context)

    assert inbox_context.user_data["receipt_notif_back"] == "pending_receipts"

    notif_update = _group_callback_update(DB_ADMIN_ID, RECEIPT_GROUP_ID, "receipt_notif_mode")
    notif_context = MagicMock()
    notif_context.user_data = dict(inbox_context.user_data)
    notif_context.bot = MagicMock()

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch(
            "vpn_bot.admin_settings_service.get_receipt_notif_mode",
            new_callable=AsyncMock,
            return_value="pv",
        ),
    ):
        result = await toggle_receipt_notif_mode(notif_update, notif_context)

    assert result == ConversationHandler.END
    assert _back_callback_from_edit(notif_update.callback_query.edit_message_text) == "pending_receipts"
