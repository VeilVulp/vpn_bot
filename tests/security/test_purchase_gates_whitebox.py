"""D1–D7: Purchase gate white-box security tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.security.helpers.security_harness import UserSecurityDriver, build_user_application
from vpn_bot.admin_settings import set_admin_setting
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Profile
from vpn_bot.user_features import checkout_subscription, finalize_wg_purchase
from vpn_bot.utils import LanguageManager, get_profile_price

pytestmark = [pytest.mark.security, pytest.mark.db]


@pytest.mark.asyncio
async def test_d1_banned_user_ovpn_checkout_denied(banned_user, db_ovpn_profile, mock_mikrotik):
    ok, sub, err = await checkout_subscription(banned_user.telegram_id, db_ovpn_profile.id)
    assert ok is False
    assert sub is None


@pytest.mark.asyncio
async def test_d2_banned_user_wg_finalize_denied(banned_user, db_wg_profile, mock_mikrotik):
    ok = await finalize_wg_purchase(banned_user.telegram_id, db_wg_profile.id, context=None, is_tg_id=True)
    assert ok is False


@pytest.mark.asyncio
async def test_d3_insufficient_balance_purchase_denied(db_user_low_balance, db_ovpn_profile, mock_mikrotik):
    ok, sub, err = await checkout_subscription(db_user_low_balance.telegram_id, db_ovpn_profile.id)
    assert ok is False
    assert sub is None


@pytest.mark.asyncio
async def test_d4_inactive_profile_denied(db_user, mock_server, mock_mikrotik):
    async with AsyncSessionLocal() as session:
        prof = Profile(
            name="SEC_INACTIVE",
            price_usd=1.0,
            price_toman=5_000.0,
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
async def test_d5_unregistered_user_buy_redirects_to_reg(unregistered_user, mock_mikrotik):
    from tests.security.helpers.security_harness import UserSecurityDriver, build_user_application

    app, bot = await build_user_application()
    driver = UserSecurityDriver(app, bot, unregistered_user.telegram_id)
    with (
        patch("vpn_bot.utils.check_maintenance_status", new_callable=AsyncMock, return_value=(False, None)),
        patch("vpn_bot.bot_handler.check_sales_status", new_callable=AsyncMock, return_value=(False, None)),
    ):
        await driver.tap("buy_service")
    bucket = app.user_data.get(unregistered_user.telegram_id, {})
    texts = driver.all_outbound_text()
    reg_welcome = LanguageManager.get("buy.reg_welcome")
    assert (
        bucket.get("reg_next") == "buy"
        or reg_welcome in texts
        or reg_welcome[:20] in texts
    )


@pytest.mark.asyncio
async def test_d6_global_sales_off_checkout_denied(db_user, db_ovpn_profile, mock_mikrotik):
    await set_admin_setting("sales_global_active", "false")
    try:
        ok, sub, err = await checkout_subscription(db_user.telegram_id, db_ovpn_profile.id)
        assert ok is False
    finally:
        await set_admin_setting("sales_global_active", "true")


@pytest.mark.asyncio
async def test_d8_checkout_denied_without_terms_acceptance(db_user, db_ovpn_profile, mock_mikrotik):
    from vpn_bot.admin_settings_service import set_purchase_terms_enabled

    await set_purchase_terms_enabled(True)
    try:
        ok, sub, err = await checkout_subscription(db_user.telegram_id, db_ovpn_profile.id)
        assert ok is False
        assert sub is None
    finally:
        await set_purchase_terms_enabled(False)


@pytest.mark.asyncio
async def test_d7_wg_capacity_full_denied(db_user, db_wg_profile, mock_mikrotik):
    from vpn_bot.admin_sales_service import count_active_sales_slots
    from vpn_bot.admin_settings import set_admin_setting

    current = await count_active_sales_slots("wg")
    await set_admin_setting("sales_wg_limit", str(current))
    try:
        ok = await finalize_wg_purchase(db_user.telegram_id, db_wg_profile.id, context=None, is_tg_id=True)
        assert ok is False
    finally:
        await set_admin_setting("sales_wg_limit", "0")
