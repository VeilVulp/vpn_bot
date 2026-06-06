"""Verify UM profile exists on router after create_profile_full."""

import asyncio
import time

import pytest

from vpn_bot.admin_profile_service import create_profile_full, delete_profile_full
from vpn_bot.mikrotik_manager import get_mikrotik_manager

pytestmark = pytest.mark.live_mt


@pytest.mark.asyncio
async def test_profile_exists_on_mikrotik_after_create(live_server):
    name = f"INTG_SYNC_PROF_{int(time.time()) % 100000}"
    prof, err = await create_profile_full(
        {
            "server_id": live_server.id,
            "name": name,
            "days": 7,
            "limit": 2,
            "price_toman": 1000,
        }
    )
    assert prof, err

    mgr = get_mikrotik_manager(live_server)
    await asyncio.to_thread(mgr.connect)
    prof_api = mgr._get_resource("/user-manager/profile")
    on_mt = await asyncio.to_thread(prof_api.get, name=name)
    await asyncio.to_thread(mgr.close)
    assert on_mt, f"Profile {name} missing on MikroTik"

    await delete_profile_full(prof.id)
