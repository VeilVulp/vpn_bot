"""
Concurrent WireGuard interface capacity tests (mock MikroTik).

Validates purchase/renew/add-data/reconcile/cleanup under contention and
capacity release after expiry without renewal.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from datetime import timedelta

import pytest
from sqlalchemy import delete, select

from tests.conftest_db import FakeBot
from tests.db.helpers.invariant_checker import (
    check_wg_active_over_max,
    check_wg_duplicate_assigned_ips,
    run_all_checks,
)
from tests.live.helpers.wg_workflow_live import renew_wg_sub_for_test
from vpn_bot.admin_cleanup import AdminCleanup
from vpn_bot.admin_wg_service import (
    WG_OCCUPYING_STATUSES,
    add_wg_subscription_data,
    count_wg_interface_active_subs,
)
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import User, WireGuardInterface, WireGuardSubscription
from vpn_bot.sync_manager import SyncManager
from vpn_bot.user_features import finalize_wg_purchase
from vpn_bot.utils import utc_now

pytestmark = pytest.mark.db


@pytest.fixture
def no_wg_preempt(monkeypatch):
    """Disable background preemptive interface creation during capacity tests."""

    async def _noop():
        return []

    monkeypatch.setattr(
        "vpn_bot.admin_wg_service.check_and_preemptively_create_interfaces",
        _noop,
    )
    monkeypatch.setattr(
        "vpn_bot.user_features.check_and_preemptively_create_interfaces",
        _noop,
        raising=False,
    )


async def _isolate_tiny_interface(mock_server, tiny_id: int) -> None:
    """Reset WG state on mock server; keep only the tiny test interface."""
    async with AsyncSessionLocal() as session:
        iface_res = await session.execute(
            select(WireGuardInterface.id).where(WireGuardInterface.server_id == mock_server.id)
        )
        iface_ids = list(iface_res.scalars().all())
        if iface_ids:
            await session.execute(
                delete(WireGuardSubscription).where(
                    WireGuardSubscription.interface_id.in_(iface_ids)
                )
            )
        for iid in iface_ids:
            if iid != tiny_id:
                row = await session.get(WireGuardInterface, iid)
                if row:
                    await session.delete(row)
        tiny = await session.get(WireGuardInterface, tiny_id)
        if tiny:
            tiny.current_users = 0
        await session.commit()


async def _seed_users(count: int, balance: float = 2_000_000.0) -> list[User]:
    users = []
    async with AsyncSessionLocal() as session:
        for _ in range(count):
            tg = random.randint(720_000_000_000, 729_999_999_999)
            user = User(
                telegram_id=tg,
                username=f"wg_cap_{uuid.uuid4().hex[:6]}",
                wallet_balance=balance,
            )
            session.add(user)
            users.append(user)
        await session.commit()
        for user in users:
            await session.refresh(user)
    return users


async def _active_count(iface_id: int) -> int:
    async with AsyncSessionLocal() as session:
        return await count_wg_interface_active_subs(session, iface_id)


async def _subs_on_iface(iface_id: int) -> list[WireGuardSubscription]:
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WireGuardSubscription)
            .where(WireGuardSubscription.interface_id == iface_id)
            .order_by(WireGuardSubscription.id.asc())
        )
        return list(res.scalars().all())


async def _backdate_sub(sub_id: int, hours_ago: int = 48) -> None:
    async with AsyncSessionLocal() as session:
        sub = await session.get(WireGuardSubscription, sub_id)
        sub.expiry_date = utc_now() - timedelta(hours=hours_ago)
        sub.deletion_warning_sent_at = utc_now() - timedelta(hours=25)
        await session.commit()


async def _reconcile(server) -> None:
    async with AsyncSessionLocal() as session:
        from vpn_bot.models import Server

        db_server = await session.get(Server, server.id)
        await SyncManager.reconcile_wg_subscriptions(session, db_server)
        await session.commit()


@pytest.mark.asyncio
async def test_wg_capacity_concurrent_full_interface_chaos(
    db_initialized,
    mock_mikrotik,
    mock_server,
    db_wg_profile,
    db_wg_interface_tiny,
    no_wg_preempt,
):
    tiny_id = db_wg_interface_tiny.id
    await _isolate_tiny_interface(mock_server, tiny_id)
    users = await _seed_users(8)

    sem = asyncio.Semaphore(5)

    async def _buy(user: User) -> bool:
        async with sem:
            return await finalize_wg_purchase(
                user.telegram_id, db_wg_profile.id, context=None, is_tg_id=True
            )

    # Phase A: 5 concurrent purchases on max_users=3 interface
    results = await asyncio.gather(*[_buy(u) for u in users[:5]], return_exceptions=True)
    assert not any(isinstance(r, Exception) for r in results)
    assert sum(1 for r in results if r is True) == 5

    tiny_active = await _active_count(tiny_id)
    assert tiny_active == 3
    assert await check_wg_active_over_max(mock_server.id) == []

    # Phase B: parallel renew + add_data + reconcile (no capacity change)
    tiny_subs = await _subs_on_iface(tiny_id)
    assert len(tiny_subs) == 3

    async def _renew(sub: WireGuardSubscription):
        user = next(u for u in users if u.id == sub.user_id)
        ok, _ = await renew_wg_sub_for_test(user.id, sub.id)
        return ok

    async def _add_data(sub_id: int):
        return await add_wg_subscription_data(sub_id, 1)

    b_results = await asyncio.gather(
        _renew(tiny_subs[0]),
        _renew(tiny_subs[1]),
        _add_data(tiny_subs[0].id),
        _add_data(tiny_subs[1].id),
        _reconcile(mock_server),
        _reconcile(mock_server),
        return_exceptions=True,
    )
    exc = [r for r in b_results if isinstance(r, Exception)]
    assert not exc, exc
    assert await _active_count(tiny_id) == 3

    # Phase C: expire 2 subs on tiny interface
    await _backdate_sub(tiny_subs[0].id)
    await _backdate_sub(tiny_subs[1].id)
    await _reconcile(mock_server)

    refreshed = await _subs_on_iface(tiny_id)
    expired = [s for s in refreshed if s.status == "expired"]
    assert len(expired) == 2
    assert await _active_count(tiny_id) == 1

    # Phase D: 2 new purchases should reuse tiny interface (not a third iface)
    async with AsyncSessionLocal() as session:
        iface_count_before = len(
            (await session.execute(
                select(WireGuardInterface).where(WireGuardInterface.server_id == mock_server.id)
            )).scalars().all()
        )

    d_results = await asyncio.gather(_buy(users[5]), _buy(users[6]), return_exceptions=True)
    assert all(r is True for r in d_results if not isinstance(r, Exception))

    async with AsyncSessionLocal() as session:
        new_subs = (
            await session.execute(
                select(WireGuardSubscription).where(
                    WireGuardSubscription.user_id.in_([users[5].id, users[6].id])
                )
            )
        ).scalars().all()
        assert all(s.interface_id == tiny_id for s in new_subs)
        iface_count_after = len(
            (await session.execute(
                select(WireGuardInterface).where(WireGuardInterface.server_id == mock_server.id)
            )).scalars().all()
        )
    assert iface_count_after == iface_count_before

    # Phase E: cleanup delete expired subs without renewal
    deleted = await AdminCleanup.clean_expired_wg_subscriptions(
        bot=FakeBot(), mode="delete", seconds=0
    )
    assert deleted >= 2
    assert await _active_count(tiny_id) == 3

    # Phase F: tiny is full (3/3); next purchase succeeds on another interface
    assert await _buy(users[7]) is True
    assert await _active_count(tiny_id) == 3
    async with AsyncSessionLocal() as session:
        last_sub = (
            await session.execute(
                select(WireGuardSubscription).where(WireGuardSubscription.user_id == users[7].id)
            )
        ).scalars().first()
        assert last_sub is not None
        assert last_sub.interface_id != tiny_id

    await run_all_checks(user_ids=[u.id for u in users], wg_server_id=mock_server.id)
    assert await check_wg_duplicate_assigned_ips() == []


@pytest.mark.asyncio
async def test_wg_capacity_one_slot_left_race(
    db_initialized,
    mock_mikrotik,
    mock_server,
    db_wg_profile,
    db_wg_interface_tiny,
    db_user_factory,
    no_wg_preempt,
):
    tiny_id = db_wg_interface_tiny.id
    await _isolate_tiny_interface(mock_server, tiny_id)
    u1 = await db_user_factory(balance=2_000_000.0)
    u2 = await db_user_factory(balance=2_000_000.0)

    assert await finalize_wg_purchase(u1.telegram_id, db_wg_profile.id, context=None, is_tg_id=True)
    assert await finalize_wg_purchase(u2.telegram_id, db_wg_profile.id, context=None, is_tg_id=True)
    assert await _active_count(tiny_id) == 2

    race_users = await _seed_users(4)
    sem = asyncio.Semaphore(4)

    async def _buy(user: User) -> bool:
        async with sem:
            return await finalize_wg_purchase(
                user.telegram_id, db_wg_profile.id, context=None, is_tg_id=True
            )

    results = await asyncio.gather(*[_buy(u) for u in race_users], return_exceptions=True)
    assert not any(isinstance(r, Exception) for r in results)
    assert sum(1 for r in results if r is True) == 4

    assert await _active_count(tiny_id) == 3
    assert await check_wg_active_over_max(mock_server.id) == []

    async with AsyncSessionLocal() as session:
        total_active = (
            await session.execute(
                select(WireGuardSubscription)
                .join(WireGuardInterface)
                .where(
                    WireGuardInterface.server_id == mock_server.id,
                    WireGuardSubscription.status.in_(WG_OCCUPYING_STATUSES),
                )
            )
        ).scalars().all()
    assert len(total_active) == 6

    await run_all_checks(
        user_ids=[u1.id, u2.id] + [u.id for u in race_users],
        wg_server_id=mock_server.id,
    )
