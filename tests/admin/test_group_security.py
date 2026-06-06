"""Security tests for Telegram group ticket/backup admin access."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import CallbackQuery, Chat, Message, Update, User
from telegram.ext import ApplicationHandlerStop, ConversationHandler

from tests.helpers.admin_e2e_harness import AdminE2EDriver, RecordingFakeBot, build_admin_application
from vpn_bot.admin_permissions import PERM_BACKUP, PERM_RECEIPTS, PERM_TICKETS_ACTIVE, has_perm_in_set
from vpn_bot.admin_tickets import admin_list_tickets
from vpn_bot.config import config
from vpn_bot.support_tickets import set_support_group
from vpn_bot.utils import LanguageManager

SUPPORT_GROUP_ID = -100111222333
BACKUP_GROUP_ID = -100444555666
SUPER_ADMIN_ID = 900001
REGULAR_USER_ID = 900002
DB_ADMIN_ID = 900003

pytestmark = [pytest.mark.security, pytest.mark.db]


def _group_update(user_id: int, chat_id: int, text: str) -> MagicMock:
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


def _callback_update(user_id: int, chat_id: int, data: str) -> Update:
    user = User(id=user_id, is_bot=False, first_name="Test")
    chat = Chat(id=chat_id, type="private")
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
async def test_non_admin_group_menu_active_denied():
    text = LanguageManager.get("admin.support_group.menu_active")
    update = _group_update(REGULAR_USER_ID, SUPPORT_GROUP_ID, text)
    context = MagicMock()
    mock_list = AsyncMock(return_value=[])

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=False),
        patch("vpn_bot.admin_settings_service.get_support_group_id", new_callable=AsyncMock, return_value=SUPPORT_GROUP_ID),
        patch("vpn_bot.admin_tickets.get_tickets_by_filter", mock_list),
    ):
        result = await admin_list_tickets(update, context)

    assert result == ConversationHandler.END
    mock_list.assert_not_awaited()


@pytest.mark.asyncio
async def test_limited_admin_without_tickets_denied():
    text = LanguageManager.get("admin.support_group.menu_active")
    update = _group_update(DB_ADMIN_ID, SUPPORT_GROUP_ID, text)
    context = MagicMock()
    limited = {PERM_RECEIPTS}
    mock_list = AsyncMock(return_value=[])

    async def _has(_uid, perm):
        return has_perm_in_set(limited, perm)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch("vpn_bot.admin_settings_service.get_support_group_id", new_callable=AsyncMock, return_value=SUPPORT_GROUP_ID),
        patch("vpn_bot.admin_tickets.get_tickets_by_filter", mock_list),
    ):
        result = await admin_list_tickets(update, context)

    assert result == ConversationHandler.END
    mock_list.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_with_tickets_active_lists_in_support_group():
    text = LanguageManager.get("admin.support_group.menu_active")
    update = _group_update(DB_ADMIN_ID, SUPPORT_GROUP_ID, text)
    context = MagicMock()
    granted = {PERM_TICKETS_ACTIVE}

    async def _has(_uid, perm):
        return has_perm_in_set(granted, perm)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch("vpn_bot.admin_settings_service.get_support_group_id", new_callable=AsyncMock, return_value=SUPPORT_GROUP_ID),
        patch("vpn_bot.admin_tickets.get_tickets_by_filter", new_callable=AsyncMock, return_value=[]),
    ):
        result = await admin_list_tickets(update, context)

    assert result == ConversationHandler.END
    update.message.reply_text.assert_called_once()


@pytest.mark.asyncio
async def test_group_menu_wrong_chat_shows_hint():
    """Support group menu text in a non-support group must not list tickets."""
    text = LanguageManager.get("admin.support_group.menu_active")
    update = _group_update(DB_ADMIN_ID, -100999888777, text)
    context = MagicMock()
    mock_list = AsyncMock(return_value=[])
    granted = {PERM_TICKETS_ACTIVE}

    async def _has(_uid, perm):
        return has_perm_in_set(granted, perm)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch("vpn_bot.admin_settings_service.get_support_group_id", new_callable=AsyncMock, return_value=SUPPORT_GROUP_ID),
        patch("vpn_bot.admin_tickets.get_tickets_by_filter", mock_list),
    ):
        result = await admin_list_tickets(update, context)

    assert result == ConversationHandler.END
    mock_list.assert_not_awaited()
    update.message.reply_text.assert_awaited_once()
    hint = update.message.reply_text.await_args.args[0]
    assert LanguageManager.get("admin.wrong_chat_support") in hint


@pytest.mark.asyncio
async def test_backup_manual_non_admin_denied():
    app, bot, _ = await build_admin_application(include_user_handlers=False)
    driver = AdminE2EDriver(app, bot, REGULAR_USER_ID, chat_id=BACKUP_GROUP_ID)
    driver._chat = lambda: Chat(id=BACKUP_GROUP_ID, type="supergroup")

    manual_text = LanguageManager.get("admin.backup_group.menu_manual")
    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=False),
        patch.object(config, "BACKUP_GROUP_ID", str(BACKUP_GROUP_ID)),
    ):
        await driver.send_text(manual_text)

    assert not any(c.method == "send_document" for c in bot.calls)


@pytest.mark.asyncio
async def test_backup_manual_admin_without_backup_perm_denied():
    app, bot, _ = await build_admin_application(include_user_handlers=False)
    driver = AdminE2EDriver(app, bot, DB_ADMIN_ID, chat_id=BACKUP_GROUP_ID)
    driver._chat = lambda: Chat(id=BACKUP_GROUP_ID, type="supergroup")

    manual_text = LanguageManager.get("admin.backup_group.menu_manual")
    limited = {PERM_RECEIPTS}

    async def _has(_uid, perm):
        return has_perm_in_set(limited, perm)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
        patch.object(config, "BACKUP_GROUP_ID", str(BACKUP_GROUP_ID)),
    ):
        await driver.send_text(manual_text)

    assert not any(c.method == "send_document" for c in bot.calls)


@pytest.mark.asyncio
async def test_setsupport_non_super_admin_denied():
    update = _group_update(REGULAR_USER_ID, SUPPORT_GROUP_ID, "/setsupport")
    context = MagicMock()
    context.bot = MagicMock()

    with patch("vpn_bot.admin_management.is_super_admin", new_callable=AsyncMock, return_value=False):
        await set_support_group(update, context)

    update.message.reply_text.assert_called_once()
    assert LanguageManager.get("admin.support_group.set_fail_admin") in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_setsupport_super_admin_non_group_admin_denied():
    update = _group_update(SUPER_ADMIN_ID, SUPPORT_GROUP_ID, "/setsupport")
    context = MagicMock()
    context.bot = MagicMock()

    with (
        patch("vpn_bot.admin_management.is_super_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.is_telegram_group_admin", new_callable=AsyncMock, return_value=False),
    ):
        await set_support_group(update, context)

    update.message.reply_text.assert_called_once()
    assert LanguageManager.get("admin.support_group.set_fail_group_admin") in update.message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_callback_admin_ticket_non_admin_denied():
    from vpn_bot.admin_permissions import admin_callback_access_gate

    update = _callback_update(REGULAR_USER_ID, REGULAR_USER_ID, "admin_ticket_1")
    context = MagicMock()

    with patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=False):
        with pytest.raises(ApplicationHandlerStop):
            await admin_callback_access_gate(update, context)
