"""WG add wizard NAT callbacks and post-delete navigation to interface list."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from vpn_bot.admin_panel import (
    ConversationHandler,
    _wg_nat_dst_choice_keyboard,
    get_wg_add_man_natrm,
    wg_delete_confirm_exec,
)


def test_nat_dst_keyboard_add_wizard_uses_man_prefix():
    choices = [
        {"kind": "ip", "val": "127.0.0.1", "label": "local"},
        {"kind": "list", "val": "mylist", "label": "mylist"},
    ]
    markup = _wg_nat_dst_choice_keyboard(
        choices,
        "127.0.0.1",
        None,
        callback_prefix="man_wg_natdst",
        back_callback="list_wg_interfaces",
    )
    cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert cbs[0] == "man_wg_natdst_c_0"
    assert cbs[1] == "man_wg_natdst_c_1"
    assert cbs[-1] == "list_wg_interfaces"


@pytest.mark.asyncio
async def test_get_wg_add_man_natrm_keyboard_matches_handler_prefix():
    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.data = "man_wg_natrm_none"
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.message = None
    context = MagicMock()
    context.user_data = {"new_wg_iface_data": {"server_id": 1, "routing_mark": None}}

    fake_server = MagicMock(id=1)
    with (
        patch("vpn_bot.admin_panel.get_server_by_id", new=AsyncMock(return_value=fake_server)),
        patch(
            "vpn_bot.admin_panel.fetch_address_list_names_timed",
            new=AsyncMock(return_value=(["blocked"], None)),
        ),
    ):
        state = await get_wg_add_man_natrm(update, context)

    from vpn_bot.admin_panel import WG_ADD_INT_MAN_NAT

    assert state == WG_ADD_INT_MAN_NAT
    kwargs = update.callback_query.edit_message_text.await_args.kwargs
    cbs = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert any(cb.startswith("man_wg_natdst_c_") for cb in cbs)


@pytest.mark.asyncio
async def test_wg_delete_confirm_exec_returns_to_interface_list():
    query = MagicMock()
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    context = MagicMock()
    context.user_data = {
        "edit_wg_interface_id": 5,
        "wg_delete_interface_name": "bot_wg1",
    }

    iface = MagicMock()
    iface.server_id = 1
    fake_server = MagicMock()
    fake_mgr = MagicMock()

    with (
        patch(
            "vpn_bot.admin_panel.get_wg_interface_details",
            new=AsyncMock(return_value=(iface, 0)),
        ),
        patch(
            "vpn_bot.admin_panel.get_server_by_id",
            new=AsyncMock(return_value=fake_server),
        ),
        patch(
            "vpn_bot.admin_wg_service.delete_wg_interface_on_router_timed",
            new=AsyncMock(return_value=(True, None)),
        ),
        patch(
            "vpn_bot.admin_panel.delete_wg_interface",
            new=AsyncMock(return_value=(True, "Deleted")),
        ),
        patch(
            "vpn_bot.admin_panel._render_wg_interfaces_list",
            new=AsyncMock(),
        ) as mock_list,
    ):
        result = await wg_delete_confirm_exec(update, context)

    assert result == ConversationHandler.END
    mock_list.assert_awaited_once()
    _args, kwargs = mock_list.await_args
    assert kwargs.get("answer_callback") is False
    assert kwargs.get("skip_router_sync") is True
    assert context.user_data.get("edit_wg_interface_id") is None
    assert "wg_list_notice" not in context.user_data
