"""Route List wizard: table, dst-address, gateway, distance."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import uuid

import pytest

from telegram.error import BadRequest

from vpn_bot.admin_panel import (
    WG_ADD_INT_MAN_ROUTE_DST,
    WG_INT_ROUTE_DIST,
    WG_INT_ROUTE_DST,
    WG_INT_ROUTE_GW,
    WG_INT_ROUTE_TABLE,
    WG_INT_SETTINGS,
    _wg_edit_reply,
    _wg_format_nat_dst_display,
    _wg_format_route_list_display,
    _wg_route_dst_choice_keyboard,
    _wg_route_dst_presets,
    _wg_validate_route_dst,
    get_wg_route_dist,
    get_wg_route_dst,
    get_wg_route_gw,
    get_wg_route_table,
    set_wg_route_list_start,
    set_wg_route_step_dst,
    _wg_parse_route_table_choice,
)
from vpn_bot.utils import LanguageManager
from vpn_bot.admin_wg_service import (
    apply_wg_automation,
    apply_wg_automation_timed,
    update_wg_interface,
    validate_wg_route_before_apply,
)
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import WireGuardInterface
from vpn_bot.mikrotik_manager import MikroTikManager
from tests.helpers.mock_mikrotik import MockMikrotikManager

pytestmark = pytest.mark.db


def test_route_dst_presets_include_default():
    presets = _wg_route_dst_presets()
    vals = [p["val"] for p in presets]
    assert "0.0.0.0/0" in vals
    assert "::/0" in vals
    assert "10.0.0.0/8" in vals
    assert "__custom__" in vals


def test_route_dst_keyboard_has_default_button():
    markup = _wg_route_dst_choice_keyboard(callback_prefix="set_wg_rdst", back_callback="back")
    cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "set_wg_rdst_p0" in cbs
    assert "set_wg_rdst_custom" in cbs


def test_validate_route_dst_accepts_cidr():
    assert _wg_validate_route_dst("0.0.0.0/0") == "0.0.0.0/0"
    assert _wg_validate_route_dst("10.0.0.0/8") == "10.0.0.0/8"
    assert _wg_validate_route_dst("::/0") == "::/0"
    assert _wg_validate_route_dst("not-a-network") is None


def test_format_route_list_display():
    iface = MagicMock(
        route_table="wgmark",
        routing_mark="manglemark",
        route_dst_address="0.0.0.0/0",
        gateway="192.168.1.1",
        route_distance=10,
    )
    text = _wg_format_route_list_display(iface)
    assert "wgmark" in text
    assert "0.0.0.0/0" in text
    assert "192.168.1.1" in text
    assert "10" in text
    assert "`" not in text


def test_route_list_display_no_nested_backticks():
    iface = MagicMock(
        route_table="wg_mark",
        routing_mark="mangle_mark",
        route_dst_address="0.0.0.0/0",
        gateway="10.0.0.1",
        route_distance=1,
    )
    summary = _wg_format_route_list_display(iface)
    assert "`" not in summary

    route_section = LanguageManager.get(
        "admin.wg.section_route_values",
        route_list=summary,
    )
    assert "`" not in route_section


def test_firewall_section_text_parseable():
    iface = MagicMock(
        name="wg_test_iface",
        upstream_interface="ether1_wan",
        routing_mark="rm_main",
        nat_routing_mark="rm_nat",
        nat_dst_address="127.0.0.1",
        nat_dst_address_list=None,
        nat_dst_negate=True,
        route_table="wg_mark",
        route_dst_address="0.0.0.0/0",
        gateway="10.0.0.1",
        route_distance=1,
    )
    route_list = _wg_format_route_list_display(iface)
    nat_dst = _wg_format_nat_dst_display(iface)

    firewall_values = LanguageManager.get(
        "admin.wg.section_firewall_values",
        upstream=iface.upstream_interface,
        routing_mark=iface.routing_mark,
        nat_routing_mark=iface.nat_routing_mark,
        nat_dst=nat_dst,
        route_list=route_list,
    )
    assert "!" in firewall_values
    assert "0.0.0.0/0" in firewall_values
    assert "ether1_wan" in firewall_values
    assert "`" not in route_list


@pytest.mark.asyncio
async def test_wg_edit_reply_fallback_on_bad_markdown():
    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.edit_message_text = AsyncMock(
        side_effect=[
            BadRequest("Can't parse entities: can't find end of the entity"),
            None,
        ]
    )
    update.message = None
    markup = MagicMock()

    await _wg_edit_reply(update, "test *markdown* text", markup)

    assert update.callback_query.edit_message_text.await_count == 2
    first_kwargs = update.callback_query.edit_message_text.await_args_list[0].kwargs
    second_kwargs = update.callback_query.edit_message_text.await_args_list[1].kwargs
    assert first_kwargs.get("parse_mode") == "Markdown"
    assert "parse_mode" not in second_kwargs


def test_format_route_list_falls_back_to_routing_mark():
    iface = MagicMock(
        route_table=None,
        routing_mark="main",
        route_dst_address=None,
        gateway=None,
        route_distance=1,
    )
    text = _wg_format_route_list_display(iface)
    assert "main" in text


def test_sync_route_uses_route_fields():
    captured = {}

    mgr = MikroTikManager(host="1.2.3.4", username="u", password="p", use_pool=False)
    mgr.connect = MagicMock()
    mgr.close = MagicMock()

    class FakeApi:
        def get(self, **kwargs):
            return []

        def set(self, **kwargs):
            captured.update(kwargs)

        def add(self, **kwargs):
            captured.update(kwargs)

    mgr.api = MagicMock()
    mgr.api.get_resource = MagicMock(
        side_effect=lambda path: FakeApi()
    )

    ok, route_applied = mgr._sync_wg_interface_automation_impl(
        "wg_test",
        "10.0.0.1/24",
        routing_mark="mangle_only",
        gateway="10.0.0.254",
        route_table="custom_table",
        route_dst="192.0.2.0/24",
        route_distance=5,
    )

    assert ok is True
    assert route_applied is True
    assert captured.get("dst-address") == "192.0.2.0/24"
    assert captured.get("gateway") == "10.0.0.254"
    assert captured.get("routing-table") == "custom_table"
    assert captured.get("distance") == "5"


def test_sync_route_table_fallback_to_routing_mark():
    captured = {}

    mgr = MikroTikManager(host="1.2.3.4", username="u", password="p", use_pool=False)
    mgr.connect = MagicMock()
    mgr.close = MagicMock()

    class FakeApi:
        def get(self, **kwargs):
            return []

        def add(self, **kwargs):
            captured.update(kwargs)

    mgr.api = MagicMock()
    mgr.api.get_resource = MagicMock(side_effect=lambda path: FakeApi())

    ok, route_applied = mgr._sync_wg_interface_automation_impl(
        "wg_test",
        "10.0.0.1/24",
        routing_mark="rm_main",
        gateway="1.1.1.1",
        route_table=None,
    )

    assert ok is True
    assert route_applied is True
    assert captured.get("routing-table") == "rm_main"
    assert captured.get("dst-address") == "0.0.0.0/0"
    assert captured.get("distance") == "1"


@pytest.mark.asyncio
async def test_set_wg_route_list_start_loads_tables():
    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.message = None
    context = MagicMock()
    context.user_data = {"edit_wg_interface_id": 1}

    fake_iface = MagicMock(server_id=2, route_table="main")
    fake_server = MagicMock(id=2)

    with (
        patch("vpn_bot.admin_panel.get_wg_interface_details", new=AsyncMock(return_value=(fake_iface, 0))),
        patch("vpn_bot.admin_panel.get_server_by_id", new=AsyncMock(return_value=fake_server)),
        patch(
            "vpn_bot.admin_panel.fetch_routing_tables_timed",
            new=AsyncMock(return_value=(["wg_mark_test", "main"], None)),
        ),
    ):
        state = await set_wg_route_list_start(update, context)

    assert state is not None
    kwargs = update.callback_query.edit_message_text.await_args.kwargs
    cbs = [b.callback_data for row in kwargs["reply_markup"].inline_keyboard for b in row]
    assert "set_wg_rt_p0" in cbs
    assert "set_wg_rt_p1" in cbs


@pytest.mark.asyncio
async def test_add_wizard_route_table_after_nat():
    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.data = "man_wg_rt_p0"
    update.callback_query.edit_message_text = AsyncMock()
    update.message = None
    context = MagicMock()
    context.user_data = {
        "new_wg_iface_data": {"server_id": 1},
        "wg_route_table_choices": ["main"],
    }

    with (
        patch("vpn_bot.admin_panel.get_server_by_id", new=AsyncMock(return_value=MagicMock(id=1))),
        patch(
            "vpn_bot.admin_panel.fetch_routing_tables_timed",
            new=AsyncMock(return_value=(["main"], None)),
        ),
    ):
        state = await get_wg_route_table(update, context)

    assert state == WG_ADD_INT_MAN_ROUTE_DST
    data = context.user_data["new_wg_iface_data"]
    assert data.get("route_table") == "main"


@pytest.mark.asyncio
async def test_update_wg_interface_persists_route_fields():
    from vpn_bot.admin_wg_service import update_wg_interface

    fake_iface = MagicMock()
    fake_iface.route_table = None
    fake_iface.route_dst_address = None
    fake_iface.route_distance = 1

    mock_session = MagicMock()
    mock_session.get = AsyncMock(side_effect=lambda model, iid: fake_iface if iid == 1 else MagicMock())
    mock_session.commit = AsyncMock()

    with patch("vpn_bot.admin_wg_service.AsyncSessionLocal") as mock_ctx:
        mock_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
        mock_ctx.return_value.__aexit__ = AsyncMock(return_value=None)

        ok, err = await update_wg_interface(
            1,
            {
                "route_table": "rt1",
                "route_dst_address": "0.0.0.0/0",
                "route_distance": 10,
                "gateway": "10.0.0.1",
            },
        )

    assert ok is True
    assert fake_iface.route_table == "rt1"
    assert fake_iface.route_dst_address == "0.0.0.0/0"
    assert fake_iface.route_distance == 10
    assert fake_iface.gateway == "10.0.0.1"


@pytest.mark.asyncio
async def test_route_dst_keyboard_has_all_presets():
    markup = _wg_route_dst_choice_keyboard(callback_prefix="set_wg_rdst", back_callback="back")
    cbs = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "set_wg_rdst_p0" in cbs
    assert "set_wg_rdst_p1" in cbs
    assert "set_wg_rdst_p2" in cbs
    assert "set_wg_rdst_custom" in cbs


@pytest.mark.asyncio
async def test_single_step_dst_applies_without_full_wizard():
    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.data = "set_wg_rdst_p0"
    update.callback_query.edit_message_text = AsyncMock()
    update.message = None
    context = MagicMock()
    context.user_data = {"edit_wg_interface_id": 5, "wg_route_edit_mode": "dst"}

    with (
        patch("vpn_bot.admin_panel.update_wg_interface", new=AsyncMock(return_value=(True, None))),
        patch(
            "vpn_bot.admin_panel._wg_apply_firewall_and_return",
            new=AsyncMock(return_value=WG_INT_SETTINGS),
        ) as mock_apply,
    ):
        state = await get_wg_route_dst(update, context)

    assert state == WG_INT_SETTINGS
    mock_apply.assert_awaited_once()
    assert context.user_data.get("wg_route_edit_mode") is None


@pytest.mark.asyncio
async def test_full_route_wizard_persists_and_syncs_to_mikrotik(mock_server, mock_mikrotik):
    """Full Route List wizard: DB fields + apply_wg_automation sends route to manager."""
    async with AsyncSessionLocal() as session:
        iface = WireGuardInterface(
            server_id=mock_server.id,
            name=f"wg_route_{uuid.uuid4().hex[:6]}",
            public_key="pub",
            private_key="priv",
            listen_port=51900,
            address="10.99.0.1/24",
            routing_mark="mangle_rm",
            upstream_interface="ether1",
            max_users=10,
            is_active=True,
        )
        session.add(iface)
        await session.commit()
        await session.refresh(iface)
        iface_id = iface.id

    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.automation_calls.clear()

    fake_server = type("Srv", (), {"id": mock_server.id})()
    fake_iface = type(
        "Iface",
        (),
        {"server_id": mock_server.id, "route_table": None},
    )()

    # Step 1: wizard start → routing table list
    u1 = MagicMock()
    u1.callback_query = MagicMock()
    u1.callback_query.answer = AsyncMock()
    u1.callback_query.edit_message_text = AsyncMock()
    u1.message = None
    ctx = MagicMock()
    ctx.user_data = {"edit_wg_interface_id": iface_id}

    with (
        patch("vpn_bot.admin_panel.get_wg_interface_details", new=AsyncMock(return_value=(fake_iface, 0))),
        patch("vpn_bot.admin_panel.get_server_by_id", new=AsyncMock(return_value=fake_server)),
        patch(
            "vpn_bot.admin_panel.fetch_routing_tables_timed",
            new=AsyncMock(return_value=(["main", "wg_rt_test"], None)),
        ),
    ):
        state = await set_wg_route_list_start(u1, ctx)
    assert state == WG_INT_ROUTE_TABLE
    assert ctx.user_data.get("wg_route_edit_mode") == "full"

    # Step 2: pick routing table
    u2 = MagicMock()
    u2.callback_query = MagicMock()
    u2.callback_query.answer = AsyncMock()
    u2.callback_query.data = "set_wg_rt_wg_rt_test"
    u2.callback_query.edit_message_text = AsyncMock()
    u2.message = None

    with patch("vpn_bot.admin_panel.update_wg_interface", new=AsyncMock(return_value=(True, None))):
        state = await get_wg_route_table(u2, ctx)
    assert state == WG_INT_ROUTE_DST

    # Step 3: pick dst preset 0 (0.0.0.0/0)
    u3 = MagicMock()
    u3.callback_query = MagicMock()
    u3.callback_query.answer = AsyncMock()
    u3.callback_query.data = "set_wg_rdst_p0"
    u3.callback_query.edit_message_text = AsyncMock()
    u3.message = None

    with patch("vpn_bot.admin_panel.update_wg_interface", new=AsyncMock(return_value=(True, None))):
        state = await get_wg_route_dst(u3, ctx)
    assert state == WG_INT_ROUTE_GW

    # Step 4: gateway (text)
    u4 = MagicMock()
    u4.callback_query = None
    u4.message = MagicMock()
    u4.message.text = "192.168.88.1"
    u4.message.reply_text = AsyncMock()

    with patch("vpn_bot.admin_panel.update_wg_interface", new=AsyncMock(return_value=(True, None))):
        state = await get_wg_route_gw(u4, ctx)
    assert state == WG_INT_ROUTE_DIST

    # Step 5: distance → MikroTik apply
    u5 = MagicMock()
    u5.callback_query = MagicMock()
    u5.callback_query.answer = AsyncMock()
    u5.callback_query.data = "set_wg_rdist_10"
    u5.callback_query.edit_message_text = AsyncMock()
    u5.message = None

    async def _fake_apply(update, context, iid):
        assert iid == iface_id
        return WG_INT_SETTINGS

    with (
        patch("vpn_bot.admin_panel.update_wg_interface", new=AsyncMock(return_value=(True, None))),
        patch("vpn_bot.admin_panel._wg_apply_firewall_and_return", side_effect=_fake_apply) as mock_apply,
    ):
        state = await get_wg_route_dist(u5, ctx)

    assert state == WG_INT_SETTINGS
    mock_apply.assert_awaited_once()

    # Persist route fields in DB (real update_wg_interface)
    ok, err = await update_wg_interface(
        iface_id,
        {
            "route_table": "wg_rt_test",
            "route_dst_address": "0.0.0.0/0",
            "gateway": "192.168.88.1",
            "route_distance": 10,
        },
    )
    assert ok and not err

    ok, route_applied = await apply_wg_automation(iface_id)
    assert ok is True
    assert route_applied is True
    last = mgr.last_automation()
    assert last is not None
    assert last["route_table"] == "wg_rt_test"
    assert last["route_dst"] == "0.0.0.0/0"
    assert last["gateway"] == "192.168.88.1"
    assert last["route_distance"] == 10


@pytest.mark.asyncio
async def test_full_wizard_skip_gateway_does_not_push_route(mock_server, mock_mikrotik):
    """MikroTik route rule requires gateway + routing-table; skip gateway = no /ip/route upsert."""
    async with AsyncSessionLocal() as session:
        iface = WireGuardInterface(
            server_id=mock_server.id,
            name=f"wg_nogw_{uuid.uuid4().hex[:6]}",
            public_key="pub",
            private_key="priv",
            listen_port=51901,
            address="10.99.1.1/24",
            routing_mark="rm_only",
            route_table="main",
            route_dst_address="0.0.0.0/0",
            route_distance=1,
            gateway=None,
            max_users=5,
            is_active=True,
        )
        session.add(iface)
        await session.commit()
        await session.refresh(iface)

    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.automation_calls.clear()
    ok, route_applied = await apply_wg_automation(iface.id)
    assert ok is True
    assert route_applied is False
    last = mgr.last_automation()
    assert last["gateway"] is None
    assert last["route_table"] == "main"


@pytest.mark.asyncio
async def test_validate_route_requires_gateway_when_table_set():
    iface = MagicMock(
        gateway=None,
        route_table="main",
        routing_mark=None,
        route_dst_address="0.0.0.0/0",
        route_distance=1,
    )
    assert validate_wg_route_before_apply(iface) == "admin.wg.route_requires_gateway"


@pytest.mark.asyncio
async def test_apply_timed_rejects_missing_gateway(mock_server, mock_mikrotik):
    async with AsyncSessionLocal() as session:
        iface = WireGuardInterface(
            server_id=mock_server.id,
            name=f"wg_val_{uuid.uuid4().hex[:6]}",
            public_key="pub",
            private_key="priv",
            listen_port=51902,
            address="10.99.2.1/24",
            route_table="main",
            gateway=None,
            max_users=5,
            is_active=True,
        )
        session.add(iface)
        await session.commit()
        await session.refresh(iface)

    ok, err, route_applied = await apply_wg_automation_timed(iface.id)
    assert ok is False
    assert route_applied is False
    assert err is not None


@pytest.mark.asyncio
async def test_parse_route_table_index_callback():
    ctx = MagicMock()
    ctx.user_data = {"wg_route_table_choices": ["main", "wg_custom"]}
    assert _wg_parse_route_table_choice(ctx, "set_wg_rt_p1", is_man=False) == "wg_custom"
    assert _wg_parse_route_table_choice(ctx, "set_wg_rt_none", is_man=False) is None


@pytest.mark.asyncio
async def test_set_wg_route_step_dst_opens_dst_prompt():
    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.message = None
    context = MagicMock()
    context.user_data = {"edit_wg_interface_id": 1}

    from vpn_bot.admin_panel import WG_INT_ROUTE_DST

    state = await set_wg_route_step_dst(update, context)
    assert state == WG_INT_ROUTE_DST
    assert context.user_data.get("wg_route_edit_mode") == "dst"
    update.callback_query.answer.assert_awaited_once()


def _cb_patterns(state_handlers) -> list[str]:
    from telegram.ext import CallbackQueryHandler

    patterns = []
    for h in state_handlers:
        if isinstance(h, CallbackQueryHandler) and h.pattern is not None:
            patterns.append(h.pattern.pattern)
    return patterns


def test_wg_conversation_route_states_no_collision():
    from vpn_bot.admin_panel import (
        admin_wg_mgmt_handler,
        WG_ADD_INT_MAN_ROUTE_DST,
        WG_ADD_INT_MAN_ROUTE_DIST,
        WG_ADD_INT_MAN_ROUTE_GW,
        WG_ADD_INT_MAN_ROUTE_TABLE,
    )

    states = admin_wg_mgmt_handler.states
    edit_states = {WG_INT_ROUTE_TABLE, WG_INT_ROUTE_DST, WG_INT_ROUTE_GW, WG_INT_ROUTE_DIST}
    add_states = {
        WG_ADD_INT_MAN_ROUTE_TABLE,
        WG_ADD_INT_MAN_ROUTE_DST,
        WG_ADD_INT_MAN_ROUTE_GW,
        WG_ADD_INT_MAN_ROUTE_DIST,
    }
    assert edit_states.isdisjoint(add_states)

    rt_patterns = _cb_patterns(states[WG_INT_ROUTE_TABLE])
    assert any("set_wg_rt_" in p for p in rt_patterns)
    assert not any("man_wg_rdst_" in p for p in rt_patterns)

    dst_patterns = _cb_patterns(states[WG_INT_ROUTE_DST])
    assert any("set_wg_rdst_" in p for p in dst_patterns)
    assert not any("man_wg_rgw_" in p for p in dst_patterns)

    gw_patterns = _cb_patterns(states[WG_INT_ROUTE_GW])
    assert any("set_wg_rgw_" in p for p in gw_patterns)
    assert not any("man_wg_rdist_" in p for p in gw_patterns)

    add_dst_patterns = _cb_patterns(states[WG_ADD_INT_MAN_ROUTE_DST])
    assert any("man_wg_rdst_" in p for p in add_dst_patterns)


def test_wg_route_conversation_handler_routing():
    from telegram.ext import CallbackQueryHandler

    from vpn_bot.admin_panel import admin_wg_mgmt_handler, get_wg_route_table

    handlers = admin_wg_mgmt_handler.states[WG_INT_ROUTE_TABLE]
    route_handlers = [
        h
        for h in handlers
        if isinstance(h, CallbackQueryHandler) and h.callback is get_wg_route_table
    ]
    assert len(route_handlers) == 1
    pattern = route_handlers[0].pattern
    assert pattern.match("set_wg_rt_p0")
    assert pattern.match("set_wg_rt_none")
    assert not pattern.match("man_wg_rt_p0")
