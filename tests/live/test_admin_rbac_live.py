"""
Optional live check: DB admin telegram id with limited permissions in database.

Set ADMIN_E2E_DB_ADMIN_TG to a user that exists in ``admins`` table with limited perms.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import select

from tests.helpers.admin_e2e_harness import AdminE2EDriver, build_admin_application
from vpn_bot.admin_management import get_admin_permissions, limited_permission_preset
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Admin

pytestmark = [pytest.mark.live_admin_rbac]


@pytest.mark.asyncio
async def test_live_db_admin_menu_matches_db_permissions(db_initialized):
    tg = os.getenv("ADMIN_E2E_DB_ADMIN_TG")
    if not tg:
        pytest.skip("Set ADMIN_E2E_DB_ADMIN_TG for live DB admin RBAC test")

    telegram_id = int(tg)
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Admin).where(Admin.telegram_id == telegram_id))
        admin = res.scalars().first()
    if not admin:
        pytest.skip(f"No admins row for telegram_id={telegram_id}")

    perms = await get_admin_permissions(telegram_id)
    app, bot, _ = await build_admin_application(include_user_handlers=False)
    driver = AdminE2EDriver(
        app,
        bot,
        telegram_id,
        permissions=perms,
        super_admin=False,
    )
    await driver.open_admin_menu()
    on_screen = set(driver.callbacks_on_screen())
    assert "admin_mgmt_menu" not in on_screen
    if perms <= limited_permission_preset() or perms == limited_permission_preset():
        assert "wg_mgmt_menu" not in on_screen
    assert not driver.errors
