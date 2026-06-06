"""WG interface edit hub: grouped sections and firewall sub-menu."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vpn_bot.admin_panel import (
    WG_INT_SETTINGS,
    _wg_return_after_edit,
    wg_edit_section_firewall,
    wg_edit_section_route,
    wg_interface_settings,
)


@pytest.mark.asyncio
async def test_firewall_section_labels_nat_vs_mangle():
    query = MagicMock()
    query.data = "wg_edit_sec_firewall"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    context = MagicMock()
    context.user_data = {"edit_wg_interface_id": 3}

    iface = MagicMock()
    iface.name = "wg1"
    iface.upstream_interface = "ether1"
    iface.routing_mark = "wg_mark"
    iface.nat_dst_address = "127.0.0.1"
    iface.gateway = "192.168.1.1"

    with patch(
        "vpn_bot.admin_panel.get_wg_interface_details",
        new_callable=AsyncMock,
        return_value=(iface, 0),
    ):
        state = await wg_edit_section_firewall(update, context)

    assert state == WG_INT_SETTINGS
    body = query.edit_message_text.await_args.args[0]
    assert "Mangle" in body or "مانگل" in body
    cbs = [
        b.callback_data
        for row in query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    assert "set_wg_rm_start" in cbs
    assert "set_wg_upstream_start" in cbs
    assert "wg_reapply_firewall" in cbs
    assert "wg_edit_sec_route" in cbs
    assert "set_wg_route_list_start" not in cbs
    assert "set_wg_route_step_table" not in cbs


@pytest.mark.asyncio
async def test_route_section_submenu_has_short_labels():
    query = MagicMock()
    query.data = "wg_edit_sec_route"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    context = MagicMock()
    context.user_data = {"edit_wg_interface_id": 3}

    iface = MagicMock()
    iface.name = "wg1"
    iface.route_table = "wg_mark"
    iface.routing_mark = None
    iface.route_dst_address = "0.0.0.0/0"
    iface.gateway = "192.168.1.1"
    iface.route_distance = 1

    with patch(
        "vpn_bot.admin_panel.get_wg_interface_details",
        new_callable=AsyncMock,
        return_value=(iface, 0),
    ):
        state = await wg_edit_section_route(update, context)

    assert state == WG_INT_SETTINGS
    assert context.user_data["wg_edit_section"] == "route"
    cbs = [
        b.callback_data
        for row in query.edit_message_text.await_args.kwargs["reply_markup"].inline_keyboard
        for b in row
    ]
    assert "set_wg_route_list_start" in cbs
    assert "set_wg_route_step_table" in cbs
    assert "set_wg_route_step_dst" in cbs
    assert "set_wg_route_step_gw" in cbs
    assert "set_wg_route_step_dist" in cbs
    assert "wg_edit_sec_firewall" in cbs


@pytest.mark.asyncio
async def test_back_from_client_section_returns_hub_not_admin_main():
    from vpn_bot.admin_panel import back_to_int_settings

    query = MagicMock()
    query.data = "back_to_int_settings"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    context = MagicMock()
    context.user_data = {"edit_wg_interface_id": 9, "wg_edit_section": "client"}

    with patch(
        "vpn_bot.admin_panel.wg_interface_settings", new_callable=AsyncMock
    ) as mock_hub:
        mock_hub.return_value = WG_INT_SETTINGS
        await back_to_int_settings(update, context)

    mock_hub.assert_awaited_once()
    assert context.user_data.get("wg_edit_section") is None


@pytest.mark.asyncio
async def test_return_after_text_edit_uses_reply_not_callback():
    """After MessageHandler (e.g. gateway), section menu must not call query.answer()."""
    message = MagicMock()
    message.reply_text = AsyncMock()
    update = MagicMock()
    update.callback_query = None
    update.message = message
    context = MagicMock()
    context.user_data = {"edit_wg_interface_id": 3, "wg_edit_section": "firewall"}

    iface = MagicMock()
    iface.name = "wg1"
    iface.upstream_interface = "ether1"
    iface.routing_mark = "rm"
    iface.nat_routing_mark = None
    iface.nat_dst_address = "127.0.0.1"
    iface.gateway = "10.0.0.1"

    with patch(
        "vpn_bot.admin_panel.get_wg_interface_details",
        new_callable=AsyncMock,
        return_value=(iface, 0),
    ):
        state = await _wg_return_after_edit(update, context, 3)

    assert state == WG_INT_SETTINGS
    message.reply_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_return_after_edit_route_section():
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.message = None
    context = MagicMock()
    context.user_data = {"edit_wg_interface_id": 3, "wg_edit_section": "route"}

    iface = MagicMock()
    iface.name = "wg1"
    iface.route_table = "main"
    iface.routing_mark = None
    iface.route_dst_address = "0.0.0.0/0"
    iface.gateway = "10.0.0.1"
    iface.route_distance = 1

    with patch(
        "vpn_bot.admin_panel.get_wg_interface_details",
        new_callable=AsyncMock,
        return_value=(iface, 0),
    ):
        state = await _wg_return_after_edit(update, context, 3)

    assert state == WG_INT_SETTINGS
    query.edit_message_text.assert_awaited_once()
