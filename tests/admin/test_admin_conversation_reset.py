"""Regression tests for admin conversation exit and server list filtering."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ConversationHandler

from vpn_bot.admin_conversation import clear_admin_flow_context, admin_exit_to_menu
from vpn_bot.admin_server_service import (
    get_server_health_status,
    get_servers_for_admin_list,
    is_mock_or_test_server,
)
from vpn_bot.models import Server


def _server(**kwargs) -> Server:
    s = Server()
    for k, v in kwargs.items():
        setattr(s, k, v)
    return s


def test_is_mock_or_test_server_dbtest():
    assert is_mock_or_test_server(_server(name="[DBTEST] Mock", host="10.0.0.1", username="admin"))


def test_is_mock_or_test_server_localhost():
    assert is_mock_or_test_server(_server(name="Prod", host="127.0.0.1", username="admin"))


def test_is_mock_or_test_server_production():
    assert not is_mock_or_test_server(_server(name="Live MikroTik", host="192.168.1.1", username="admin"))


def test_is_mock_or_test_server_real_ip_kept_even_if_test_name():
    assert not is_mock_or_test_server(
        _server(name="[TEST] Live MikroTik", host="81.30.108.27", username="admin")
    )


def test_is_mock_or_test_server_real_list_placeholder():
    assert is_mock_or_test_server(
        _server(name="[TEST] Real List abc", host="192.0.2.10", username="admin")
    )


def test_clear_admin_flow_context_removes_keys():
    ctx = MagicMock()
    ctx.user_data = {
        "target_user": "x",
        "admin_target_user_id": 1,
        "other": "keep",
    }
    clear_admin_flow_context(ctx)
    assert "target_user" not in ctx.user_data
    assert "admin_target_user_id" not in ctx.user_data
    assert ctx.user_data["other"] == "keep"


@pytest.mark.asyncio
async def test_admin_exit_to_menu_clears_and_returns_end():
    update = MagicMock()
    update.effective_message = None
    update.callback_query = None
    update.effective_chat = MagicMock()
    update.effective_chat.id = 12345
    update.effective_chat.type = "private"
    context = MagicMock()
    context.user_data = {"target_user": "u1"}

    with (
        patch(
            "vpn_bot.admin_permissions.require_admin_message",
            new_callable=AsyncMock,
            return_value=True,
        ),
        patch(
            "vpn_bot.admin_permissions.resolve_group_admin_scope",
            new_callable=AsyncMock,
            return_value="private",
        ),
        patch("vpn_bot.admin_panel.admin_start", new_callable=AsyncMock) as mock_start,
    ):
        result = await admin_exit_to_menu(update, context)

    assert result == ConversationHandler.END
    assert "target_user" not in context.user_data
    mock_start.assert_awaited_once_with(update, context)


@pytest.mark.asyncio
async def test_get_server_health_status_skips_mock_connect():
    mock_srv = _server(name="[DBTEST] Mock", host="127.0.0.1", username="mock", id=99)
    with patch("vpn_bot.admin_server_service.get_mikrotik_manager") as mgr:
        ok = await get_server_health_status(mock_srv)
    assert ok is False
    mgr.assert_not_called()


@pytest.mark.asyncio
async def test_get_servers_for_admin_list_filters_dbtest(db_initialized):
    import uuid

    from vpn_bot.database import AsyncSessionLocal

    suffix = uuid.uuid4().hex[:8]
    async with AsyncSessionLocal() as session:
        mock = Server(
            name=f"[DBTEST] Mock List {suffix}",
            host="127.0.0.1",
            port=8728,
            username="mock",
            password="mock",
            is_active=True,
        )
        placeholder = Server(
            name=f"[TEST] Real List {suffix}",
            host="192.0.2.10",
            port=8728,
            username="admin",
            password="secret",
            is_active=True,
        )
        prod = Server(
            name=f"Router Main {suffix}",
            host="81.30.108.27",
            port=8728,
            username="admin",
            password="secret",
            is_active=True,
        )
        session.add(mock)
        session.add(placeholder)
        session.add(prod)
        await session.commit()

    listed = await get_servers_for_admin_list()
    names = {s.name for s in listed}
    assert f"Router Main {suffix}" in names
    assert f"[TEST] Real List {suffix}" not in names
    assert f"[DBTEST] Mock List {suffix}" not in names


def test_build_admin_fallbacks_includes_admin_command():
    from vpn_bot.admin_conversation import build_admin_fallback_handlers
    from telegram.ext import CommandHandler

    handlers = build_admin_fallback_handlers(MagicMock())
    admin_cmds = [h for h in handlers if isinstance(h, CommandHandler) and "admin" in (h.commands or ())]
    cancel_cmds = [h for h in handlers if isinstance(h, CommandHandler) and "cancel" in (h.commands or ())]
    assert admin_cmds
    assert cancel_cmds
