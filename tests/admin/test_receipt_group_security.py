"""Security tests for receipt Telegram group registration and menu access."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import Chat

from vpn_bot.admin_panel import list_pending_receipts
from vpn_bot.bot_handler import set_receipt_group
from vpn_bot.utils import LanguageManager

RECEIPT_GROUP_ID = -100777888999
SUPER_ADMIN_ID = 910_101
REGULAR_USER_ID = 910_102

pytestmark = [pytest.mark.security, pytest.mark.db]


def _group_message_update(user_id: int, chat_id: int, text: str = "/setreceipt") -> MagicMock:
    user = MagicMock()
    user.id = user_id
    chat = Chat(id=chat_id, type="supergroup")
    update = MagicMock()
    update.effective_user = user
    update.effective_chat = chat
    update.message = MagicMock()
    update.message.reply_text = AsyncMock()
    return update


@pytest.fixture(autouse=True)
def _load_locales():
    LanguageManager.load_locales()


@pytest.mark.asyncio
async def test_setreceipt_non_super_denied():
    update = _group_message_update(REGULAR_USER_ID, RECEIPT_GROUP_ID)
    context = MagicMock()
    context.bot = MagicMock()

    with patch("vpn_bot.admin_management.is_super_admin", new_callable=AsyncMock, return_value=False):
        await set_receipt_group(update, context)

    update.message.reply_text.assert_awaited_once()
    assert LanguageManager.get("admin.receipt_group.set_fail_admin") in update.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_setreceipt_non_group_admin_denied():
    update = _group_message_update(SUPER_ADMIN_ID, RECEIPT_GROUP_ID)
    context = MagicMock()
    context.bot = MagicMock()

    with (
        patch("vpn_bot.admin_management.is_super_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.is_telegram_group_admin", new_callable=AsyncMock, return_value=False),
    ):
        await set_receipt_group(update, context)

    update.message.reply_text.assert_awaited_once()
    assert LanguageManager.get("admin.receipt_group.set_fail_group_admin") in update.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_setreceipt_super_group_admin_succeeds():
    update = _group_message_update(SUPER_ADMIN_ID, RECEIPT_GROUP_ID)
    context = MagicMock()
    context.bot = MagicMock()
    mock_set_gid = AsyncMock()

    with (
        patch("vpn_bot.admin_management.is_super_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.is_telegram_group_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_settings_service.set_receipt_group_id", mock_set_gid),
    ):
        await set_receipt_group(update, context)

    mock_set_gid.assert_awaited_once_with(RECEIPT_GROUP_ID)
    update.message.reply_text.assert_awaited_once()
    assert LanguageManager.get("admin.receipt_group.set_success") in update.message.reply_text.await_args.args[0]


@pytest.mark.asyncio
async def test_receipt_group_menu_pending_denied_for_non_admin():
    text = LanguageManager.get("admin.receipt_group.menu_pending")
    update = MagicMock()
    update.effective_user = MagicMock(id=REGULAR_USER_ID)
    update.effective_chat = Chat(id=RECEIPT_GROUP_ID, type="supergroup")
    update.callback_query = None
    update.message = MagicMock()
    update.message.text = text
    context = MagicMock()

    mock_render = AsyncMock()

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=False),
        patch("vpn_bot.admin_panel._render_pending_receipts_list", mock_render),
    ):
        result = await list_pending_receipts(update, context)

    mock_render.assert_not_awaited()
