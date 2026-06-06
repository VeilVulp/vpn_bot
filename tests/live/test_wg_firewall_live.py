"""Live MikroTik: verify bot-managed WG NAT/Mangle rules on router."""

from __future__ import annotations

import asyncio

import pytest

pytestmark = pytest.mark.live_mt


def _rules_with_comment(mgr, resource: str, comment: str) -> list[dict]:
    mgr.connect()
    try:
        api = mgr.api.get_resource(resource)
        return api.get(comment=comment) or []
    finally:
        mgr.close()


@pytest.mark.asyncio
async def test_sync_wg_firewall_rules_on_router(live_server):
    from vpn_bot.mikrotik_manager import get_mikrotik_manager

    mgr = get_mikrotik_manager(live_server)
    params = await asyncio.to_thread(mgr.find_available_wg_interface_params)
    if not params:
        pytest.skip("No free WG interface params on test router")

    upstream_list = await asyncio.to_thread(mgr.get_upstream_interfaces)
    upstream = upstream_list[0]["name"] if upstream_list else None
    if not upstream:
        pytest.skip("No upstream interfaces on test router")

    name = params["name"]
    port = params["listen_port"]
    address = params["address"]
    routing_mark = f"bot_wg_test_{name.replace('-', '_')[:20]}"
    nat_routing_mark = f"nat_{routing_mark}"[:31]

    keys = await asyncio.to_thread(
        mgr.create_wg_interface, name, port, address
    )
    if not keys:
        pytest.skip("Could not create temporary WG interface on router")

    try:
        ok = await asyncio.to_thread(
            mgr.sync_wg_interface_automation,
            name=name,
            address=address,
            upstream=upstream,
            routing_mark=routing_mark,
            nat_routing_mark=nat_routing_mark,
            nat_dst="127.0.0.1",
            gateway=None,
            listen_port=port,
        )
        assert ok is True

        nat_comment = f"managed-by-bot-wg-nat-{name}"
        mangle_comment = f"managed-by-bot-wg-mangle-{name}"

        nat_rules = await asyncio.to_thread(
            _rules_with_comment, mgr, "/ip/firewall/nat", nat_comment
        )
        mangle_rules = await asyncio.to_thread(
            _rules_with_comment, mgr, "/ip/firewall/mangle", mangle_comment
        )

        assert len(nat_rules) >= 1
        nat = nat_rules[0]
        assert nat.get("chain") == "srcnat"
        assert nat.get("action") == "masquerade"
        assert nat.get("out-interface") == upstream
        assert nat.get("routing-mark") == nat_routing_mark

        assert len(mangle_rules) >= 1
        mangle = mangle_rules[0]
        assert mangle.get("chain") == "prerouting"
        assert mangle.get("action") == "mark-routing"
        assert mangle.get("new-routing-mark") == routing_mark
        assert mangle.get("passthrough") in ("no", False, "false")
    finally:
        await asyncio.to_thread(mgr.cleanup_wg_interface_automation, name)
        await asyncio.to_thread(mgr.delete_wg_interface, name)


@pytest.mark.asyncio
async def test_route_list_wizard_creates_route_on_router(live_server):
    """Route List fields: /ip/route with dst, gateway, routing-table, distance."""
    from vpn_bot.mikrotik_manager import get_mikrotik_manager

    mgr = get_mikrotik_manager(live_server)
    params = await asyncio.to_thread(mgr.find_available_wg_interface_params)
    if not params:
        pytest.skip("No free WG interface params on test router")

    upstream_list = await asyncio.to_thread(mgr.get_upstream_interfaces)
    upstream = upstream_list[0]["name"] if upstream_list else None
    if not upstream:
        pytest.skip("No upstream interfaces on test router")

    tables = await asyncio.to_thread(mgr.get_routing_tables)
    if not tables:
        pytest.skip("Cannot read routing tables from router")

    route_table = "main" if "main" in tables else tables[0]
    name = params["name"]
    port = params["listen_port"]
    address = params["address"]
    routing_mark = f"bot_rt_{name.replace('-', '_')[:16]}"
    gateway = "192.0.2.1"
    route_dst = "0.0.0.0/0"
    distance = 7

    keys = await asyncio.to_thread(mgr.create_wg_interface, name, port, address)
    if not keys:
        pytest.skip("Could not create temporary WG interface on router")

    try:
        ok = await asyncio.to_thread(
            mgr.sync_wg_interface_automation,
            name=name,
            address=address,
            upstream=upstream,
            routing_mark=routing_mark,
            nat_dst="127.0.0.1",
            gateway=gateway,
            route_table=route_table,
            route_dst=route_dst,
            route_distance=distance,
            listen_port=port,
        )
        assert ok is True

        route_comment = f"managed-by-bot-wg-route-{name}"
        routes = await asyncio.to_thread(
            _rules_with_comment, mgr, "/ip/route", route_comment
        )
        assert len(routes) >= 1, "Expected bot-managed /ip/route entry on router"
        route = routes[0]
        assert route.get("dst-address") == route_dst
        assert route.get("gateway") == gateway
        assert route.get("routing-table") == route_table
        assert route.get("distance") == str(distance)
    finally:
        await asyncio.to_thread(mgr.cleanup_wg_interface_automation, name)
        await asyncio.to_thread(mgr.delete_wg_interface, name)
