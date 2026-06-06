"""Tests for scoped /admin routing and group callback allowlists."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.ext import ApplicationHandlerStop, ConversationHandler

from vpn_bot.admin_conversation import route_admin_command
from vpn_bot.admin_management import full_permission_preset
from vpn_bot.admin_permissions import (
    PERM_BACKUP,
    PERM_RECEIPTS,
    PERM_TICKETS,
    admin_callback_access_gate,
    has_perm_in_set,
    is_callback_allowed_in_scope,
    require_admin_message,
    resolve_group_admin_scope,
)
from vpn_bot.config import config
from vpn_bot.utils import LanguageManager

SUPPORT_GROUP_ID = -100111222333
BACKUP_GROUP_ID = -100444555666
RECEIPT_GROUP_ID = -100777888999
DB_ADMIN_ID = 900_003

pytestmark = [pytest.mark.security, pytest.mark.db]


def _group_update(user_id: int, chat_id: int, text: str = "/admin") -> MagicMock:
    user = User(id=user_id, is_bot=False, first_name="Test")
    chat = Chat(id=chat_id, type="supergroup")
    update = MagicMock()
    update.effective_user = user
    update.effective_chat = chat
    update.callback_query = None
    update.message = MagicMock()
    update.message.text = text
    update.message.reply_text = AsyncMock()
    return update


def _group_callback_update(user_id: int, chat_id: int, data: str) -> Update:
    user = User(id=user_id, is_bot=False, first_name="Test")
    chat = Chat(id=chat_id, type="supergroup")
    msg = Message(message_id=10, date=int(time.time()), text="x", chat=chat, from_user=user)
    query = CallbackQuery(
        id="1",
        from_user=user,
        chat_instance="test",
        data=data,
        message=msg,
    )
    return Update(update_id=2, callback_query=query)


@pytest.fixture(autouse=True)
def _load_locales():
    LanguageManager.load_locales()


@pytest.mark.asyncio
async def test_resolve_scope_receipt_group():
    with (
        patch.object(config, "BACKUP_GROUP_ID", None),
        patch(
            "vpn_bot.admin_settings_service.get_support_group_id",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "vpn_bot.admin_settings_service.get_receipt_group_id",
            new_callable=AsyncMock,
            return_value=RECEIPT_GROUP_ID,
        ),
    ):
        scope = await resolve_group_admin_scope(RECEIPT_GROUP_ID, chat_type="supergroup")
    assert scope == "receipt"


@pytest.mark.asyncio
async def test_admin_in_receipt_group_shows_receipt_hub():
    update = _group_update(DB_ADMIN_ID, RECEIPT_GROUP_ID)
    context = MagicMock()
    context.user_data = {}
    granted = {PERM_RECEIPTS}

    async def _has(_uid, perm):
        return has_perm_in_set(granted, perm)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch.object(config, "BACKUP_GROUP_ID", None),
        patch(
            "vpn_bot.admin_settings_service.get_support_group_id",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "vpn_bot.admin_settings_service.get_receipt_group_id",
            new_callable=AsyncMock,
            return_value=RECEIPT_GROUP_ID,
        ),
    ):
        await route_admin_command(update, context)

    update.message.reply_text.assert_called_once()
    text = update.message.reply_text.await_args.kwargs.get("text") or update.message.reply_text.await_args.args[0]
    markup = update.message.reply_text.await_args.kwargs.get("reply_markup")
    assert LanguageManager.get("admin.receipt_group.admin_menu") in text
    callbacks = [btn.callback_data for row in markup.inline_keyboard for btn in row]
    assert "pending_receipts" in callbacks
    assert "receipt_notif_mode" in callbacks
    assert "admin_start" not in callbacks


@pytest.mark.asyncio
async def test_admin_in_support_group_shows_ticket_menu():
    update = _group_update(DB_ADMIN_ID, SUPPORT_GROUP_ID)
    context = MagicMock()
    context.user_data = {}
    granted = {PERM_TICKETS}

    async def _has(_uid, perm):
        return has_perm_in_set(granted, perm)

    mock_ticket_menu = AsyncMock(return_value=ConversationHandler.END)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch.object(config, "BACKUP_GROUP_ID", None),
        patch(
            "vpn_bot.admin_settings_service.get_support_group_id",
            new_callable=AsyncMock,
            return_value=SUPPORT_GROUP_ID,
        ),
        patch(
            "vpn_bot.admin_settings_service.get_receipt_group_id",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("vpn_bot.admin_tickets.admin_ticket_menu", mock_ticket_menu),
    ):
        await route_admin_command(update, context)

    mock_ticket_menu.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_admin_start_denied_in_receipt_group():
    update = _group_callback_update(DB_ADMIN_ID, RECEIPT_GROUP_ID, "admin_start")
    context = MagicMock()
    granted = {PERM_RECEIPTS}

    async def _has(_uid, perm):
        return has_perm_in_set(granted, perm)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch.object(config, "BACKUP_GROUP_ID", None),
        patch(
            "vpn_bot.admin_settings_service.get_support_group_id",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "vpn_bot.admin_settings_service.get_receipt_group_id",
            new_callable=AsyncMock,
            return_value=RECEIPT_GROUP_ID,
        ),
    ):
        with pytest.raises(ApplicationHandlerStop):
            await admin_callback_access_gate(update, context)


@pytest.mark.asyncio
async def test_callback_list_servers_denied_in_support_group():
    update = _group_callback_update(DB_ADMIN_ID, SUPPORT_GROUP_ID, "list_servers")
    context = MagicMock()
    granted = {PERM_TICKETS, "servers"}

    async def _has(_uid, perm):
        return has_perm_in_set(granted, perm)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch.object(config, "BACKUP_GROUP_ID", None),
        patch(
            "vpn_bot.admin_settings_service.get_support_group_id",
            new_callable=AsyncMock,
            return_value=SUPPORT_GROUP_ID,
        ),
        patch(
            "vpn_bot.admin_settings_service.get_receipt_group_id",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        with pytest.raises(ApplicationHandlerStop):
            await admin_callback_access_gate(update, context)


@pytest.mark.asyncio
async def test_callback_pending_receipts_allowed_in_receipt_group():
    update = _group_callback_update(DB_ADMIN_ID, RECEIPT_GROUP_ID, "pending_receipts")
    context = MagicMock()
    granted = {PERM_RECEIPTS}

    async def _has(_uid, perm):
        return has_perm_in_set(granted, perm)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch.object(config, "BACKUP_GROUP_ID", None),
        patch(
            "vpn_bot.admin_settings_service.get_support_group_id",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "vpn_bot.admin_settings_service.get_receipt_group_id",
            new_callable=AsyncMock,
            return_value=RECEIPT_GROUP_ID,
        ),
    ):
        await admin_callback_access_gate(update, context)


@pytest.mark.asyncio
async def test_callback_backup_export_allowed_in_backup_group():
    update = _group_callback_update(DB_ADMIN_ID, BACKUP_GROUP_ID, "backup_export")
    context = MagicMock()
    granted = {PERM_BACKUP}

    async def _has(_uid, perm):
        return has_perm_in_set(granted, perm)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch.object(config, "BACKUP_GROUP_ID", str(BACKUP_GROUP_ID)),
        patch(
            "vpn_bot.admin_settings_service.get_support_group_id",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch(
            "vpn_bot.admin_settings_service.get_receipt_group_id",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        await admin_callback_access_gate(update, context)


@pytest.mark.asyncio
async def test_callback_admin_tickets_active_allowed_in_support_group_full_preset():
    """DB admin with full preset must tap ticket submenu callbacks in support group."""
    update = _group_callback_update(DB_ADMIN_ID, SUPPORT_GROUP_ID, "admin_tickets_active")
    context = MagicMock()
    granted = full_permission_preset()

    async def _has(_uid, perm):
        return has_perm_in_set(granted, perm)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch.object(config, "BACKUP_GROUP_ID", None),
        patch(
            "vpn_bot.admin_settings_service.get_support_group_id",
            new_callable=AsyncMock,
            return_value=SUPPORT_GROUP_ID,
        ),
        patch(
            "vpn_bot.admin_settings_service.get_receipt_group_id",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        await admin_callback_access_gate(update, context)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("chat_id", "backup_gid", "support_gid", "receipt_gid", "menu_patch"),
    [
        (
            BACKUP_GROUP_ID,
            str(BACKUP_GROUP_ID),
            None,
            None,
            "vpn_bot.admin_panel.send_backup_group_menu",
        ),
        (
            SUPPORT_GROUP_ID,
            None,
            SUPPORT_GROUP_ID,
            None,
            "vpn_bot.admin_tickets.admin_ticket_menu",
        ),
        (
            RECEIPT_GROUP_ID,
            None,
            None,
            RECEIPT_GROUP_ID,
            "vpn_bot.admin_panel.send_receipt_group_admin_menu",
        ),
    ],
)
async def test_admin_full_preset_routes_in_all_group_types(
    chat_id,
    backup_gid,
    support_gid,
    receipt_gid,
    menu_patch,
):
    """Full-preset DB admin can open scoped /admin hub in each registered group."""
    update = _group_update(DB_ADMIN_ID, chat_id)
    context = MagicMock()
    context.user_data = {}
    granted = full_permission_preset()

    async def _has(_uid, perm):
        return has_perm_in_set(granted, perm)

    mock_menu = AsyncMock(return_value=ConversationHandler.END)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch.object(config, "BACKUP_GROUP_ID", backup_gid),
        patch(
            "vpn_bot.admin_settings_service.get_support_group_id",
            new_callable=AsyncMock,
            return_value=support_gid,
        ),
        patch(
            "vpn_bot.admin_settings_service.get_receipt_group_id",
            new_callable=AsyncMock,
            return_value=receipt_gid,
        ),
        patch(menu_patch, mock_menu),
    ):
        await route_admin_command(update, context)

    mock_menu.assert_awaited_once()


@pytest.mark.asyncio
async def test_wrong_chat_context_shows_hint_not_silent():
    """Admin with permission in wrong group gets a helpful message."""
    update = _group_update(DB_ADMIN_ID, -100999888777)
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
        allowed = await require_admin_message(
            update, perm=PERM_RECEIPTS, chat_context="receipt_or_private"
        )

    assert allowed is False
    update.message.reply_text.assert_awaited_once()
    hint = update.message.reply_text.await_args.args[0]
    assert LanguageManager.get("admin.wrong_chat_receipt") in hint


def test_allowlist_helpers():
    assert is_callback_allowed_in_scope("private", "admin_start")
    assert is_callback_allowed_in_scope("receipt", "pending_receipts")
    assert is_callback_allowed_in_scope("receipt", "receipt_approve_12")
    assert not is_callback_allowed_in_scope("receipt", "admin_start")
    assert is_callback_allowed_in_scope("backup", "backup_export")
    assert not is_callback_allowed_in_scope("backup", "pending_receipts")
    assert is_callback_allowed_in_scope("support", "admin_tickets_active")
    assert is_callback_allowed_in_scope("support", "admin_tickets_notif_mode")
    assert not is_callback_allowed_in_scope("unknown", "admin_tickets")
