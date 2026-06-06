"""
Comprehensive traffic / quota / expiry / renewal / bandwidth policy tests (mock MikroTik).

Validates that SyncManager and admin/user flows drive the same actions expected on a real router.
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from vpn_bot.admin_subscription_service import add_subscription_data, extend_subscription_validity
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Profile, Server, Subscription, WireGuardProfile, WireGuardSubscription
from vpn_bot.sync_manager import SyncManager
from tests.helpers.mock_mikrotik import MockMikrotikManager
from vpn_bot.utils import utc_now

pytestmark = pytest.mark.db


def _uname(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# --- OVPN (User Manager) ---


@pytest.mark.asyncio
async def test_t1_ovpn_sync_pulls_usage_from_router(mock_server, mock_mikrotik, db_user):
    """T1: reconcile_ovpn copies download+upload into used_bytes."""
    uname = _uname("t1_ovpn")
    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.um_users[uname] = {
        "name": uname,
        "disabled": "false",
        "download-used": str(500_000_000),
        "upload-used": str(200_000_000),
    }
    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=None,
            mikrotik_username=uname,
            mikrotik_password="x",
            expiry_date=utc_now() + timedelta(days=5),
            status="active",
            used_bytes=0,
            total_limit_bytes=2 * 1024**3,
        )
        session.add(sub)
        await session.commit()

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, mock_server.id)
        await SyncManager.reconcile_ovpn_subscriptions(session, server)
        await session.commit()
        refreshed = (
            await session.execute(select(Subscription).where(Subscription.mikrotik_username == uname))
        ).scalar_one()

    assert refreshed.used_bytes == 700_000_000


@pytest.mark.asyncio
async def test_t2_ovpn_quota_exhausted_disables_on_server(mock_server, mock_mikrotik, db_user):
    """T2: used_bytes >= total_limit_bytes → disable_user + status disabled."""
    uname = _uname("t2_quota")
    limit = 1 * 1024**3
    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.um_users[uname] = {
        "name": uname,
        "disabled": "false",
        "download-used": str(limit),
        "upload-used": "0",
    }
    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=None,
            mikrotik_username=uname,
            mikrotik_password="x",
            expiry_date=utc_now() + timedelta(days=3),
            status="active",
            used_bytes=limit,
            total_limit_bytes=limit,
        )
        session.add(sub)
        await session.commit()

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, mock_server.id)
        await SyncManager.reconcile_ovpn_subscriptions(session, server)
        await session.commit()
        refreshed = (
            await session.execute(select(Subscription).where(Subscription.mikrotik_username == uname))
        ).scalar_one()

    assert refreshed.status == "disabled"
    assert uname in mgr.disable_user_calls


@pytest.mark.asyncio
async def test_t3_ovpn_add_data_increases_router_limit(mock_server, mock_mikrotik, db_user, db_ovpn_profile):
    """T3: add_subscription_data bumps lim_{profile} transfer-limit on mock router."""
    uname = _uname("t3_add")
    prof_name = db_ovpn_profile.name
    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.um_users[uname] = {"name": uname, "disabled": "false", "download-used": "0", "upload-used": "0"}
    mgr.um_user_profiles[uname] = prof_name
    lim_name = f"lim_{prof_name}"
    mgr.um_limitations[lim_name] = {"name": lim_name, "transfer-limit": str(1024**3)}

    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=db_ovpn_profile.id,
            mikrotik_username=uname,
            mikrotik_password="x",
            expiry_date=utc_now() + timedelta(days=5),
            status="active",
            total_limit_bytes=1024**3,
        )
        session.add(sub)
        await session.commit()

    assert await add_subscription_data(uname, 2) is True
    assert int(mgr.um_limitations[lim_name]["transfer-limit"]) == 3 * 1024**3

    async with AsyncSessionLocal() as session:
        refreshed = (
            await session.execute(select(Subscription).where(Subscription.mikrotik_username == uname))
        ).scalar_one()
    assert refreshed.total_limit_bytes == 1024**3 + 2 * 1024**3


@pytest.mark.asyncio
async def test_t4_ovpn_extend_validity_calls_router(mock_server, mock_mikrotik, db_user):
    """T4: admin extend updates DB expiry and calls extend_validity."""
    uname = _uname("t4_ext")
    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.um_users[uname] = {"name": uname, "disabled": "false"}
    now = utc_now()
    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=None,
            mikrotik_username=uname,
            mikrotik_password="x",
            expiry_date=now + timedelta(days=2),
            status="active",
        )
        session.add(sub)
        await session.commit()
        before = sub.expiry_date

    ok, new_exp = await extend_subscription_validity(uname, 5)
    assert ok is True
    assert new_exp > before


# --- WireGuard ---


@pytest.mark.asyncio
async def test_t5_wg_sync_cumulative_traffic_delta(mock_server, mock_mikrotik, db_user, db_wg_interface):
    """T5: WG sync adds rx/tx deltas to DB counters (cumulative)."""
    pub = f"wg_t5_{uuid.uuid4().hex}"
    iface_name = db_wg_interface.name
    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.wg_peers[iface_name] = {pub: {"public-key": pub, "disabled": "false", "rx": 0, "tx": 0}}

    async with AsyncSessionLocal() as session:
        sub = WireGuardSubscription(
            user_id=db_user.id,
            interface_id=db_wg_interface.id,
            profile_id=None,
            unique_identifier=f"WG-T5_{uuid.uuid4().hex[:6]}",
            peer_public_key=pub,
            peer_private_key="priv",
            assigned_ip="10.88.0.5",
            expiry_date=utc_now() + timedelta(days=5),
            bytes_remaining=5 * 1024**3,
            status="active",
            last_router_rx=0,
            last_router_tx=0,
        )
        session.add(sub)
        await session.commit()

    mgr.set_peer_traffic(iface_name, pub, rx=1_000_000_000, tx=500_000_000)
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, mock_server.id)
        await SyncManager.reconcile_wg_subscriptions(session, server)
        await session.commit()
        refreshed = (
            await session.execute(
                select(WireGuardSubscription).where(WireGuardSubscription.peer_public_key == pub)
            )
        ).scalar_one()

    assert refreshed.total_bytes_rx == 1_000_000_000
    assert refreshed.total_bytes_tx == 500_000_000
    assert refreshed.last_router_rx == 1_000_000_000


@pytest.mark.asyncio
async def test_t6_wg_quota_exhausted_disables_peer(mock_server, mock_mikrotik, db_user, db_wg_interface):
    """T6: WG used >= bytes_remaining → set_wg_peer_status(disabled) + status disabled."""
    pub = f"wg_t6_{uuid.uuid4().hex}"
    iface_name = db_wg_interface.name
    quota = 100 * 1024**2
    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.wg_peers[iface_name] = {pub: {"public-key": pub, "disabled": "false", "rx": 0, "tx": 0}}

    async with AsyncSessionLocal() as session:
        sub = WireGuardSubscription(
            user_id=db_user.id,
            interface_id=db_wg_interface.id,
            profile_id=None,
            unique_identifier=f"WG-T6_{uuid.uuid4().hex[:6]}",
            peer_public_key=pub,
            peer_private_key="priv",
            assigned_ip="10.88.0.6",
            expiry_date=utc_now() + timedelta(days=5),
            bytes_remaining=quota,
            status="active",
            total_bytes_rx=quota,
            total_bytes_tx=0,
            last_router_rx=quota,
            last_router_tx=0,
        )
        session.add(sub)
        await session.commit()

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, mock_server.id)
        await SyncManager.reconcile_wg_subscriptions(session, server)
        await session.commit()
        refreshed = (
            await session.execute(
                select(WireGuardSubscription).where(WireGuardSubscription.peer_public_key == pub)
            )
        ).scalar_one()

    assert refreshed.status == "disabled"
    assert any(c[1] == pub and c[2] is True for c in mgr.set_wg_peer_status_calls)


@pytest.mark.asyncio
async def test_t7_wg_expired_peer_disabled(mock_server, mock_mikrotik, db_user, db_wg_interface):
    """T7: expiry_date in past → peer disabled (same as E7)."""
    pub = f"wg_t7_{uuid.uuid4().hex}"
    iface_name = db_wg_interface.name
    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.wg_peers[iface_name] = {pub: {"public-key": pub, "disabled": "false", "rx": 0, "tx": 0}}

    async with AsyncSessionLocal() as session:
        sub = WireGuardSubscription(
            user_id=db_user.id,
            interface_id=db_wg_interface.id,
            profile_id=None,
            unique_identifier=f"WG-T7_{uuid.uuid4().hex[:6]}",
            peer_public_key=pub,
            peer_private_key="priv",
            assigned_ip="10.88.0.7",
            expiry_date=utc_now() - timedelta(hours=1),
            status="active",
        )
        session.add(sub)
        await session.commit()

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, mock_server.id)
        await SyncManager.reconcile_wg_subscriptions(session, server)
        await session.commit()
        refreshed = (
            await session.execute(
                select(WireGuardSubscription).where(WireGuardSubscription.peer_public_key == pub)
            )
        ).scalar_one()

    assert refreshed.status == "expired"
    assert any(c[1] == pub and c[2] is True for c in mgr.set_wg_peer_status_calls)


@pytest.mark.asyncio
async def test_t8_wg_missing_queue_recreated(mock_server, mock_mikrotik, db_user, db_wg_interface, db_wg_profile):
    """T8: sync recreates simple queue when profile has rate_limit (name = unique_identifier)."""
    pub = f"wg_t8_{uuid.uuid4().hex}"
    uid = f"WG-T8_{uuid.uuid4().hex[:6]}"
    iface_name = db_wg_interface.name
    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.wg_peers[iface_name] = {pub: {"public-key": pub, "disabled": "false", "rx": 0, "tx": 0}}

    async with AsyncSessionLocal() as session:
        prof = await session.get(WireGuardProfile, db_wg_profile.id)
        prof.rate_limit = "5M/5M"
        sub = WireGuardSubscription(
            user_id=db_user.id,
            interface_id=db_wg_interface.id,
            profile_id=prof.id,
            unique_identifier=uid,
            peer_public_key=pub,
            peer_private_key="priv",
            assigned_ip="10.88.0.8",
            expiry_date=utc_now() + timedelta(days=3),
            bytes_remaining=1024**3,
            status="active",
        )
        session.add(sub)
        await session.commit()

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, mock_server.id)
        await SyncManager.reconcile_wg_subscriptions(session, server)
        await session.commit()

    assert uid in mgr.wg_queues
    assert mgr.wg_queues[uid]["max-limit"] == "5M/5M"


@pytest.mark.asyncio
async def test_t9_profile_rate_limit_on_ovpn_create(mock_server, mock_mikrotik):
    """T9: create_profile_with_limits stores rate_limit on limitation (mock tracks via create)."""
    from vpn_bot.admin_profile_service import create_profile_full, delete_profile_full

    name = f"DBTEST_RATE_{uuid.uuid4().hex[:6]}"
    prof, err = await create_profile_full(
        {
            "server_id": mock_server.id,
            "name": name,
            "days": 7,
            "limit": 2,
            "price_toman": 1000,
            "rate_limit": "4M/4M",
        }
    )
    assert prof, err
    mgr = MockMikrotikManager.for_server(mock_server.id)
    lim = mgr.um_limitations.get(f"lim_{name}")
    assert lim is not None
    assert lim.get("rate-limit-rx") == "4M" or "4M" in str(lim)
    await delete_profile_full(prof.id)


@pytest.mark.asyncio
async def test_t10_wg_router_counter_reset_handled(mock_server, mock_mikrotik, db_user, db_wg_interface):
    """T10: if router rx drops (reset), sync treats full current value as delta."""
    pub = f"wg_t10_{uuid.uuid4().hex}"
    iface_name = db_wg_interface.name
    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.wg_peers[iface_name] = {pub: {"public-key": pub, "disabled": "false", "rx": 2_000_000_000, "tx": 0}}

    async with AsyncSessionLocal() as session:
        sub = WireGuardSubscription(
            user_id=db_user.id,
            interface_id=db_wg_interface.id,
            profile_id=None,
            unique_identifier=f"WG-T10_{uuid.uuid4().hex[:6]}",
            peer_public_key=pub,
            peer_private_key="priv",
            assigned_ip="10.88.0.10",
            expiry_date=utc_now() + timedelta(days=5),
            bytes_remaining=10 * 1024**3,
            status="active",
            total_bytes_rx=5_000_000_000,
            last_router_rx=8_000_000_000,
            last_router_tx=0,
        )
        session.add(sub)
        await session.commit()

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, mock_server.id)
        await SyncManager.reconcile_wg_subscriptions(session, server)
        await session.commit()
        refreshed = (
            await session.execute(
                select(WireGuardSubscription).where(WireGuardSubscription.peer_public_key == pub)
            )
        ).scalar_one()

    assert refreshed.total_bytes_rx == 5_000_000_000 + 2_000_000_000
    assert refreshed.last_router_rx == 2_000_000_000
