"""Regression: WG interface settings navigation must not mutate CallbackQuery.data (PTB v20+)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vpn_bot.admin_panel import (
    WG_INT_SETTINGS,
    back_to_int_settings,
    manage_wg_redirect,
    wg_interface_settings,
    wg_toggle_flow,
)


@pytest.mark.asyncio
async def test_back_to_int_settings_uses_interface_id_kwarg():
    query = MagicMock()
    query.data = "back_to_int_settings"
    query.answer = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    context = MagicMock()
    context.user_data = {"edit_wg_interface_id": 42}

    with patch(
        "vpn_bot.admin_panel.wg_interface_settings", new_callable=AsyncMock
    ) as mock_settings:
        mock_settings.return_value = WG_INT_SETTINGS
        result = await back_to_int_settings(update, context)

    assert result == WG_INT_SETTINGS
    assert query.data == "back_to_int_settings"
    mock_settings.assert_awaited_once_with(update, context, interface_id=42)


@pytest.mark.asyncio
async def test_wg_toggle_flow_does_not_mutate_callback_data():
    query = MagicMock()
    query.data = "wg_toggle_99"
    query.answer = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    context = MagicMock()
    context.user_data = {}

    with (
        patch(
            "vpn_bot.admin_panel.toggle_wg_subscription_status",
            new_callable=AsyncMock,
            return_value=(True, None),
        ),
        patch(
            "vpn_bot.admin_panel.manage_wg_redirect", new_callable=AsyncMock
        ) as mock_redirect,
    ):
        mock_redirect.return_value = 1
        await wg_toggle_flow(update, context)

    assert query.data == "wg_toggle_99"
    mock_redirect.assert_awaited_once_with(update, context, wg_sub_id=99)


@pytest.mark.asyncio
async def test_wg_interface_settings_from_explicit_id():
    query = MagicMock()
    query.data = "back_to_int_settings"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.message = None
    context = MagicMock()
    context.user_data = {"edit_wg_interface_id": 7}

    iface = MagicMock()
    iface.name = "wg0"
    iface.address = "10.0.0.1/24"
    iface.dns = "1.1.1.1"
    iface.endpoint_host = None
    iface.listen_port = 51820
    iface.mtu = 1420
    iface.keepalive = 25
    iface.upstream_interface = None
    iface.routing_mark = None
    iface.nat_dst_address = "0.0.0.0"
    iface.gateway = None
    iface.current_users = 1
    iface.max_users = 10

    with patch(
        "vpn_bot.admin_panel.get_wg_interface_details",
        new_callable=AsyncMock,
        return_value=(iface, 1),
    ):
        result = await wg_interface_settings(update, context, interface_id=7)

    assert result == WG_INT_SETTINGS
    query.edit_message_text.assert_awaited_once()
    cbs = [
        b.callback_data
        for row in query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    assert "wg_edit_sec_firewall" in cbs
    assert "wg_edit_sec_client" in cbs
    assert "set_wg_upstream_start" not in cbs


@pytest.mark.asyncio
async def test_manage_wg_redirect_accepts_explicit_sub_id():
    query = MagicMock()
    query.data = "wg_toggle_5"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    context = MagicMock()
    context.user_data = {}

    sub = MagicMock()
    sub.user_id = 1001
    sub.id = 5

    with patch(
        "vpn_bot.admin_panel.get_wg_subscription_comprehensive_info",
        new_callable=AsyncMock,
        return_value=(sub, {}),
    ), patch(
        "vpn_bot.admin_panel.format_wg_subscription_info_text",
        new_callable=AsyncMock,
        return_value="info",
    ):
        await manage_wg_redirect(update, context, wg_sub_id=5)

    assert query.data == "wg_toggle_5"
    query.edit_message_text.assert_awaited_once()
