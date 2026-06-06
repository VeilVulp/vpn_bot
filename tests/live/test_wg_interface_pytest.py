"""Pytest entry for WG interface logic (wraps legacy script checks)."""

import asyncio

import pytest

pytestmark = pytest.mark.live_mt


@pytest.mark.asyncio
async def test_wg_interface_params_and_upstream(live_server):
    from vpn_bot.mikrotik_manager import get_mikrotik_manager

    mgr = get_mikrotik_manager(live_server)
    await asyncio.to_thread(mgr.connect)

    params = await asyncio.to_thread(mgr.find_available_wg_interface_params)
    assert params is None or "name" in params

    upstream = await asyncio.to_thread(mgr.get_upstream_interfaces)
    assert isinstance(upstream, list)

    await asyncio.to_thread(mgr.close)
