"""Abuse scenarios A1–A6 (mock MT)."""

import asyncio

import pytest

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import User
from vpn_bot.user_features import checkout_subscription, finalize_wg_purchase
from vpn_bot.utils import get_profile_price

pytestmark = pytest.mark.db


@pytest.mark.asyncio
async def test_a1_safety_margin_does_not_allow_partial_price_purchase(
    db_user_low_balance, db_ovpn_profile, mock_mikrotik
):
    """V1 fix: balance within 100 of price but below price must not complete purchase."""
    price = await get_profile_price(db_ovpn_profile)
    async with AsyncSessionLocal() as session:
        u = await session.get(User, db_user_low_balance.id)
        u.wallet_balance = price - 50
        await session.commit()

    ok, sub, err = await checkout_subscription(db_user_low_balance.telegram_id, db_ovpn_profile.id)
    assert ok is False
    assert sub is None


@pytest.mark.asyncio
async def test_a4_banned_user_blocked(db_user_factory, db_ovpn_profile, mock_mikrotik):
    banned = await db_user_factory(balance=1_000_000.0, banned=True)
    ok, sub, err = await checkout_subscription(banned.telegram_id, db_ovpn_profile.id)
    assert ok is False


@pytest.mark.asyncio
async def test_a5_inactive_profile_blocked(db_user, mock_server, mock_mikrotik):
    from vpn_bot.models import Profile

    async with AsyncSessionLocal() as session:
        prof = Profile(
            name="INACTIVE_PROF",
            price_usd=1.0,
            price_toman=5000.0,
            validity_days=1,
            data_limit_gb=1,
            server_id=mock_server.id,
            is_active=False,
        )
        session.add(prof)
        await session.commit()
        await session.refresh(prof)
        pid = prof.id

    ok, sub, err = await checkout_subscription(db_user.telegram_id, pid)
    assert ok is False


@pytest.mark.asyncio
async def test_a6_race_deposit_and_purchase(db_user, db_ovpn_profile, mock_mikrotik):
    from vpn_bot.wallet_manager import WalletManager

    price = await get_profile_price(db_ovpn_profile)
    async with AsyncSessionLocal() as session:
        u = await session.get(User, db_user.id)
        u.wallet_balance = price
        await session.commit()

    results = await asyncio.gather(
        WalletManager.deposit(db_user.id, 100_000.0, "race deposit"),
        checkout_subscription(db_user.telegram_id, db_ovpn_profile.id),
        return_exceptions=True,
    )

    async with AsyncSessionLocal() as session:
        u = await session.get(User, db_user.id)
        assert u.wallet_balance >= 0


@pytest.mark.asyncio
async def test_banned_wg_purchase(db_user_factory, db_wg_profile, db_wg_interface, mock_mikrotik):
    banned = await db_user_factory(balance=1_000_000.0, banned=True)
    ok = await finalize_wg_purchase(banned.telegram_id, db_wg_profile.id, context=None, is_tg_id=True)
    assert ok is False
