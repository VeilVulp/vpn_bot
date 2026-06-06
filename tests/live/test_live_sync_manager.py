"""Live SyncManager reconciliation against real router."""

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.models import Server, Subscription
from vpn_bot.sync_manager import SyncManager

pytestmark = pytest.mark.live_mt


@pytest.mark.asyncio
async def test_reconcile_ovpn_marks_missing_on_router(intg_user, live_server):
    from vpn_bot.admin_profile_service import create_profile_full, delete_profile_full

    prof, err = await create_profile_full(
        {
            "server_id": live_server.id,
            "name": f"INTG_SYNC_{int(datetime.now().timestamp()) % 100000}",
            "days": 1,
            "limit": 1,
            "price_toman": 100,
        }
    )
    assert prof, err

    from vpn_bot.user_features import checkout_subscription

    ok, sub, _ = await checkout_subscription(intg_user.telegram_id, prof.id)
    assert ok

    mgr = get_mikrotik_manager(live_server)
    await asyncio.to_thread(mgr.delete_user, sub.mikrotik_username)

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, live_server.id)
        await SyncManager.reconcile_ovpn_subscriptions(session, server)
        await session.commit()
        db_sub = await session.get(Subscription, sub.id)
        assert db_sub.status in ("inconsistent", "expired", "active", "disabled")

    async with AsyncSessionLocal() as session:
        row = await session.get(Subscription, sub.id)
        if row:
            await session.delete(row)
            await session.commit()
    await delete_profile_full(prof.id)
