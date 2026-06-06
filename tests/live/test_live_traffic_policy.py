"""
Live MikroTik checks for traffic policy (optional — requires .env.test + router).

Run:
  pytest tests/live/test_live_traffic_policy.py -m live_mt -v
"""

import asyncio
import time
import uuid

import pytest
from sqlalchemy import select

from vpn_bot.admin_subscription_service import extend_subscription_validity
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.models import Subscription
from vpn_bot.sync_manager import SyncManager
from vpn_bot.user_features import checkout_subscription

pytestmark = pytest.mark.live_mt


@pytest.mark.asyncio
async def test_live_ovpn_purchase_has_um_user_and_limit(intg_user, live_server):
    """Purchase creates UM user; limitation exists for profile."""
    from vpn_bot.admin_profile_service import create_profile_full, delete_profile_full

    name = f"INTG_TRAF_{int(time.time()) % 100000}"
    prof, err = await create_profile_full(
        {
            "server_id": live_server.id,
            "name": name,
            "days": 3,
            "limit": 1,
            "price_toman": 1000,
            "rate_limit": "2M/2M",
        }
    )
    assert prof, err

    ok, sub, msg = await checkout_subscription(intg_user.telegram_id, prof.id)
    assert ok, msg

    mgr = get_mikrotik_manager(live_server)
    await asyncio.to_thread(mgr.connect)
    try:
        user_api = mgr._get_resource("/user-manager/user")
        users = await asyncio.to_thread(user_api.get, name=sub.mikrotik_username)
        assert users, "UM user must exist on router"

        lim_api = mgr._get_resource("/user-manager/limitation")
        lims = await asyncio.to_thread(lim_api.get, name=f"lim_{name}")
        assert lims, "lim_{profile} must exist"
        tl = lims[0].get("transfer-limit") or lims[0].get("total-limit")
        assert tl and int(tl) >= 1024**3
    finally:
        await asyncio.to_thread(mgr.close)

    await asyncio.to_thread(mgr.delete_user, sub.mikrotik_username)
    async with AsyncSessionLocal() as session:
        row = await session.get(Subscription, sub.id)
        if row:
            await session.delete(row)
            await session.commit()
    await delete_profile_full(prof.id)


@pytest.mark.asyncio
async def test_live_sync_and_extend_after_purchase(intg_user, live_server):
    """Sync reads usage; extend_validity succeeds on live UM."""
    from vpn_bot.admin_profile_service import create_profile_full, delete_profile_full

    name = f"INTG_SYNC_{int(time.time()) % 100000}"
    prof, err = await create_profile_full(
        {"server_id": live_server.id, "name": name, "days": 2, "limit": 1, "price_toman": 1000}
    )
    assert prof, err
    ok, sub, msg = await checkout_subscription(intg_user.telegram_id, prof.id)
    assert ok, msg

    async with AsyncSessionLocal() as session:
        server = await session.get(type(live_server), live_server.id)
        n = await SyncManager.reconcile_ovpn_subscriptions(session, server)
        await session.commit()
        assert n >= 0

    ext_ok, _ = await extend_subscription_validity(sub.mikrotik_username, 3)
    assert ext_ok is True

    mgr = get_mikrotik_manager(live_server)
    await asyncio.to_thread(mgr.delete_user, sub.mikrotik_username)
    async with AsyncSessionLocal() as session:
        row = await session.get(Subscription, sub.id)
        if row:
            await session.delete(row)
            await session.commit()
    await delete_profile_full(prof.id)
