"""
Live concurrent WireGuard capacity test on real MikroTik.

Subset of the mock chaos test: parallel purchases, renew/add-data, expiry,
reuse of freed slots, and cleanup delete.
"""

from __future__ import annotations

import asyncio

import pytest
from sqlalchemy import delete, select

from tests.conftest_db import FakeBot
from tests.db.helpers.invariant_checker import check_wg_active_over_max, check_wg_duplicate_assigned_ips
from tests.live.helpers.wg_workflow_live import (
    backdate_wg_expiry,
    fetch_latest_wg_sub,
    load_sub,
    renew_wg_sub_for_test,
    run_wg_reconcile,
    set_wg_deletion_warning_sent,
    teardown_wg_sub,
)
from vpn_bot.admin_cleanup import AdminCleanup
from vpn_bot.admin_wg_service import (
    add_wg_subscription_data,
    count_wg_interface_active_subs,
    sync_wg_interface_current_users,
)
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import WireGuardInterface, WireGuardSubscription
from vpn_bot.user_features import finalize_wg_purchase

pytestmark = pytest.mark.live_mt


async def _active_count(iface_id: int) -> int:
    async with AsyncSessionLocal() as session:
        return await count_wg_interface_active_subs(session, iface_id)


async def _isolate_tiny_interface(live_server, tiny_id: int, created_subs: list) -> None:
    """
    Drop overflow subs/interfaces from concurrent fill so slot-reuse phases
    only see the tiny test interface (matches mock db test isolation).
    """
    from vpn_bot.mikrotik_manager import get_mikrotik_manager

    mgr = get_mikrotik_manager(live_server)
    overflow = [s for s in created_subs if s.interface_id != tiny_id]
    for sub in overflow:
        refreshed = await load_sub(sub.id)
        if refreshed:
            await teardown_wg_sub(refreshed, live_server)

    created_subs[:] = [s for s in created_subs if s.interface_id == tiny_id]

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WireGuardInterface).where(WireGuardInterface.server_id == live_server.id)
        )
        for iface in res.scalars().all():
            if iface.id == tiny_id:
                continue
            await session.execute(
                delete(WireGuardSubscription).where(
                    WireGuardSubscription.interface_id == iface.id
                )
            )
            name = iface.name
            await session.delete(iface)
            await session.commit()
            try:
                await asyncio.to_thread(mgr.cleanup_wg_interface_automation, name)
                await asyncio.to_thread(mgr.delete_wg_interface, name)
            except Exception:
                pass

        await sync_wg_interface_current_users(session, tiny_id)
        await session.commit()


async def _seed_live_users(count: int, base_tg: int = 880002000) -> list:
    from vpn_bot.models import User

    users = []
    async with AsyncSessionLocal() as session:
        for i in range(count):
            tg = base_tg + i
            res = await session.execute(select(User).where(User.telegram_id == tg))
            user = res.scalars().first()
            if not user:
                user = User(
                    telegram_id=tg,
                    username=f"wg_cap_live_{tg}",
                    wallet_balance=5_000_000.0,
                )
                session.add(user)
            else:
                user.wallet_balance = 5_000_000.0
            users.append(user)
        await session.commit()
        for user in users:
            await session.refresh(user)
    return users


@pytest.mark.asyncio
async def test_live_wg_capacity_concurrent_purchase_renew_cleanup(
    intg_wg_profile_long_days,
    intg_wg_interface_tiny,
    live_server,
    no_wg_preempt,
):
    tiny_id = intg_wg_interface_tiny.id
    users = await _seed_live_users(6)
    sem = asyncio.Semaphore(4)
    created_subs: list[WireGuardSubscription] = []

    async def _buy(user) -> bool:
        async with sem:
            ok = await finalize_wg_purchase(
                user.telegram_id,
                intg_wg_profile_long_days.id,
                context=None,
                is_tg_id=True,
            )
            if ok:
                sub = await fetch_latest_wg_sub(user.id, intg_wg_profile_long_days.id)
                if sub:
                    created_subs.append(sub)
            return ok

    # Phase A: 4 parallel purchases (max_users=3 on tiny iface)
    results = await asyncio.gather(*[_buy(u) for u in users[:4]], return_exceptions=True)
    assert not any(isinstance(r, Exception) for r in results)
    assert sum(1 for r in results if r is True) == 4
    assert await _active_count(tiny_id) == 3
    assert await check_wg_active_over_max(live_server.id) == []

    tiny_subs = [
        s for s in created_subs if s.interface_id == tiny_id
    ]
    assert len(tiny_subs) == 3

    # Overflow iface from the 4th concurrent buy would win least-loaded pick later.
    await _isolate_tiny_interface(live_server, tiny_id, created_subs)
    assert await _active_count(tiny_id) == 3

    # Phase B: renew + add_data on one tiny sub
    target = tiny_subs[0]
    buyer = next(u for u in users if u.id == target.user_id)
    ok, msg = await renew_wg_sub_for_test(buyer.id, target.id)
    assert ok, msg
    assert await add_wg_subscription_data(target.id, 1) is True

    # Phase C: expire one sub and reconcile
    expire_sub = tiny_subs[1]
    await backdate_wg_expiry(expire_sub.id, hours_ago=48)
    await set_wg_deletion_warning_sent(expire_sub.id, hours_ago=25)
    await run_wg_reconcile(live_server)
    expired = await load_sub(expire_sub.id)
    assert expired.status == "expired"
    assert await _active_count(tiny_id) == 2

    # Phase D: new purchase reuses tiny interface slot
    new_sub = None
    assert await _buy(users[4]) is True
    new_sub = await fetch_latest_wg_sub(users[4].id, intg_wg_profile_long_days.id)
    assert new_sub.interface_id == tiny_id
    assert await _active_count(tiny_id) == 3

    # Phase E: cleanup delete expired row
    deleted = await AdminCleanup.clean_expired_wg_subscriptions(
        bot=FakeBot(), mode="delete", seconds=0
    )
    assert deleted >= 1

    # Phase F: final purchase succeeds
    assert await _buy(users[5]) is True
    assert await check_wg_duplicate_assigned_ips() == []

    for sub in created_subs:
        refreshed = await load_sub(sub.id)
        if refreshed:
            await teardown_wg_sub(refreshed, live_server)
    if new_sub:
        refreshed = await load_sub(new_sub.id)
        if refreshed:
            await teardown_wg_sub(refreshed, live_server)
    last = await fetch_latest_wg_sub(users[5].id, intg_wg_profile_long_days.id)
    if last:
        await teardown_wg_sub(last, live_server)
