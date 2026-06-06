"""Sales capacity limits apply to new purchases only, not renewals."""

import uuid
from datetime import timedelta

import pytest
from sqlalchemy import select

from vpn_bot.admin_sales_service import (
    apply_sales_capacity_delta,
    assert_new_purchase_capacity,
    coerce_sales_bool,
    coerce_sales_limit,
    count_active_sales_slots,
    is_new_purchase_capacity_available,
    sales_capacity_remaining,
)
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Subscription, WireGuardSubscription
from vpn_bot.renewal_policy import check_renewal_eligibility
from vpn_bot.settings_utils import set_admin_setting
from vpn_bot.user_features import checkout_subscription
from vpn_bot.utils import utc_now

pytestmark = pytest.mark.db


@pytest.mark.asyncio
async def test_capacity_blocks_when_at_limit(mock_server, db_user, db_ovpn_profile, mock_mikrotik):
    await set_admin_setting("sales_ovpn_limit", 2)
    async with AsyncSessionLocal() as session:
        for i in range(2):
            session.add(
                Subscription(
                    user_id=db_user.id,
                    server_id=mock_server.id,
                    profile_id=db_ovpn_profile.id,
                    mikrotik_username=f"cap_{i}_{uuid.uuid4().hex[:6]}",
                    mikrotik_password="x",
                    expiry_date=utc_now() + timedelta(days=5),
                    status="active",
                )
            )
        await session.commit()

    allowed, count, limit = await is_new_purchase_capacity_available("ovpn")
    assert limit == 2
    assert count >= 2
    assert allowed is False

    ok, msg = await assert_new_purchase_capacity("ovpn")
    assert ok is False
    assert msg is not None


@pytest.mark.asyncio
async def test_renewal_not_blocked_by_sales_cap(mock_server, db_user, mock_mikrotik):
    await set_admin_setting("sales_ovpn_limit", 1)
    await set_admin_setting("sales_um_renew_active", True)
    uname = f"renew_cap_{uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=db_user.id,
            server_id=mock_server.id,
            profile_id=None,
            mikrotik_username=uname,
            mikrotik_password="x",
            expiry_date=utc_now() - timedelta(days=1),
            status="expired",
            total_limit_bytes=1024**3,
        )
        session.add(sub)
        await session.commit()

    allowed, _count, _limit = await is_new_purchase_capacity_available("ovpn")
    assert allowed is False

    eligible, block = await check_renewal_eligibility(sub, "um")
    assert eligible is True
    assert block is None


@pytest.mark.asyncio
async def test_checkout_respects_capacity(
    mock_server, db_user_factory, db_ovpn_profile, mock_mikrotik
):
    await set_admin_setting("sales_ovpn_limit", 1)
    user = await db_user_factory(balance=5_000_000)
    async with AsyncSessionLocal() as session:
        session.add(
            Subscription(
                user_id=user.id,
                server_id=mock_server.id,
                profile_id=db_ovpn_profile.id,
                mikrotik_username=f"filled_{uuid.uuid4().hex[:6]}",
                mikrotik_password="x",
                expiry_date=utc_now() + timedelta(days=3),
                status="active",
            )
        )
        await session.commit()

    ok, _sub, err = await checkout_subscription(user.telegram_id, db_ovpn_profile.id)
    assert ok is False
    assert err is not None


@pytest.mark.asyncio
async def test_disabled_subs_not_counted_toward_cap(mock_server, db_user, mock_mikrotik):
    before = await count_active_sales_slots("ovpn")
    async with AsyncSessionLocal() as session:
        session.add(
            Subscription(
                user_id=db_user.id,
                server_id=mock_server.id,
                profile_id=None,
                mikrotik_username=f"dis_{uuid.uuid4().hex[:6]}",
                mikrotik_password="x",
                expiry_date=utc_now() + timedelta(days=5),
                status="disabled",
            )
        )
        await session.commit()

    after = await count_active_sales_slots("ovpn")
    assert after == before


@pytest.mark.asyncio
async def test_expired_active_not_counted_toward_cap(mock_server, db_user, mock_mikrotik):
    before = await count_active_sales_slots("ovpn")
    async with AsyncSessionLocal() as session:
        session.add(
            Subscription(
                user_id=db_user.id,
                server_id=mock_server.id,
                profile_id=None,
                mikrotik_username=f"exp_{uuid.uuid4().hex[:6]}",
                mikrotik_password="x",
                expiry_date=utc_now() - timedelta(days=1),
                status="active",
            )
        )
        await session.commit()

    after = await count_active_sales_slots("ovpn")
    assert after == before


@pytest.mark.asyncio
async def test_capacity_at_limit_blocks_new_purchase(mock_server, db_user, db_ovpn_profile, mock_mikrotik):
    """Simulates 29 active with cap 3: any count >= limit must block."""
    before = await count_active_sales_slots("ovpn")
    added = 29
    async with AsyncSessionLocal() as session:
        for i in range(added):
            session.add(
                Subscription(
                    user_id=db_user.id,
                    server_id=mock_server.id,
                    profile_id=db_ovpn_profile.id,
                    mikrotik_username=f"ovpn29_{i}_{uuid.uuid4().hex[:4]}",
                    mikrotik_password="x",
                    expiry_date=utc_now() + timedelta(days=5),
                    status="active",
                )
            )
        await session.commit()

    count = await count_active_sales_slots("ovpn")
    assert count == before + added
    await set_admin_setting("sales_ovpn_limit", 3)

    allowed, count2, limit = await is_new_purchase_capacity_available("ovpn")
    assert limit == 3
    assert count2 == count
    assert count2 >= 29
    assert allowed is False


@pytest.mark.asyncio
async def test_capacity_above_active_allows_new_purchase(mock_server, db_user, db_ovpn_profile, mock_mikrotik):
    """Simulates 29 active with cap 32: three new purchase slots remain."""
    before = await count_active_sales_slots("ovpn")
    added = 29
    async with AsyncSessionLocal() as session:
        for i in range(added):
            session.add(
                Subscription(
                    user_id=db_user.id,
                    server_id=mock_server.id,
                    profile_id=db_ovpn_profile.id,
                    mikrotik_username=f"ovpn32_{i}_{uuid.uuid4().hex[:4]}",
                    mikrotik_password="x",
                    expiry_date=utc_now() + timedelta(days=5),
                    status="active",
                )
            )
        await session.commit()

    count = await count_active_sales_slots("ovpn")
    assert count == before + added
    await set_admin_setting("sales_ovpn_limit", count + 3)

    allowed, count2, limit = await is_new_purchase_capacity_available("ovpn")
    assert limit == count + 3
    assert count2 == count
    assert sales_capacity_remaining(count2, limit) == 3
    assert allowed is True


@pytest.mark.asyncio
async def test_unlimited_capacity_always_allows(mock_server, db_user, mock_mikrotik):
    await set_admin_setting("sales_wg_limit", 0)
    allowed, count, limit = await is_new_purchase_capacity_available("wg")
    assert limit == 0
    assert allowed is True
    ok, msg = await assert_new_purchase_capacity("wg")
    assert ok is True
    assert msg is None


@pytest.mark.asyncio
async def test_apply_sales_capacity_delta_adds_slots(mock_server, db_user, db_ovpn_profile, mock_mikrotik):
    before = await count_active_sales_slots("ovpn")
    added = 5
    async with AsyncSessionLocal() as session:
        for i in range(added):
            session.add(
                Subscription(
                    user_id=db_user.id,
                    server_id=mock_server.id,
                    profile_id=db_ovpn_profile.id,
                    mikrotik_username=f"delta_{i}_{uuid.uuid4().hex[:4]}",
                    mikrotik_password="x",
                    expiry_date=utc_now() + timedelta(days=5),
                    status="active",
                )
            )
        await session.commit()

    count = await count_active_sales_slots("ovpn")
    assert count == before + added
    delta = 3
    new_limit = await apply_sales_capacity_delta("ovpn", delta)
    assert new_limit == count + delta
    allowed, _, limit = await is_new_purchase_capacity_available("ovpn")
    assert limit == count + delta
    assert allowed is True


def test_coerce_sales_limit_and_bool():
    assert coerce_sales_limit("32") == 32
    assert coerce_sales_limit(0) == 0
    assert coerce_sales_limit(None) == 0
    assert coerce_sales_limit("bad") == 0
    assert coerce_sales_bool("false") is False
    assert coerce_sales_bool("true") is True
    assert coerce_sales_bool(True) is True


def test_sales_capacity_remaining():
    assert sales_capacity_remaining(29, 32) == 3
    assert sales_capacity_remaining(29, 3) == 0
    assert sales_capacity_remaining(10, 0) == "∞"
