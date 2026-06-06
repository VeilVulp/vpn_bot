"""B1–B10: IDOR white-box tests for subscription/config access."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers.admin_e2e_harness import RecordingFakeBot
from tests.security.helpers.security_harness import UserSecurityDriver, bind_bot, build_user_application
from vpn_bot.bot_handler import main_menu_callback, send_wg_config_again
from vpn_bot.utils import LanguageManager

pytestmark = [pytest.mark.security, pytest.mark.db]


def _cb_update(tg_id: int, data: str):
    from tests.security.helpers.security_harness import _callback_update

    return _callback_update(tg_id, tg_id, data)


@pytest.mark.asyncio
async def test_b1_attacker_cannot_resend_victim_wg_config(victim_wg_sub, attacker_user, mock_mikrotik):
    bot = RecordingFakeBot()
    update = _cb_update(attacker_user.telegram_id, f"get_wg_conf_{victim_wg_sub.id}")
    bind_bot(update, bot)
    context = MagicMock()
    context.bot = bot

    with patch("vpn_bot.wg_delivery.deliver_wg_config", new_callable=AsyncMock) as mock_deliver:
        await send_wg_config_again(update, context)
        mock_deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_b2_attacker_cannot_view_victim_wg_detail(victim_wg_sub, attacker_user, mock_mikrotik):
    app, bot = await build_user_application()
    driver = UserSecurityDriver(app, bot, attacker_user.telegram_id)
    await driver.tap(f"view_wg_sub_{victim_wg_sub.id}")
    text = driver.all_outbound_text()
    assert victim_wg_sub.unique_identifier not in text
    assert victim_wg_sub.assigned_ip not in text


@pytest.mark.asyncio
async def test_b3_attacker_cannot_view_victim_ovpn_password(victim_ovpn_sub, attacker_user, mock_mikrotik):
    app, bot = await build_user_application()
    driver = UserSecurityDriver(app, bot, attacker_user.telegram_id)
    await driver.tap(f"view_sub_{victim_ovpn_sub.id}")
    text = driver.all_outbound_text()
    assert victim_ovpn_sub.mikrotik_password not in text
    assert victim_ovpn_sub.mikrotik_username not in text


@pytest.mark.asyncio
async def test_b4_attacker_cannot_dl_ovpn_victim(victim_ovpn_sub, victim_ovpn_config, attacker_user, mock_mikrotik):
    bot = RecordingFakeBot()
    update = _cb_update(
        attacker_user.telegram_id,
        f"dl_ovpn_{victim_ovpn_sub.id}_{victim_ovpn_config.id}",
    )
    bind_bot(update, bot)
    context = MagicMock()
    context.bot = bot
    context.user_data = {}

    with patch("vpn_bot.user_features.send_ovpn_file", new_callable=AsyncMock) as mock_send:
        await main_menu_callback(update, context)
        mock_send.assert_not_awaited()


@pytest.mark.asyncio
async def test_b5_attacker_cannot_dl_info_victim(victim_ovpn_sub, attacker_user, mock_mikrotik):
    bot = RecordingFakeBot()
    update = _cb_update(attacker_user.telegram_id, f"dl_info_{victim_ovpn_sub.id}")
    bind_bot(update, bot)
    context = MagicMock()
    context.bot = bot
    context.user_data = {}

    with patch("vpn_bot.user_features.send_connection_info", new_callable=AsyncMock) as mock_info:
        await main_menu_callback(update, context)
        mock_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_b6_attacker_config_menu_no_username_leak(victim_ovpn_sub, attacker_user, mock_mikrotik):
    app, bot = await build_user_application()
    driver = UserSecurityDriver(app, bot, attacker_user.telegram_id)
    await driver.tap(f"get_config_{victim_ovpn_sub.id}")
    text = driver.all_outbound_text()
    assert victim_ovpn_sub.mikrotik_username not in text


@pytest.mark.asyncio
async def test_b7_owner_can_resend_own_wg_config(attacker_wg_sub, attacker_user, mock_mikrotik):
    bot = RecordingFakeBot()
    update = _cb_update(attacker_user.telegram_id, f"get_wg_conf_{attacker_wg_sub.id}")
    bind_bot(update, bot)
    context = MagicMock()
    context.bot = bot

    with patch("vpn_bot.wg_delivery.deliver_wg_config", new_callable=AsyncMock, return_value=True) as mock_deliver:
        await send_wg_config_again(update, context)
        mock_deliver.assert_awaited_once()


@pytest.mark.asyncio
async def test_b8_owner_cannot_resend_expired_wg(victim_wg_sub_expired, victim_user, mock_mikrotik):
    bot = RecordingFakeBot()
    update = _cb_update(victim_user.telegram_id, f"get_wg_conf_{victim_wg_sub_expired.id}")
    bind_bot(update, bot)
    context = MagicMock()
    context.bot = bot

    with patch("vpn_bot.wg_delivery.deliver_wg_config", new_callable=AsyncMock) as mock_deliver:
        await send_wg_config_again(update, context)
        mock_deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_b9_nonexistent_wg_config_denied(attacker_user, mock_mikrotik):
    bot = RecordingFakeBot()
    update = _cb_update(attacker_user.telegram_id, "get_wg_conf_999999")
    bind_bot(update, bot)
    context = MagicMock()
    context.bot = bot

    with patch("vpn_bot.wg_delivery.deliver_wg_config", new_callable=AsyncMock) as mock_deliver:
        await send_wg_config_again(update, context)
        mock_deliver.assert_not_awaited()


@pytest.mark.asyncio
async def test_b10_attacker_cannot_renew_victim_wg(victim_wg_sub, attacker_user, mock_mikrotik):
    app, bot = await build_user_application()
    driver = UserSecurityDriver(app, bot, attacker_user.telegram_id)
    await driver.tap(f"renew_wg_{victim_wg_sub.id}")
    text = driver.all_outbound_text()
    assert victim_wg_sub.unique_identifier not in text
    assert LanguageManager.get("renew.sub_not_found") in text or "not found" in text.lower()
