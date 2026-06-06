"""Renewal eligibility: volume, time, window, admin toggles."""

from datetime import timedelta

import pytest

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Profile, Subscription, WireGuardProfile, WireGuardSubscription
from vpn_bot.renewal_policy import (
    check_renewal_eligibility,
    ovpn_quota_exhausted,
    should_offer_renewal_button,
    subscription_time_expired,
    wg_quota_exhausted,
)
from vpn_bot.settings_utils import set_admin_setting
from vpn_bot.utils import utc_now

pytestmark = pytest.mark.db


@pytest.mark.asyncio
async def test_quota_exhausted_allows_renew_despite_window(mock_server, db_user):
    """Volume full + many days left → renew allowed (not blocked by window)."""
    await set_admin_setting("sales_um_renew_active", True)
    await set_admin_setting("sales_renew_strict_window", True)
    await set_admin_setting("sales_renew_window_days", "3d")

    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=None,
            mikrotik_username="u_quota_renew",
            mikrotik_password="x",
            expiry_date=utc_now() + timedelta(days=20),
            status="disabled",
            used_bytes=2 * 1024**3,
            total_limit_bytes=2 * 1024**3,
        )
        session.add(sub)
        await session.commit()

    allowed, msg = await check_renewal_eligibility(sub, "um")
    assert allowed is True
    assert msg is None
    assert ovpn_quota_exhausted(sub) is True


@pytest.mark.asyncio
async def test_expired_allows_renew(mock_server, db_user):
    await set_admin_setting("sales_um_renew_active", True)
    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=None,
            mikrotik_username="u_exp_renew",
            mikrotik_password="x",
            expiry_date=utc_now() - timedelta(days=1),
            status="expired",
            used_bytes=0,
            total_limit_bytes=1024**3,
        )
        session.add(sub)
        await session.commit()

    allowed, _ = await check_renewal_eligibility(sub, "um")
    assert allowed is True
    assert subscription_time_expired(sub) is True


@pytest.mark.asyncio
async def test_early_renew_blocked_inside_window(mock_server, db_user):
    """Active sub with volume+time → blocked until window."""
    await set_admin_setting("sales_um_renew_active", True)
    await set_admin_setting("sales_renew_strict_window", True)
    await set_admin_setting("sales_renew_window_days", "3d")

    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=None,
            mikrotik_username="u_early",
            mikrotik_password="x",
            expiry_date=utc_now() + timedelta(days=10),
            status="active",
            used_bytes=100,
            total_limit_bytes=5 * 1024**3,
        )
        session.add(sub)
        await session.commit()

    allowed, msg = await check_renewal_eligibility(sub, "um")
    assert allowed is False
    assert msg is not None


@pytest.mark.asyncio
async def test_strict_window_off_allows_early_renew(mock_server, db_user):
    await set_admin_setting("sales_um_renew_active", True)
    await set_admin_setting("sales_renew_strict_window", False)

    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=None,
            mikrotik_username="u_free_early",
            mikrotik_password="x",
            expiry_date=utc_now() + timedelta(days=30),
            status="active",
            used_bytes=0,
            total_limit_bytes=5 * 1024**3,
        )
        session.add(sub)
        await session.commit()

    allowed, _ = await check_renewal_eligibility(sub, "um")
    assert allowed is True


@pytest.mark.asyncio
async def test_wg_quota_exhausted(mock_server, db_user, db_wg_interface, db_wg_profile):
    await set_admin_setting("sales_wg_renew_active", True)
    async with AsyncSessionLocal() as session:
        sub = WireGuardSubscription(
            user_id=db_user.id,
            interface_id=db_wg_interface.id,
            profile_id=db_wg_profile.id,
            unique_identifier="WG-Q",
            peer_public_key="pk_q",
            peer_private_key="priv",
            assigned_ip="10.1.0.2",
            expiry_date=utc_now() + timedelta(days=15),
            bytes_remaining=1024**3,
            total_bytes_rx=1024**3,
            total_bytes_tx=0,
            status="disabled",
        )
        session.add(sub)
        await session.commit()
        prof = await session.get(WireGuardProfile, db_wg_profile.id)

    assert wg_quota_exhausted(sub, prof) is True
    allowed, _ = await check_renewal_eligibility(sub, "wg", profile=prof)
    assert allowed is True
    assert await should_offer_renewal_button(sub, "wg", profile=prof) is True


@pytest.mark.asyncio
async def test_renew_disabled_blocks(mock_server, db_user):
    await set_admin_setting("sales_um_renew_active", False)
    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=None,
            mikrotik_username="u_off",
            mikrotik_password="x",
            expiry_date=utc_now() - timedelta(days=1),
            status="expired",
        )
        session.add(sub)
        await session.commit()

    allowed, msg = await check_renewal_eligibility(sub, "um")
    assert allowed is False
    assert msg is not None
