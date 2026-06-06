"""E1–E4: Banned user handler white-box security tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.security.helpers.security_harness import UserSecurityDriver, build_user_application
from vpn_bot.utils import LanguageManager

pytestmark = [pytest.mark.security, pytest.mark.db]


def _banned_signal(bot, texts: str) -> bool:
    banned_msg = LanguageManager.get("user.account_banned")
    if banned_msg in texts or "مسدود" in texts or "suspended" in texts.lower():
        return True
    return any(
        c.method == "answer_callback_query"
        and banned_msg in str(c.kwargs.get("text", ""))
        for c in bot.calls
    )


@pytest.mark.asyncio
async def test_e1_banned_support_menu_denied(banned_user, mock_mikrotik):
    app, bot = await build_user_application()
    driver = UserSecurityDriver(app, bot, banned_user.telegram_id)
    await driver.tap("support")
    assert _banned_signal(bot, driver.all_outbound_text())


@pytest.mark.asyncio
async def test_e2_banned_ticket_create_denied(banned_user, mock_mikrotik):
    app, bot = await build_user_application()
    driver = UserSecurityDriver(app, bot, banned_user.telegram_id)
    await driver.tap("ticket_create")
    assert _banned_signal(bot, driver.all_outbound_text())


@pytest.mark.asyncio
async def test_e3_banned_wallet_menu_denied(banned_user, mock_mikrotik):
    app, bot = await build_user_application()
    driver = UserSecurityDriver(app, bot, banned_user.telegram_id)
    with patch("vpn_bot.bot_handler.check_user_registration", new_callable=AsyncMock, return_value=None):
        await driver.tap("wallet_menu")
    assert _banned_signal(bot, driver.all_outbound_text())


@pytest.mark.asyncio
async def test_e4_banned_buy_service_denied(banned_user, mock_mikrotik):
    app, bot = await build_user_application()
    driver = UserSecurityDriver(app, bot, banned_user.telegram_id)
    with (
        patch("vpn_bot.utils.check_maintenance_status", new_callable=AsyncMock, return_value=(False, None)),
        patch("vpn_bot.bot_handler.check_sales_status", new_callable=AsyncMock, return_value=(False, None)),
    ):
        await driver.tap("buy_service")
    assert _banned_signal(bot, driver.all_outbound_text())
