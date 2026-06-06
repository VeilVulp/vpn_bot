"""Fixtures for live chaos (concurrent admin + user + maintenance) tests."""

import asyncio
import os
import random
import time
import uuid

import pytest
from sqlalchemy import delete, select

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import (
    PaymentReceipt,
    Profile,
    Subscription,
    User,
    WireGuardSubscription,
)
from tests.conftest_db import FakeBot
from tests.live.helpers.chaos_report import new_metrics

CHAOS_PROFILE_PREFIX = "INTG_CHAOS_OVPN_"
CHAOS_TG_BASE = 881_000_000


@pytest.fixture
def chaos_metrics():
    return new_metrics()


@pytest.fixture
def mt_sem():
    n = int(os.getenv("CHAOS_MT_SEM", "4"))
    return asyncio.Semaphore(n)


@pytest.fixture
def fake_bot():
    return FakeBot()


@pytest.fixture
async def chaos_users(db_initialized, live_server):
    """Eight users with high balance for parallel purchases."""
    users = []
    async with AsyncSessionLocal() as session:
        for i in range(8):
            tg = CHAOS_TG_BASE + random.randint(0, 999_999)
            res = await session.execute(select(User).where(User.telegram_id == tg))
            u = res.scalars().first()
            if not u:
                u = User(
                    telegram_id=tg,
                    username=f"chaos_{uuid.uuid4().hex[:8]}",
                    wallet_balance=15_000_000.0,
                )
                session.add(u)
            else:
                u.wallet_balance = 15_000_000.0
            users.append(u)
        await session.commit()
        for u in users:
            await session.refresh(u)
    yield users
    # DB cleanup handled by chaos_cleanup fixture


@pytest.fixture
async def chaos_ovpn_profile(live_server):
    from vpn_bot.admin_profile_service import create_profile_full, delete_profile_full

    name = f"{CHAOS_PROFILE_PREFIX}{int(time.time()) % 100000}"
    prof, err = await create_profile_full(
        {
            "server_id": live_server.id,
            "name": name,
            "days": 7,
            "limit": 1,
            "price_usd": 1.0,
            "price_toman": 1000.0,
        }
    )
    if not prof:
        pytest.fail(f"chaos OVPN profile failed: {err}")
    yield prof
    try:
        await delete_profile_full(prof.id)
    except Exception:
        pass


@pytest.fixture
async def chaos_expired_ovpn(chaos_users, live_server, chaos_ovpn_profile):
    """One expired subscription for cleanup warn/delete tests."""
    from vpn_bot.utils import utc_now
    from datetime import timedelta

    user = chaos_users[0]
    from vpn_bot.user_features import checkout_subscription

    ok, sub, msg = await checkout_subscription(user.telegram_id, chaos_ovpn_profile.id)
    if not ok or not sub:
        pytest.skip(f"Could not seed expired sub: {msg}")

    async with AsyncSessionLocal() as session:
        db_sub = await session.get(Subscription, sub.id)
        db_sub.expiry_date = utc_now() - timedelta(days=10)
        db_sub.mikrotik_username = sub.mikrotik_username
        await session.commit()

    yield {"sub": sub, "username": sub.mikrotik_username, "user": user}


@pytest.fixture
async def chaos_cleanup(live_server, request):
    """Track chaos artifacts and purge UM/DB after each chaos test."""
    state = {
        "usernames": [],
        "profile_ids": [],
        "user_ids": [],
        "receipt_ids": [],
        "chaos_tg_ids": [],
    }
    yield state

    import asyncio as aio

    from vpn_bot.admin_profile_service import delete_profile_full
    from vpn_bot.mikrotik_manager import get_mikrotik_manager
    from tests.conftest import _is_test_um_username

    mgr = get_mikrotik_manager(live_server)
    try:
        await aio.to_thread(mgr.connect)
        user_api = mgr._get_resource("/user-manager/user")
        users = await aio.to_thread(user_api.get)
        for u in users or []:
            name = u.get("name", "")
            if _is_test_um_username(name) or name in state["usernames"]:
                try:
                    await aio.to_thread(mgr.delete_user, name)
                except Exception:
                    pass
        await aio.to_thread(mgr.close)
    except Exception:
        pass

    for pid in state.get("profile_ids", []):
        try:
            await delete_profile_full(pid)
        except Exception:
            pass

    from vpn_bot.models import Transaction

    async with AsyncSessionLocal() as session:
        if state.get("user_ids"):
            await session.execute(
                delete(Subscription).where(Subscription.user_id.in_(state["user_ids"]))
            )
            await session.execute(
                delete(WireGuardSubscription).where(
                    WireGuardSubscription.user_id.in_(state["user_ids"])
                )
            )
            await session.execute(
                delete(PaymentReceipt).where(PaymentReceipt.user_id.in_(state["user_ids"]))
            )
            await session.execute(
                delete(Transaction).where(Transaction.user_id.in_(state["user_ids"]))
            )
        elif state.get("usernames"):
            await session.execute(
                delete(Subscription).where(
                    Subscription.mikrotik_username.in_(state["usernames"])
                )
            )
        if state.get("chaos_tg_ids"):
            await session.execute(
                delete(User).where(User.telegram_id.in_(state["chaos_tg_ids"]))
            )
        await session.commit()
