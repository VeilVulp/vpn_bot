"""WireGuard purchase menu when no plans are available."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler

from vpn_bot.bot_handler import buy_wg_service


@pytest.mark.asyncio
async def test_buy_wg_no_plans_edits_message():
    update = MagicMock()
    update.effective_user.id = 1
    update.callback_query = MagicMock()
    update.callback_query.data = "buy_wg"
    update.callback_query.message = MagicMock()
    update.callback_query.message.edit_text = AsyncMock()
    update.callback_query.message.reply_text = AsyncMock()
    update.callback_query.answer = AsyncMock()
    update.message = None

    context = MagicMock()
    context.user_data = {}

    with (
        patch("vpn_bot.utils.check_maintenance_status", AsyncMock(return_value=(False, ""))),
        patch("vpn_bot.bot_handler.check_sales_status", AsyncMock(return_value=(False, ""))),
        patch("vpn_bot.bot_handler.block_if_current_user_banned", AsyncMock(return_value=False)),
        patch("vpn_bot.bot_handler.check_and_refresh_keyboard", AsyncMock(return_value=False)),
        patch("vpn_bot.bot_handler.check_user_registration", AsyncMock(return_value=None)),
        patch("vpn_bot.bot_handler.fetch_user_by_telegram", AsyncMock(return_value=MagicMock())),
        patch("vpn_bot.bot_handler.maybe_gate_terms", AsyncMock(return_value=None)),
        patch("vpn_bot.bot_handler.list_purchasable_wg_profiles", AsyncMock(return_value=[])),
        patch("vpn_bot.bot_handler.LanguageManager.get", side_effect=lambda k, **kw: f"[{k}]" if k == "buy.no_plans" else "text"),
    ):
        result = await buy_wg_service(update, context)

    assert result == ConversationHandler.END
    update.callback_query.message.edit_text.assert_awaited()
    call_kwargs = update.callback_query.message.edit_text.await_args.kwargs
    assert "buy.no_plans" in update.callback_query.message.edit_text.await_args.args[0] or "[buy.no_plans]" in str(
        update.callback_query.message.edit_text.await_args.args[0]
    )
