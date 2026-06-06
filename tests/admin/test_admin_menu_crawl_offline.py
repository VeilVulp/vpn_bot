"""Offline BFS crawl of admin menus for multiple RBAC profiles."""

from __future__ import annotations

import pytest

from tests.helpers.admin_e2e_harness import AdminE2EDriver, build_admin_application
from vpn_bot.admin_management import full_permission_preset, limited_permission_preset
from vpn_bot.config import config

pytestmark = pytest.mark.asyncio


async def _driver(super_admin: bool, permissions: set[str] | None):
    app, bot, _ = await build_admin_application(include_user_handlers=False)
    uid = int(config.ADMIN_IDS[0]) if config.ADMIN_IDS else 900001
    return AdminE2EDriver(
        app,
        bot,
        uid if super_admin else uid + 100_000,
        permissions=permissions,
        super_admin=super_admin,
    )


@pytest.mark.asyncio
async def test_bfs_crawl_super_profile():
    if not config.ADMIN_IDS:
        pytest.skip("ADMIN_IDS not set")
    driver = await _driver(True, full_permission_preset())
    result = await driver.bfs_crawl(max_depth=2)
    assert not driver.errors, driver.errors
    assert len(result.taps) >= 8


@pytest.mark.asyncio
async def test_bfs_crawl_db_full_profile():
    driver = await _driver(False, full_permission_preset())
    result = await driver.crawl_allowed_only(max_depth=2)
    assert not driver.errors, driver.errors
    assert "admin_mgmt_menu" not in result.taps
    assert len(result.taps) >= 3


@pytest.mark.asyncio
async def test_bfs_crawl_db_limited_profile():
    limited = limited_permission_preset()
    driver = await _driver(False, limited)
    result = await driver.crawl_allowed_only(max_depth=1)
    assert not driver.errors, driver.errors
    assert "wg_mgmt_menu" not in result.taps
    assert "admin_mgmt_menu" not in result.taps
    assert any(cb in result.taps for cb in ("search_user", "pending_receipts", "admin_tickets"))
