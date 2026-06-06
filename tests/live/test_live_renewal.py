"""Live OVPN renewal / extension on MikroTik."""

import asyncio
from datetime import datetime, timedelta

import pytest

from vpn_bot.admin_profile_service import create_profile_full, delete_profile_full
from vpn_bot.admin_subscription_service import extend_subscription_validity
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.models import Subscription
from vpn_bot.user_features import checkout_subscription

pytestmark = pytest.mark.live_mt


@pytest.mark.asyncio
async def test_ovpn_extend_validity_after_purchase(intg_user, live_server):
    name = f"INTG_RENEW_{int(__import__('time').time()) % 100000}"
    prof, err = await create_profile_full(
        {
            "server_id": live_server.id,
            "name": name,
            "days": 3,
            "limit": 1,
            "price_toman": 500,
        }
    )
    assert prof, err

    ok, sub, msg = await checkout_subscription(intg_user.telegram_id, prof.id)
    assert ok, msg

    async with AsyncSessionLocal() as session:
        db_sub = await session.get(Subscription, sub.id)
        ref = db_sub.expiry_date
        tz = ref.tzinfo if ref and ref.tzinfo else None
        before = ref

    ext_ok, new_exp = await extend_subscription_validity(sub.mikrotik_username, 5)
    assert ext_ok is True

    async with AsyncSessionLocal() as session:
        after = (await session.get(Subscription, sub.id)).expiry_date
        assert after > before

    mgr = get_mikrotik_manager(live_server)
    await asyncio.to_thread(mgr.delete_user, sub.mikrotik_username)
    async with AsyncSessionLocal() as session:
        row = await session.get(Subscription, sub.id)
        if row:
            await session.delete(row)
            await session.commit()
    await delete_profile_full(prof.id)
