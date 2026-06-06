"""Shared helpers for live WireGuard workflow tests on real MikroTik."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import joinedload

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Server, User, WireGuardSubscription
from vpn_bot.sync_manager import SyncManager
from vpn_bot.utils import LanguageManager, utc_now
from vpn_bot.wallet_manager import WalletManager


def _peer_is_disabled(peer: dict | None) -> bool:
    if not peer:
        return False
    return peer.get("disabled") in ("true", "yes", True)


async def run_wg_reconcile(server) -> int:
    """Run SyncManager.reconcile_wg_subscriptions for a server."""
    async with AsyncSessionLocal() as session:
        db_server = await session.get(Server, server.id)
        updated = await SyncManager.reconcile_wg_subscriptions(session, db_server)
        await session.commit()
        return updated


async def load_sub(sub_id: int) -> WireGuardSubscription | None:
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WireGuardSubscription)
            .options(
                joinedload(WireGuardSubscription.interface),
                joinedload(WireGuardSubscription.profile),
                joinedload(WireGuardSubscription.user),
            )
            .where(WireGuardSubscription.id == sub_id)
        )
        return res.scalars().first()


def _mt():
    from vpn_bot.mikrotik_manager import get_mikrotik_manager

    return get_mikrotik_manager


async def get_peer_disabled(mgr, iface_name: str, pubkey: str) -> bool:
    peer = await asyncio.to_thread(mgr.get_wg_peer, iface_name, pubkey)
    return _peer_is_disabled(peer)


async def assert_peer_disabled(mgr, iface_name: str, pubkey: str) -> None:
    assert await get_peer_disabled(mgr, iface_name, pubkey), (
        f"Expected peer {pubkey[:12]}... disabled on {iface_name}"
    )


async def assert_peer_enabled(mgr, iface_name: str, pubkey: str) -> None:
    assert not await get_peer_disabled(mgr, iface_name, pubkey), (
        f"Expected peer {pubkey[:12]}... enabled on {iface_name}"
    )


async def fetch_latest_wg_sub(user_id: int, profile_id: int) -> WireGuardSubscription | None:
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WireGuardSubscription)
            .options(
                joinedload(WireGuardSubscription.interface),
                joinedload(WireGuardSubscription.profile),
            )
            .where(
                WireGuardSubscription.user_id == user_id,
                WireGuardSubscription.profile_id == profile_id,
            )
            .order_by(WireGuardSubscription.id.desc())
        )
        return res.scalars().first()


async def seed_wg_usage_at_quota(
    sub_id: int,
    *,
    at_cap: bool = True,
    server=None,
) -> WireGuardSubscription:
    """
    Align DB usage with current router counters, then set totals at/near quota cap.
    Router rx/tx are read-only; this seeds DB so reconcile enforces quota without real traffic.
    """
    async with AsyncSessionLocal() as session:
        sub = await session.get(
            WireGuardSubscription,
            sub_id,
            options=[
                joinedload(WireGuardSubscription.interface),
            ],
        )
        if not sub or not sub.interface:
            raise ValueError(f"WG sub {sub_id} or interface missing")

        srv = server
        if srv is None:
            srv = await session.get(Server, sub.interface.server_id)
        mgr = _mt()(srv)
        stats = await asyncio.to_thread(
            mgr.get_wg_peer_stats,
            sub.interface.name,
            sub.peer_public_key,
        )
        curr_rx = int((stats or {}).get("rx", 0))
        curr_tx = int((stats or {}).get("tx", 0))
        sub.last_router_rx = curr_rx
        sub.last_router_tx = curr_tx

        quota = int(sub.bytes_remaining or 0)
        if quota > 0:
            if at_cap:
                sub.total_bytes_rx = quota
                sub.total_bytes_tx = 0
            else:
                sub.total_bytes_rx = max(quota - 1, 0)
                sub.total_bytes_tx = 0
        await session.commit()
        await session.refresh(sub)
        return sub


async def backdate_wg_expiry(sub_id: int, *, hours_ago: int = 2) -> WireGuardSubscription:
    async with AsyncSessionLocal() as session:
        sub = await session.get(WireGuardSubscription, sub_id)
        if not sub:
            raise ValueError(f"WG sub {sub_id} missing")
        sub.expiry_date = utc_now() - timedelta(hours=hours_ago)
        await session.commit()
        await session.refresh(sub)
        return sub


async def set_wg_deletion_warning_sent(sub_id: int, *, hours_ago: int = 25) -> None:
    async with AsyncSessionLocal() as session:
        sub = await session.get(WireGuardSubscription, sub_id)
        if not sub:
            raise ValueError(f"WG sub {sub_id} missing")
        sub.deletion_warning_sent_at = utc_now() - timedelta(hours=hours_ago)
        await session.commit()


async def teardown_wg_sub(sub: WireGuardSubscription, server) -> None:
    """Remove peer/queue from MikroTik and delete DB row."""
    mgr = _mt()(server)
    if sub.interface and sub.peer_public_key:
        try:
            await asyncio.to_thread(
                mgr.remove_wg_peer, sub.interface.name, sub.peer_public_key
            )
        except Exception:
            pass
    if sub.unique_identifier:
        try:
            await asyncio.to_thread(mgr.remove_wg_queue, sub.unique_identifier)
        except Exception:
            pass

    async with AsyncSessionLocal() as session:
        row = await session.get(WireGuardSubscription, sub.id)
        if row:
            await session.delete(row)
            await session.commit()


async def renew_wg_sub_for_test(user_id: int, sub_id: int) -> tuple[bool, str]:
    """
    Service-level WG renewal (mirrors confirm_wg_renewal without Telegram).
    Returns (success, message).
    """
    async with AsyncSessionLocal() as session:
        u_res = await session.execute(select(User).where(User.id == user_id))
        user = u_res.scalars().first()
        if not user:
            return False, "user_not_found"

        s_res = await session.execute(
            select(WireGuardSubscription)
            .options(
                joinedload(WireGuardSubscription.profile),
                joinedload(WireGuardSubscription.interface),
            )
            .where(WireGuardSubscription.id == sub_id)
        )
        subscription = s_res.scalars().first()
        if not subscription or subscription.user_id != user.id:
            return False, "sub_not_found"

        profile = subscription.profile
        if not profile:
            return False, "plan_gone"

        p_price = profile.price_toman
        desc = LanguageManager.get(
            "wg.renewal_desc",
            name=profile.name,
            uid=subscription.unique_identifier,
        )
        success = await WalletManager.deduct(user.id, p_price, desc, session=session)
        if not success:
            return False, "insufficient_balance"

        now = utc_now()
        exp = subscription.expiry_date
        if exp and exp.tzinfo is None:
            from datetime import timezone

            exp = exp.replace(tzinfo=timezone.utc)
        if exp and exp < now:
            subscription.expiry_date = now + timedelta(days=profile.duration_days)
        else:
            subscription.expiry_date += timedelta(days=profile.duration_days)

        subscription.total_bytes_rx = 0
        subscription.total_bytes_tx = 0
        subscription.bytes_remaining = (profile.volume_gb or 0) * 1024**3
        subscription.status = "active"

        interface = subscription.interface
        if interface:
            server = await session.get(Server, interface.server_id)
            mt = _mt()(server)
            await asyncio.to_thread(
                mt.set_wg_peer_status,
                interface.name,
                subscription.peer_public_key,
                False,
            )
            stats = await asyncio.to_thread(
                mt.get_wg_peer_stats,
                interface.name,
                subscription.peer_public_key,
            )
            if stats:
                subscription.last_router_rx = stats.get("rx", 0)
                subscription.last_router_tx = stats.get("tx", 0)
            else:
                subscription.last_router_rx = 0
                subscription.last_router_tx = 0

        await session.commit()
        return True, "ok"
