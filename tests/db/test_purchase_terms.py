"""Purchase terms policy: once vs every_purchase modes and checkout gates."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select, update

from vpn_bot.admin_settings_service import (
    PURCHASE_TERMS_MODE_EVERY,
    PURCHASE_TERMS_MODE_ONCE,
    set_purchase_terms_enabled,
    set_purchase_terms_mode,
    set_purchase_terms_text,
)
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import User
from vpn_bot.purchase_terms import (
    TERMS_SESSION_KEY,
    assert_terms_accepted_for_checkout,
    record_terms_acceptance,
    user_needs_terms,
)
from vpn_bot.settings_utils import set_admin_setting
from vpn_bot.user_features import checkout_subscription

pytestmark = [pytest.mark.db]


@pytest.fixture
async def terms_enabled():
    await set_purchase_terms_enabled(True)
    await set_admin_setting("purchase_terms_version", "1")
    yield
    await set_purchase_terms_enabled(False)


def _mock_context():
    ctx = MagicMock()
    ctx.user_data = {}
    return ctx


@pytest.mark.asyncio
async def test_once_mode_skips_after_accept(db_user, terms_enabled):
    await set_purchase_terms_mode(PURCHASE_TERMS_MODE_ONCE)
    await record_terms_acceptance(db_user.telegram_id)

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.id == db_user.id))
        user = res.scalars().first()

    assert await user_needs_terms(user, _mock_context()) is False


@pytest.mark.asyncio
async def test_once_mode_reprompt_on_version_change(db_user, terms_enabled):
    await set_purchase_terms_mode(PURCHASE_TERMS_MODE_ONCE)
    await record_terms_acceptance(db_user.telegram_id)
    await set_purchase_terms_text("Updated terms v2")

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.id == db_user.id))
        user = res.scalars().first()

    assert await user_needs_terms(user, _mock_context()) is True


@pytest.mark.asyncio
async def test_every_mode_uses_session_flag(db_user, terms_enabled):
    await set_purchase_terms_mode(PURCHASE_TERMS_MODE_EVERY)

    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.id == db_user.id))
        user = res.scalars().first()

    ctx = _mock_context()
    assert await user_needs_terms(user, ctx) is True
    ctx.user_data[TERMS_SESSION_KEY] = True
    assert await user_needs_terms(user, ctx) is False


@pytest.mark.asyncio
async def test_checkout_denied_without_terms(db_user, db_ovpn_profile, mock_mikrotik, terms_enabled):
    await set_purchase_terms_mode(PURCHASE_TERMS_MODE_ONCE)

    ok, sub, err = await checkout_subscription(db_user.telegram_id, db_ovpn_profile.id)
    assert ok is False
    assert sub is None
    assert err is not None


@pytest.mark.asyncio
async def test_checkout_allowed_after_terms_accept(db_user, db_ovpn_profile, mock_mikrotik, terms_enabled):
    await set_purchase_terms_mode(PURCHASE_TERMS_MODE_ONCE)
    await record_terms_acceptance(db_user.telegram_id)

    ok, sub, err = await checkout_subscription(db_user.telegram_id, db_ovpn_profile.id)
    assert ok is True
    assert sub is not None


@pytest.mark.asyncio
async def test_every_mode_checkout_requires_recent_acceptance(db_user, terms_enabled):
    await set_purchase_terms_mode(PURCHASE_TERMS_MODE_EVERY)

    stale = datetime.now(timezone.utc) - timedelta(hours=2)
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User)
            .where(User.id == db_user.id)
            .values(purchase_terms_accepted_at=stale, purchase_terms_version="1")
        )
        await session.commit()
        res = await session.execute(select(User).where(User.id == db_user.id))
        user = res.scalars().first()

    ok, err = await assert_terms_accepted_for_checkout(user)
    assert ok is False
    assert err is not None
