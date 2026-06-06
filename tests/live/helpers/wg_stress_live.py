"""Stress orchestration helpers for live WG tests on real MikroTik."""

from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import delete, func, select

from tests.db.helpers.invariant_checker import check_wg_active_over_max
from tests.live.helpers.chaos_report import record_op, write_report
from tests.live.helpers.wg_workflow_live import (
    backdate_wg_expiry,
    fetch_latest_wg_sub,
    load_sub,
    renew_wg_sub_for_test,
    run_wg_reconcile,
    set_wg_deletion_warning_sent,
    teardown_wg_sub,
)
from vpn_bot.admin_wg_service import count_wg_interface_active_subs
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import User, WireGuardInterface, WireGuardSubscription
from vpn_bot.user_features import finalize_wg_purchase

STRESS_TG_BASE = 880_003_000
STRESS_WALLET = 5_000_000.0
REPORT_PATH = Path("reports/live_wg_stress_last.json")


async def seed_stress_users(
    count: int,
    base_tg: int = STRESS_TG_BASE,
    *,
    balance: float = STRESS_WALLET,
) -> list[User]:
    users: list[User] = []
    async with AsyncSessionLocal() as session:
        for i in range(count):
            tg = base_tg + i
            res = await session.execute(select(User).where(User.telegram_id == tg))
            user = res.scalars().first()
            if not user:
                user = User(
                    telegram_id=tg,
                    username=f"wg_stress_live_{tg}",
                    wallet_balance=balance,
                    phone_number=f"+98912{tg % 10_000_000:07d}",
                )
                session.add(user)
            else:
                user.wallet_balance = balance
                if not user.phone_number:
                    user.phone_number = f"+98912{tg % 10_000_000:07d}"
            users.append(user)
        await session.commit()
        for user in users:
            await session.refresh(user)
    return users


async def fund_users(users: list[User], balance: float = STRESS_WALLET) -> None:
    async with AsyncSessionLocal() as session:
        for user in users:
            row = await session.get(User, user.id)
            row.wallet_balance = balance
        await session.commit()


async def active_on_iface(iface_id: int) -> int:
    async with AsyncSessionLocal() as session:
        return await count_wg_interface_active_subs(session, iface_id)


async def count_ifaces(server_id: int) -> int:
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(func.count())
            .select_from(WireGuardInterface)
            .where(
                WireGuardInterface.server_id == server_id,
                WireGuardInterface.is_active == True,
            )
        )
        return int(res.scalar() or 0)


async def subs_on_iface(iface_id: int) -> list[WireGuardSubscription]:
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WireGuardSubscription)
            .where(WireGuardSubscription.interface_id == iface_id)
            .order_by(WireGuardSubscription.id.asc())
        )
        return list(res.scalars().all())


async def assert_no_capacity_violations(server_id: int) -> None:
    violations = await check_wg_active_over_max(server_id)
    assert violations == [], violations


async def ensure_mt_ready(server) -> None:
    """Drop pooled MT connection and reconnect before heavy concurrent phases."""
    from vpn_bot.mikrotik_manager import MikroTikManager, get_mikrotik_manager

    mgr = get_mikrotik_manager(server)
    MikroTikManager.drop_connection_pool(mgr.host, mgr.port, mgr.username)
    await asyncio.to_thread(mgr.connect_with_retry)
    await asyncio.sleep(0.5)


async def burst_purchase(
    users: list[User],
    profile_id: int,
    sem: asyncio.Semaphore,
    metrics: dict | None = None,
    mt_sem: asyncio.Semaphore | None = None,
) -> list[tuple[User, bool, WireGuardSubscription | None]]:
    created: list[tuple[User, bool, WireGuardSubscription | None]] = []

    async def _buy(user: User):
        import time

        t0 = time.perf_counter()
        async with sem:
            try:
                if mt_sem is not None:
                    async with mt_sem:
                        ok = await finalize_wg_purchase(
                            user.telegram_id,
                            profile_id,
                            context=None,
                            is_tg_id=True,
                        )
                else:
                    ok = await finalize_wg_purchase(
                        user.telegram_id,
                        profile_id,
                        context=None,
                        is_tg_id=True,
                    )
                sub = None
                if ok:
                    sub = await fetch_latest_wg_sub(user.id, profile_id)
                if metrics is not None:
                    record_op(metrics, "purchase", ok, time.perf_counter() - t0)
                created.append((user, ok, sub))
                return ok
            except Exception as exc:
                if metrics is not None:
                    record_op(metrics, "purchase", False, time.perf_counter() - t0, str(exc))
                raise

    await asyncio.gather(*[_buy(u) for u in users], return_exceptions=False)
    return created


async def burst_renew(
    subs: list[WireGuardSubscription],
    sem: asyncio.Semaphore,
    metrics: dict | None = None,
    mt_sem: asyncio.Semaphore | None = None,
) -> list[bool]:
    results: list[bool] = []

    async def _renew(sub: WireGuardSubscription):
        import time

        t0 = time.perf_counter()
        async with sem:
            refreshed = await load_sub(sub.id)
            if not refreshed or not refreshed.user_id:
                if metrics is not None:
                    record_op(metrics, "renew", False, time.perf_counter() - t0, "no sub")
                results.append(False)
                return False
            if mt_sem is not None:
                async with mt_sem:
                    ok, _ = await renew_wg_sub_for_test(refreshed.user_id, sub.id)
            else:
                ok, _ = await renew_wg_sub_for_test(refreshed.user_id, sub.id)
            if metrics is not None:
                record_op(metrics, "renew", ok, time.perf_counter() - t0)
            results.append(ok)
            return ok

    await asyncio.gather(*[_renew(s) for s in subs], return_exceptions=False)
    return results


async def expire_subs_batch(sub_ids: list[int]) -> None:
    for sid in sub_ids:
        await backdate_wg_expiry(sid, hours_ago=48)
        await set_wg_deletion_warning_sent(sid, hours_ago=25)


async def guarded(coro, mt_sem: asyncio.Semaphore, metrics: dict, op_name: str):
    import time

    t0 = time.perf_counter()
    try:
        async with mt_sem:
            result = await coro
        record_op(metrics, op_name, True, time.perf_counter() - t0)
        return result
    except Exception as exc:
        record_op(metrics, op_name, False, time.perf_counter() - t0, str(exc))
        raise


async def teardown_stress_run(
    subs: list[WireGuardSubscription],
    server,
    *,
    tiny_id: int | None = None,
    keep_tiny_iface: bool = True,
) -> None:
    """Remove stress subs from router/DB; optionally drop overflow interfaces."""
    from vpn_bot.mikrotik_manager import get_mikrotik_manager

    seen: set[int] = set()
    for sub in subs:
        if not sub or sub.id in seen:
            continue
        seen.add(sub.id)
        refreshed = await load_sub(sub.id)
        if refreshed:
            await teardown_wg_sub(refreshed, server)

    mgr = get_mikrotik_manager(server)
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WireGuardInterface).where(WireGuardInterface.server_id == server.id)
        )
        for iface in res.scalars().all():
            if keep_tiny_iface and tiny_id and iface.id == tiny_id:
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


def write_stress_report(metrics: dict, path: Path | str = REPORT_PATH) -> dict:
    return write_report(metrics, path)


async def reconcile_guarded(server, mt_sem, metrics):
    return await guarded(run_wg_reconcile(server), mt_sem, metrics, "reconcile")
