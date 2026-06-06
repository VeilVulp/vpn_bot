"""Live admin subscription operations (extend, password, data)."""

import asyncio

import pytest
from sqlalchemy import select

from vpn_bot.admin_profile_service import create_profile_full, delete_profile_full
from vpn_bot.admin_subscription_service import (
    add_subscription_data,
    extend_subscription_validity,
    reset_subscription_password,
    toggle_subscription_status,
)
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.models import Subscription
from vpn_bot.user_features import checkout_subscription

pytestmark = pytest.mark.live_mt


@pytest.mark.asyncio
async def test_admin_extend_reset_and_add_data(intg_user, live_server):
    name = f"INTG_ADMIN_{int(__import__('time').time()) % 100000}"
    prof, err = await create_profile_full(
        {
            "server_id": live_server.id,
            "name": name,
            "days": 3,
            "limit": 1,
            "price_toman": 500,
            "price_usd": 0,
        }
    )
    assert prof, err

    ok, sub, msg = await checkout_subscription(intg_user.telegram_id, prof.id)
    assert ok, msg
    username = sub.mikrotik_username
    new_pass = "IntgTest99"

    ext_ok, _ = await extend_subscription_validity(username, 2)
    assert ext_ok is True

    assert await reset_subscription_password(username, new_pass) is True
    mgr = get_mikrotik_manager(live_server)
    info = await asyncio.to_thread(mgr.get_user_info, username)
    assert info is not None

    assert await add_subscription_data(username, 1) is True

    toggled, status = await toggle_subscription_status(username)
    assert toggled is True
    assert status in ("disabled", "enabled")

    async with AsyncSessionLocal() as session:
        db_sub = (
            await session.execute(
                select(Subscription).where(Subscription.mikrotik_username == username)
            )
        ).scalars().first()
        assert db_sub is not None

    await asyncio.to_thread(mgr.delete_user, username)
    async with AsyncSessionLocal() as session:
        if db_sub:
            await session.delete(await session.get(Subscription, db_sub.id))
            await session.commit()
    await delete_profile_full(prof.id)
