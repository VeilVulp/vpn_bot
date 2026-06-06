"""V2: concurrent checkout on same user."""

import asyncio

import pytest
from sqlalchemy import select

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Subscription, User
from tests.db.helpers.invariant_checker import check_no_negative_balances, run_all_checks
from vpn_bot.user_features import checkout_subscription
from vpn_bot.utils import get_profile_price

pytestmark = pytest.mark.db


@pytest.mark.asyncio
async def test_a2_double_checkout_same_user(db_user, db_ovpn_profile, mock_mikrotik):
    from vpn_bot.settings_utils import set_admin_setting

    await set_admin_setting("sales_ovpn_limit", 0)
    price = await get_profile_price(db_ovpn_profile)
    async with AsyncSessionLocal() as session:
        u = await session.get(User, db_user.id)
        u.wallet_balance = price * 1.5
        await session.commit()

    results = await asyncio.gather(
        checkout_subscription(db_user.telegram_id, db_ovpn_profile.id),
        checkout_subscription(db_user.telegram_id, db_ovpn_profile.id),
        return_exceptions=True,
    )
    ok_count = sum(1 for r in results if isinstance(r, tuple) and r[0] is True)

    async with AsyncSessionLocal() as session:
        subs = (
            await session.execute(select(Subscription).where(Subscription.user_id == db_user.id))
        ).scalars().all()
        u = await session.get(User, db_user.id)

    assert ok_count == 1, f"Expected exactly one successful checkout, got {ok_count}: {results}"
    assert u.wallet_balance >= 0
    assert len(subs) == 1
    assert await check_no_negative_balances([db_user.id]) == []

    await run_all_checks(user_ids=[db_user.id])
