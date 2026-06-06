"""WG add/edit: NAT upstream, routing mark, and NAT dst wizard."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vpn_bot.admin_panel import (
    WG_ADD_INT_MAN_UPSTREAM,
    _wg_has_firewall_template,
    wg_add_show_upstream_prompt,
)


def test_wg_has_firewall_template_requires_upstream_or_mark():
    assert not _wg_has_firewall_template(None)
    bare = MagicMock(upstream_interface=None, routing_mark=None)
    assert not _wg_has_firewall_template(bare)
    bare.routing_mark = "wg_main"
    assert _wg_has_firewall_template(bare)
    bare2 = MagicMock(upstream_interface="ether1", routing_mark=None)
    assert _wg_has_firewall_template(bare2)


@pytest.mark.asyncio
async def test_wg_add_show_upstream_prompt_lists_interfaces():
    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.message = None
    context = MagicMock()
    context.user_data = {"new_wg_iface_data": {"server_id": 1}}

    fake_server = MagicMock(id=1)
    with (
        patch("vpn_bot.admin_panel.get_server_by_id", new=AsyncMock(return_value=fake_server)),
        patch(
            "vpn_bot.admin_panel.fetch_upstream_interfaces_timed",
            new=AsyncMock(return_value=([{"name": "ether1", "running": True}], None)),
        ),
    ):
        state = await wg_add_show_upstream_prompt(update, context)

    assert state == WG_ADD_INT_MAN_UPSTREAM
    kwargs = update.callback_query.edit_message_text.await_args.kwargs
    cbs = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "man_wg_up_ether1" in cbs
    assert "man_wg_up_none" in cbs
