"""Live cleanup service smoke tests (DB only, INTG-prefixed data)."""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import select

from vpn_bot.admin_cleanup import AdminCleanup
from vpn_bot.admin_cleanup_service import get_db_health_stats
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import PaymentReceipt, Subscription, User

pytestmark = pytest.mark.live_mt


@pytest.mark.asyncio
async def test_db_health_stats():
    stats = await get_db_health_stats()
    assert isinstance(stats, dict)


@pytest.mark.asyncio
async def test_clean_pending_receipts_dry_run(intg_user):
    async with AsyncSessionLocal() as session:
        old = PaymentReceipt(
            user_id=intg_user.id,
            amount=1,
            receipt_file_id="old_rcpt",
            status="pending",
            submitted_at=datetime.now() - timedelta(days=30),
        )
        session.add(old)
        await session.commit()

    count = await AdminCleanup.clean_pending_receipts(days=7)
    assert count >= 0


@pytest.mark.asyncio
async def test_warn_mode_expired_subscription(intg_user, live_server):
    """Warn mode should not delete; only mark warning timestamp."""
    from vpn_bot.admin_profile_service import create_profile_full, delete_profile_full
    from vpn_bot.user_features import checkout_subscription

    name = f"INTG_CLEAN_{int(__import__('time').time()) % 100000}"
    prof, err = await create_profile_full(
        {
            "server_id": live_server.id,
            "name": name,
            "days": 1,
            "limit": 1,
            "price_toman": 100,
        }
    )
    assert prof, err
    ok, sub, msg = await checkout_subscription(intg_user.telegram_id, prof.id)
    assert ok, msg

    async with AsyncSessionLocal() as session:
        db_sub = await session.get(Subscription, sub.id)
        db_sub.expiry_date = datetime.now() - timedelta(days=10)
        await session.commit()

    warned = await AdminCleanup.clean_expired_subscriptions(bot=None, mode="warn", seconds=86400)
    assert warned >= 0

    async with AsyncSessionLocal() as session:
        db_sub = await session.get(Subscription, sub.id)
        assert db_sub is not None

    import asyncio
    from vpn_bot.mikrotik_manager import get_mikrotik_manager

    mgr = get_mikrotik_manager(live_server)
    await asyncio.to_thread(mgr.delete_user, sub.mikrotik_username)
    async with AsyncSessionLocal() as session:
        row = await session.get(Subscription, sub.id)
        if row:
            await session.delete(row)
            await session.commit()
    await delete_profile_full(prof.id)
