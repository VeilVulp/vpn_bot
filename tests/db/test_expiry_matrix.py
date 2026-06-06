"""Expiry scenarios E1–E8 (mock MikroTik)."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from vpn_bot.admin_cleanup import AdminCleanup
from vpn_bot.admin_subscription_service import extend_subscription_validity
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Server, Subscription, WireGuardSubscription
from vpn_bot.sync_manager import SyncManager
from tests.conftest_db import FakeBot
from tests.helpers.mock_mikrotik import MockMikrotikManager
from vpn_bot.utils import utc_now

pytestmark = pytest.mark.db


def _uname(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_e1_sync_marks_expired_ovpn(mock_server, mock_mikrotik, db_user):
    """E1: expired subscription → status expired + disable_user called."""
    uname = _uname("u_exp_e1")
    now = utc_now()
    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=None,
            mikrotik_username=uname,
            mikrotik_password="x",
            expiry_date=now - timedelta(hours=1),
            status="active",
        )
        session.add(sub)
        await session.commit()

    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.um_users[uname] = {"name": uname, "disabled": "false"}

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, mock_server.id)
        n = await SyncManager.reconcile_ovpn_subscriptions(session, server)
        await session.commit()
        refreshed = (
            await session.execute(select(Subscription).where(Subscription.mikrotik_username == uname))
        ).scalar_one()

    assert refreshed.status == "expired"
    assert uname in mgr.disable_user_calls


@pytest.mark.asyncio
async def test_e2_aware_expiry_detected_with_utc_now(mock_server, mock_mikrotik, db_user):
    """E2: timezone-aware expiry in the past is handled by utc_now() in sync."""
    uname = _uname("u_exp_e2")
    now = utc_now()
    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=None,
            mikrotik_username=uname,
            mikrotik_password="x",
            expiry_date=now - timedelta(minutes=30),
            status="active",
        )
        session.add(sub)
        await session.commit()

    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.um_users[uname] = {"name": uname, "disabled": "false"}

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, mock_server.id)
        await SyncManager.reconcile_ovpn_subscriptions(session, server)
        await session.commit()
        refreshed = (
            await session.execute(select(Subscription).where(Subscription.mikrotik_username == uname))
        ).scalar_one()

    assert refreshed.status == "expired"


@pytest.mark.asyncio
async def test_e3_cleanup_warn_sets_deletion_warning(mock_server, mock_mikrotik, db_user):
    """E3: warn mode sets deletion_warning_sent_at when bot is provided."""
    uname = _uname("u_warn_e3")
    now = utc_now()
    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=None,
            mikrotik_username=uname,
            mikrotik_password="x",
            expiry_date=now - timedelta(days=5),
            status="expired",
            deletion_warning_sent_at=None,
        )
        session.add(sub)
        await session.commit()
        sid = sub.id

    count = await AdminCleanup.clean_expired_subscriptions(bot=FakeBot(), mode="warn", seconds=0)
    assert count >= 1

    async with AsyncSessionLocal() as session:
        refreshed = await session.get(Subscription, sid)
        assert refreshed.deletion_warning_sent_at is not None


@pytest.mark.asyncio
async def test_e4_cleanup_delete_after_warning(mock_server, mock_mikrotik, db_user):
    """E4: delete mode removes sub warned 24h+ ago."""
    uname = _uname("u_del_e4")
    now = utc_now()
    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=None,
            mikrotik_username=uname,
            mikrotik_password="x",
            expiry_date=now - timedelta(days=10),
            status="expired",
            deletion_warning_sent_at=now - timedelta(hours=25),
        )
        session.add(sub)
        await session.commit()
        uname = sub.mikrotik_username

    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.um_users[uname] = {"name": uname, "disabled": "true"}

    deleted = await AdminCleanup.clean_expired_subscriptions(bot=None, mode="delete", seconds=0)
    assert deleted >= 1

    async with AsyncSessionLocal() as session:
        gone = (
            await session.execute(select(Subscription).where(Subscription.mikrotik_username == uname))
        ).scalar_one_or_none()
    assert gone is None
    assert uname in mgr.delete_user_calls or uname in mgr.disable_user_calls


@pytest.mark.asyncio
async def test_e5_admin_extend_validity(mock_server, mock_mikrotik, db_user):
    """E5: extend_subscription_validity moves expiry forward."""
    uname = _uname("u_ext_e5")
    now = utc_now()
    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.um_users[uname] = {"name": uname, "disabled": "false"}
    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=None,
            mikrotik_username=uname,
            mikrotik_password="x",
            expiry_date=now + timedelta(days=1),
            status="active",
        )
        session.add(sub)
        await session.commit()
        before = sub.expiry_date

    ok, new_exp = await extend_subscription_validity(uname, 7)
    assert ok is True
    from datetime import timezone

    before_cmp = before if before.tzinfo else before.replace(tzinfo=timezone.utc)
    new_cmp = new_exp if new_exp.tzinfo else new_exp.replace(tzinfo=timezone.utc)
    assert new_cmp > before_cmp


@pytest.mark.asyncio
async def test_e7_wg_expired_peer_disabled(mock_server, mock_mikrotik, db_user, db_wg_interface):
    """E7: reconcile_wg marks expired and disables peer."""
    now = utc_now()
    pub = f"wg_pub_e7_{uuid.uuid4().hex}"
    async with AsyncSessionLocal() as session:
        sub = WireGuardSubscription(
            user_id=db_user.id,
            interface_id=db_wg_interface.id,
            profile_id=None,
            unique_identifier=f"WG-e7_{uuid.uuid4().hex[:8]}",
            peer_public_key=pub,
            peer_private_key="priv",
            assigned_ip="10.88.0.2",
            expiry_date=now - timedelta(hours=2),
            status="active",
        )
        session.add(sub)
        await session.commit()

    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.wg_peers[db_wg_interface.name] = {pub: {"public-key": pub, "disabled": "false"}}

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


@pytest.mark.asyncio
async def test_e8_expired_not_reenabled_as_active(mock_server, mock_mikrotik, db_user):
    """E8: expired sub stays expired even if router shows enabled."""
    uname = _uname("u_e8")
    now = utc_now()
    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=None,
            mikrotik_username=uname,
            mikrotik_password="x",
            expiry_date=now - timedelta(days=1),
            status="expired",
        )
        session.add(sub)
        await session.commit()

    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.um_users[uname] = {"name": uname, "disabled": "false"}

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, mock_server.id)
        await SyncManager.reconcile_ovpn_subscriptions(session, server)
        await session.commit()
        refreshed = (
            await session.execute(select(Subscription).where(Subscription.mikrotik_username == uname))
        ).scalar_one()

    assert refreshed.status == "expired"
