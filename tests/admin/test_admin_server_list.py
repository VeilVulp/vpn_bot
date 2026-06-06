"""Admin server list: empty state buttons, keyboard, inactive servers."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vpn_bot.admin_server_service import (
    build_server_list_keyboard,
    get_servers_for_admin_list,
)
from vpn_bot.admin_permissions import (
    callback_allowed_for_permissions,
    permission_for_callback,
)
from vpn_bot.models import Server


def _callback_rows(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def test_build_server_list_keyboard_always_has_add_and_back():
    markup = build_server_list_keyboard([])
    cbs = _callback_rows(markup)
    assert "server_add" in cbs
    assert "admin_start" in cbs
    assert not any(cb.startswith("server_edit_") for cb in cbs)


def test_server_edit_menu_has_delete_button():
    from vpn_bot.admin_panel import _server_edit_menu_markup

    markup = _server_edit_menu_markup(7, "Main")
    cbs = _callback_rows(markup)
    assert "server_delete_7" in cbs
    from vpn_bot.utils import LanguageManager

    delete_btn = markup.inline_keyboard[-2][0]
    assert delete_btn.callback_data == "server_delete_7"
    assert delete_btn.text == LanguageManager.get("admin.server.btn_delete")


def test_server_delete_handler_in_edit_select_state():
    from vpn_bot.admin_panel import SERVER_EDIT_SELECT, admin_server_handler
    from telegram.ext import CallbackQueryHandler

    handlers = admin_server_handler.states[SERVER_EDIT_SELECT]
    delete_handlers = [
        h
        for h in handlers
        if isinstance(h, CallbackQueryHandler) and getattr(h, "callback", None)
    ]
    names = {getattr(h.callback, "__name__", "") for h in delete_handlers}
    assert "server_delete_start" in names


def test_build_server_list_keyboard_manage_rows():
    srv = MagicMock()
    srv.id = 42
    srv.name = "Main"
    markup = build_server_list_keyboard([srv])
    cbs = _callback_rows(markup)
    assert "server_edit_42" in cbs
    assert "server_add" in cbs


def test_server_add_permission_mapping():
    assert permission_for_callback("server_add") == "servers"
    assert callback_allowed_for_permissions("server_add", granted=["servers"], is_super=False)
    assert not callback_allowed_for_permissions("server_add", granted=["receipts"], is_super=False)


@pytest.mark.asyncio
async def test_list_servers_empty_shows_add_button():
    from vpn_bot.admin_panel import list_servers

    query = MagicMock()
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.edit_text = AsyncMock()
    query.message.reply_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.message = None
    update.effective_user = None
    context = MagicMock()
    context.user_data = {}

    with patch(
        "vpn_bot.admin_panel.get_servers_for_admin_list",
        new=AsyncMock(return_value=[]),
    ):
        await list_servers(update, context)

    call = query.message.edit_text.await_args
    assert call is not None
    markup = call.kwargs.get("reply_markup")
    cbs = _callback_rows(markup)
    assert "server_add" in cbs
    assert "admin_start" in cbs


@pytest.mark.asyncio
async def test_list_servers_with_servers_shows_add_button():
    from vpn_bot.admin_panel import list_servers

    srv = MagicMock()
    srv.id = 1
    srv.name = "Router"
    srv.is_active = True
    srv.host = "10.0.0.1"
    srv.port = 8728

    query = MagicMock()
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.edit_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.message = None
    update.effective_user = None
    context = MagicMock()
    context.user_data = {}

    with (
        patch(
            "vpn_bot.admin_panel.get_servers_for_admin_list",
            new=AsyncMock(return_value=[srv]),
        ),
        patch(
            "vpn_bot.admin_panel.get_multi_server_health",
            new=AsyncMock(return_value={1: True}),
        ),
    ):
        await list_servers(update, context)

    call = query.message.edit_text.await_args
    markup = call.kwargs.get("reply_markup")
    cbs = _callback_rows(markup)
    assert "server_add" in cbs
    assert "server_edit_1" in cbs


@pytest.mark.asyncio
async def test_get_servers_for_admin_list_includes_inactive(db_initialized):
    from vpn_bot.database import AsyncSessionLocal

    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as session:
        session.add(
            Server(
                name=f"Inactive Router {suffix}",
                host="10.20.30.40",
                port=8728,
                username="admin",
                password="secret",
                is_active=False,
            )
        )
        await session.commit()

    listed = await get_servers_for_admin_list()
    assert any(s.name == f"Inactive Router {suffix}" for s in listed)


@pytest.mark.asyncio
async def test_delete_server_blocks_when_profile_linked(mock_server, db_ovpn_profile):
    from vpn_bot.admin_server_service import delete_server

    ok, code = await delete_server(mock_server.id)
    assert ok is False
    assert code == "blocked_dependencies"


@pytest.mark.asyncio
async def test_delete_server_success_when_no_dependencies(db_initialized):
    import uuid

    from vpn_bot.admin_server_service import create_server, delete_server, get_server_by_id

    suffix = uuid.uuid4().hex[:8]
    srv = await create_server(
        {
            "name": f"DelOk {suffix}",
            "host": "10.30.40.51",
            "username": "admin",
            "password": "x",
            "port": 8728,
        }
    )
    ok, name = await delete_server(srv.id)
    assert ok is True
    assert name == f"DelOk {suffix}"
    assert await get_server_by_id(srv.id) is None
