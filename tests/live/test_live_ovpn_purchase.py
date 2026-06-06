"""Live OVPN purchase via checkout_subscription."""

import asyncio

import pytest
from sqlalchemy import select

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.models import Subscription
from vpn_bot.user_features import checkout_subscription

pytestmark = pytest.mark.live_mt


@pytest.mark.asyncio
async def test_checkout_subscription_creates_um_user(intg_user, intg_ovpn_profile, live_server):
    tg_id = intg_user.telegram_id
    ok, sub, err = await checkout_subscription(tg_id, intg_ovpn_profile.id)
    assert ok is True, err
    assert sub is not None
    assert sub.mikrotik_username.startswith("u")

    mgr = get_mikrotik_manager(live_server)
    info = await asyncio.to_thread(mgr.get_user_info, sub.mikrotik_username)
    assert info is not None, "User should exist on MikroTik User Manager"

    async with AsyncSessionLocal() as session:
        db_sub = await session.get(Subscription, sub.id)
        assert db_sub is not None
        assert db_sub.status == "active"

    await asyncio.to_thread(mgr.delete_user, sub.mikrotik_username)
    async with AsyncSessionLocal() as session:
        row = await session.get(Subscription, sub.id)
        if row:
            await session.delete(row)
            await session.commit()
