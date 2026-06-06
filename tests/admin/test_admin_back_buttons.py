"""Regression: admin multi-step flows expose inline back buttons."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vpn_bot.admin_menu import build_admin_back_markup


def test_build_admin_back_markup_wg_config_uses_admin_start():
    markup = build_admin_back_markup("admin_start")
    assert markup.inline_keyboard[0][0].callback_data == "admin_start"


@pytest.mark.asyncio
async def test_search_user_start_includes_back_button():
    from vpn_bot.admin_panel import search_user_start, SEARCH_USERNAME

    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    context = MagicMock()
    context.user_data = {}

    with patch("vpn_bot.admin_conversation.clear_admin_flow_context"):
        result = await search_user_start(update, context)

    assert result == SEARCH_USERNAME
    kwargs = query.edit_message_text.await_args.kwargs
    back_cb = kwargs["reply_markup"].inline_keyboard[-1][0].callback_data
    assert back_cb == "admin_start"
