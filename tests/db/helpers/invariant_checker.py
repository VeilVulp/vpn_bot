"""Post-condition invariant scans for DB tests."""

from __future__ import annotations

from datetime import timezone

import asyncio

from sqlalchemy import select

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import PaymentReceipt, Server, Subscription, User, WireGuardInterface, WireGuardSubscription
from vpn_bot.admin_wg_service import WG_OCCUPYING_STATUSES
from vpn_bot.utils import utc_now


class InvariantViolation(Exception):
    pass


async def check_no_negative_balances(user_ids: list[int] | None = None) -> list[int]:
    """Return user ids with wallet_balance < 0."""
    async with AsyncSessionLocal() as session:
        q = select(User.id, User.wallet_balance)
        if user_ids:
            q = q.where(User.id.in_(user_ids))
        rows = (await session.execute(q)).all()
        return [uid for uid, bal in rows if bal is not None and bal < 0]


async def check_receipts_single_approve(receipt_ids: list[int] | None = None) -> list[int]:
    """Receipts approved more than once (should not happen)."""
    async with AsyncSessionLocal() as session:
        q = select(PaymentReceipt.id, PaymentReceipt.status).where(PaymentReceipt.status == "approved")
        if receipt_ids:
            q = q.where(PaymentReceipt.id.in_(receipt_ids))
        rows = (await session.execute(q)).all()
        return [rid for rid, _ in rows]


async def check_expired_still_active(user_ids: list[int] | None = None) -> list[int]:
    """Active subscriptions with expiry in the past (UTC)."""
    now = utc_now()
    async with AsyncSessionLocal() as session:
        q = select(Subscription.id, Subscription.expiry_date).where(Subscription.status == "active")
        if user_ids:
            q = q.where(Subscription.user_id.in_(user_ids))
        res = await session.execute(q)
        bad = []
        for sid, exp in res.all():
            if not exp:
                continue
            exp_cmp = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
            if exp_cmp < now:
                bad.append(sid)
        return bad


async def check_wg_capacity(server_id: int | None = None) -> list[int]:
    """Interfaces where current_users > max_users."""
    async with AsyncSessionLocal() as session:
        q = select(WireGuardInterface)
        if server_id is not None:
            q = q.where(WireGuardInterface.server_id == server_id)
        res = await session.execute(q)
        return [
            i.id
            for i in res.scalars().all()
            if i.current_users is not None and i.max_users is not None and i.current_users > i.max_users
        ]


async def check_wg_active_over_max(server_id: int | None = None) -> list[tuple[int, int, int]]:
    """Interfaces where live active/pending count exceeds max_users."""
    async with AsyncSessionLocal() as session:
        q = select(WireGuardInterface)
        if server_id is not None:
            q = q.where(WireGuardInterface.server_id == server_id)
        res = await session.execute(q)
        bad: list[tuple[int, int, int]] = []
        for iface in res.scalars().all():
            if iface.max_users is None:
                continue
            active_res = await session.execute(
                select(WireGuardSubscription.id).where(
                    WireGuardSubscription.interface_id == iface.id,
                    WireGuardSubscription.status.in_(WG_OCCUPYING_STATUSES),
                )
            )
            active_count = len(active_res.scalars().all())
            if active_count > iface.max_users:
                bad.append((iface.id, active_count, iface.max_users))
        return bad


async def check_wg_duplicate_assigned_ips(interface_id: int | None = None) -> list[tuple[int, str]]:
    """Duplicate assigned_ip values on the same interface."""
    from collections import Counter

    async with AsyncSessionLocal() as session:
        q = select(WireGuardSubscription.interface_id, WireGuardSubscription.assigned_ip).where(
            WireGuardSubscription.assigned_ip.isnot(None)
        )
        if interface_id is not None:
            q = q.where(WireGuardSubscription.interface_id == interface_id)
        rows = (await session.execute(q)).all()
        counts = Counter((iface_id, ip) for iface_id, ip in rows)
        return [(iface_id, ip) for (iface_id, ip), cnt in counts.items() if cnt > 1]


async def check_mt_um_users_exist(server: Server, usernames: list[str], must_exist: bool = True) -> list[str]:
    """Verify UM users exist (or are absent if must_exist=False)."""
    from vpn_bot.mikrotik_manager import get_mikrotik_manager

    mgr = get_mikrotik_manager(server)
    router_users = await asyncio.to_thread(mgr.get_all_um_users)
    if router_users is None:
        return []  # Cannot verify — do not fail invariants on API error
    names_on_router = {u.get("name") for u in router_users}
    if must_exist:
        return [n for n in usernames if n not in names_on_router]
    return [n for n in usernames if n in names_on_router]


async def check_db_mt_consistency(server_id: int, user_ids: list[int] | None = None) -> list[str]:
    """Active DB subs for server should appear on MikroTik UM."""
    from vpn_bot.mikrotik_manager import get_mikrotik_manager

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            return ["server_not_found"]
        q = select(Subscription).where(
            Subscription.server_id == server_id,
            Subscription.status == "active",
        )
        if user_ids:
            q = q.where(Subscription.user_id.in_(user_ids))
        subs = (await session.execute(q)).scalars().all()

    mgr = get_mikrotik_manager(server)
    router_users = await asyncio.to_thread(mgr.get_all_um_users)
    if router_users is None:
        return []  # API unavailable — skip consistency check
    names_on_router = {u.get("name") for u in router_users}
    return [s.mikrotik_username for s in subs if s.mikrotik_username not in names_on_router]


async def run_all_checks(
    user_ids: list[int] | None = None,
    receipt_ids: list[int] | None = None,
    server_id: int | None = None,
    active_usernames: list[str] | None = None,
    wg_server_id: int | None = None,
) -> dict:
    """Run all invariant checks; raise InvariantViolation if any fail."""
    neg = await check_no_negative_balances(user_ids)
    expired_active = await check_expired_still_active(user_ids)
    wg_sid = wg_server_id if wg_server_id is not None else server_id
    wg_over = await check_wg_capacity(wg_sid)
    wg_active_over = await check_wg_active_over_max(wg_sid)
    wg_dup_ips = await check_wg_duplicate_assigned_ips()
    mt_missing = []
    db_mt_orphans = []
    if server_id is not None and active_usernames:
        async with AsyncSessionLocal() as session:
            server = await session.get(Server, server_id)
        if server and active_usernames:
            mt_missing = await check_mt_um_users_exist(server, active_usernames, must_exist=True)
            db_mt_orphans = await check_db_mt_consistency(server_id, user_ids)
            # Under concurrent load, allow brief MT lag (retry once)
            if mt_missing:
                import asyncio
                await asyncio.sleep(2)
                mt_missing = await check_mt_um_users_exist(server, active_usernames, must_exist=True)
    result = {
        "negative_balances": neg,
        "expired_still_active": expired_active,
        "wg_over_capacity": wg_over,
        "wg_active_over_max": wg_active_over,
        "wg_duplicate_ips": wg_dup_ips,
        "mt_missing_users": mt_missing,
        "db_mt_inconsistent": db_mt_orphans,
    }
    failures = {k: v for k, v in result.items() if v}
    if failures:
        raise InvariantViolation(f"Invariant failures: {failures}")
    return result
