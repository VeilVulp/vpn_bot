"""Security: banned users cannot purchase via service layer."""

import pytest
from sqlalchemy import select

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import User
from vpn_bot.user_features import checkout_subscription, finalize_wg_purchase

pytestmark = pytest.mark.live_mt


@pytest.fixture
async def banned_user_with_profile(live_server):
    from vpn_bot.admin_profile_service import create_profile_full, delete_profile_full
    from vpn_bot.admin_wg_service import create_wg_profile, delete_wg_profile

    tg = 880003003
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.telegram_id == tg))
        user = res.scalars().first()
        if not user:
            user = User(telegram_id=tg, username="banned_test", wallet_balance=1_000_000)
            session.add(user)
        user.is_banned = True
        user.wallet_balance = 1_000_000
        await session.commit()
        await session.refresh(user)

    ovpn, err = await create_profile_full(
        {
            "server_id": live_server.id,
            "name": f"INTG_BAN_OVPN_{tg}",
            "days": 1,
            "limit": 1,
            "price_toman": 100,
        }
    )
    assert ovpn, err
    wg = await create_wg_profile(
        {
            "name": f"INTG_BAN_WG_{tg}",
            "volume": 1,
            "days": 1,
            "price_toman": 100,
            "server_id": live_server.id,
        }
    )
    assert wg
    yield user, ovpn, wg
    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(select(User).where(User.telegram_id == tg))
        ).scalars().first()
        if row:
            row.is_banned = False
            await session.commit()
    await delete_profile_full(ovpn.id)
    await delete_wg_profile(wg.id)


@pytest.mark.asyncio
async def test_checkout_rejects_banned_user(banned_user_with_profile):
    user, ovpn, _ = banned_user_with_profile
    ok, sub, err = await checkout_subscription(user.telegram_id, ovpn.id)
    assert ok is False
    assert sub is None
    err_text = err if isinstance(err, str) else str(err)
    assert "suspended" in err_text.lower() or "مسدود" in err_text


@pytest.mark.asyncio
async def test_wg_purchase_rejects_banned_user(banned_user_with_profile):
    user, _, wg = banned_user_with_profile
    ok = await finalize_wg_purchase(user.telegram_id, wg.id, context=None, is_tg_id=True)
    assert ok is False
