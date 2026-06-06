"""Admin backup import/export handlers (mocked BackupManager, no real pg_restore)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler

from vpn_bot.admin_panel import confirm_restore_action, manual_export, process_import


@pytest.mark.asyncio
async def test_manual_export_calls_backup_manager():
    update = MagicMock()
    update.effective_chat.id = 12345
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.message = MagicMock()
    update.callback_query.message.reply_text = AsyncMock()

    context = MagicMock()
    context.bot = MagicMock()

    with patch("vpn_bot.backup_manager.BackupManager") as mock_cls:
        mock_cls.return_value.send_backup_to_telegram = AsyncMock()
        result = await manual_export(update, context)

    mock_cls.return_value.send_backup_to_telegram.assert_awaited_once_with(
        12345, is_auto=False
    )
    assert result == ConversationHandler.END


@pytest.mark.asyncio
async def test_confirm_restore_success_deletes_temp_file(tmp_path):
    path = tmp_path / "temp_restore_test.sql"
    path.write_bytes(b"fake")

    update = MagicMock()
    query = MagicMock()
    query.data = "confirm_restore_db"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update.callback_query = query

    context = MagicMock()
    context.user_data = {"restore_path": str(path)}

    with patch(
        "vpn_bot.backup_manager.BackupManager.restore_database",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_restore:
        result = await confirm_restore_action(update, context)

    mock_restore.assert_awaited_once_with(str(path))
    assert not path.exists()
    assert "restore_path" not in context.user_data
    assert result == ConversationHandler.END


@pytest.mark.asyncio
async def test_confirm_restore_cancel_deletes_temp_file(tmp_path):
    path = tmp_path / "temp_restore_cancel.sql"
    path.write_bytes(b"fake")

    update = MagicMock()
    query = MagicMock()
    query.data = "cancel_restore_db"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update.callback_query = query

    context = MagicMock()
    context.user_data = {"restore_path": str(path)}

    with patch(
        "vpn_bot.backup_manager.BackupManager.restore_database",
        new_callable=AsyncMock,
    ) as mock_restore:
        result = await confirm_restore_action(update, context)

    mock_restore.assert_not_awaited()
    assert not path.exists()
    assert result == ConversationHandler.END


@pytest.mark.asyncio
async def test_process_import_rejects_non_sql_document():
    update = MagicMock()
    update.message = MagicMock()
    update.message.document = MagicMock()
    update.message.document.file_name = "backup.zip"
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    from vpn_bot.admin_panel import WAIT_IMPORT_FILE

    result = await process_import(update, context)
    assert result == WAIT_IMPORT_FILE
