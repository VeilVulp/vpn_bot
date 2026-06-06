"""E5–E6: Banned user blocked at confirm_pay / confirm_pay_wg handlers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vpn_bot.bot_handler import process_purchase_flow, process_purchase_flow_wg
from vpn_bot.utils import LanguageManager

pytestmark = [pytest.mark.security, pytest.mark.db]


@pytest.mark.asyncio
async def test_e5_banned_confirm_pay_denied(banned_user, db_ovpn_profile, mock_mikrotik):
    query = MagicMock()
    query.data = "confirm_pay"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock()
    update.effective_user.id = banned_user.telegram_id

    context = MagicMock()
    context.user_data = {"selected_profile_id": db_ovpn_profile.id}

    with patch("vpn_bot.user_features.checkout_subscription", new_callable=AsyncMock) as mock_checkout:
        result = await process_purchase_flow(update, context)
        mock_checkout.assert_not_awaited()

    assert result is not None
    banned_msg = LanguageManager.get("user.account_banned")
    query.edit_message_text.assert_awaited()
    assert banned_msg in str(query.edit_message_text.await_args)


@pytest.mark.asyncio
async def test_e6_banned_confirm_pay_wg_denied(banned_user, db_wg_profile, mock_mikrotik):
    query = MagicMock()
    query.data = "confirm_pay_wg"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock()
    update.effective_user.id = banned_user.telegram_id

    context = MagicMock()
    context.user_data = {"selected_wg_profile_id": db_wg_profile.id}

    with patch("vpn_bot.user_features.finalize_wg_purchase", new_callable=AsyncMock) as mock_finalize:
        result = await process_purchase_flow_wg(update, context)
        mock_finalize.assert_not_awaited()

    assert result is not None
    banned_msg = LanguageManager.get("user.account_banned")
    query.edit_message_text.assert_awaited()
    assert banned_msg in str(query.edit_message_text.await_args)
