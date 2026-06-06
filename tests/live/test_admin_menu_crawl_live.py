"""BFS crawl of documented admin menus via Application.process_update."""

from __future__ import annotations

import pytest

from tests.helpers.admin_e2e_harness import should_skip_bfs_callback
from vpn_bot.config import config

pytest_plugins = ["tests.live.admin_e2e_conftest"]

pytestmark = [pytest.mark.live_mt, pytest.mark.live_admin_e2e]


@pytest.mark.asyncio
async def test_bfs_admin_menu_crawl(admin_driver):
    """Tap through admin menu graph without unhandled handler errors."""
    result = await admin_driver.bfs_crawl(max_depth=2)
    assert not admin_driver.errors, f"Handler errors: {admin_driver.errors}"
    assert len(result.taps) >= 10, f"Expected many taps, got {len(result.taps)}"
    assert len(result.errors) == 0, f"BFS errors: {result.errors}"


@pytest.mark.asyncio
async def test_documented_callbacks_reachable(admin_driver):
    """Each MENU_TREE submenu exposes its documented child buttons on screen."""
    from vpn_bot.admin_menu import MENU_TREE

    await admin_driver.open_admin_menu()
    top_level = set(MENU_TREE["admin_start"])
    on_main = set(admin_driver.callbacks_on_screen())
    assert top_level <= on_main | {"admin_mgmt_menu"}, (
        f"Missing top-level buttons: {top_level - on_main}"
    )

    optional_children = {
        "settings_connection",
        "manage_ovpn",
        "backup_set_interval",
        "backup_export",
        "backup_import",
        "renew_mgmt_menu",
        "sales_capacity_menu",
        "admin_maintenance_menu",
        "clean_settings_menu",
        "admin_mgmt_menu",
    }

    for parent, children in MENU_TREE.items():
        if parent == "admin_start":
            continue
        if parent == "admin_mgmt_menu" and admin_driver.admin_user_id not in (config.ADMIN_IDS or []):
            continue
        await admin_driver.tap("admin_start")
        await admin_driver.tap(parent)
        on_screen = set(admin_driver.callbacks_on_screen())
        for child in children:
            if should_skip_bfs_callback(child) or child in optional_children:
                continue
            assert child in on_screen, f"{parent} missing button {child}; got {sorted(on_screen)}"
        assert not admin_driver.errors


@pytest.mark.asyncio
async def test_super_admin_menu_if_configured(admin_driver):
    if not config.ADMIN_IDS:
        pytest.skip("No ADMIN_IDS")
    await admin_driver.open_admin_menu()
    await admin_driver.tap("admin_mgmt_menu")
    assert admin_driver.callbacks_on_screen() or not admin_driver.errors
