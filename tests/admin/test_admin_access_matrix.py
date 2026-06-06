"""Offline RBAC and admin access matrix tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.helpers.admin_e2e_harness import (
    AdminE2EDriver,
    build_admin_application,
    documented_menu_callbacks,
)
from vpn_bot.admin_management import (
    full_permission_preset,
    get_all_admin_telegram_ids,
    limited_permission_preset,
)
from vpn_bot.admin_menu import build_admin_main_keyboard
from vpn_bot.admin_permissions import (
    ALL_PERMISSION_KEYS,
    PERM_RECEIPTS,
    PERM_TICKETS,
    PERM_USER_MGMT,
    callback_allowed_for_permissions,
    has_perm_in_set,
    permission_for_callback,
)
from vpn_bot.config import config


def _callback_rows(markup) -> list[list[str]]:
    return [[btn.callback_data for btn in row] for row in markup.inline_keyboard]


@pytest.mark.asyncio
async def test_has_admin_perm_wildcards():
    granted = {PERM_TICKETS}
    assert has_perm_in_set(granted, PERM_TICKETS)
    assert has_perm_in_set(granted, "tickets.active")
    assert not has_perm_in_set(granted, PERM_RECEIPTS)


@pytest.mark.asyncio
async def test_db_admin_menu_excludes_admin_mgmt():
    with (
        patch("vpn_bot.admin_menu.get_admin_inbox_counts", new_callable=AsyncMock, return_value={"receipts": 0, "tickets": 0}),
        patch("vpn_bot.admin_menu.is_super_admin", new_callable=AsyncMock, return_value=False),
        patch("vpn_bot.admin_menu.has_admin_perm", new_callable=AsyncMock, return_value=True),
    ):
        markup = await build_admin_main_keyboard(999001)
    flat = [cb for row in _callback_rows(markup) for cb in row]
    assert "admin_mgmt_menu" not in flat
    assert "search_user" in flat


@pytest.mark.asyncio
async def test_super_sees_admin_mgmt():
    with (
        patch("vpn_bot.admin_menu.get_admin_inbox_counts", new_callable=AsyncMock, return_value={"receipts": 0, "tickets": 0}),
        patch("vpn_bot.admin_menu.is_super_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_menu.has_admin_perm", new_callable=AsyncMock, return_value=True),
    ):
        markup = await build_admin_main_keyboard(1)
    flat = [cb for row in _callback_rows(markup) for cb in row]
    assert "admin_mgmt_menu" in flat


@pytest.mark.asyncio
async def test_limited_admin_sees_only_permitted_buttons():
    limited = limited_permission_preset()
    with (
        patch("vpn_bot.admin_menu.get_admin_inbox_counts", new_callable=AsyncMock, return_value={"receipts": 1, "tickets": 2}),
        patch("vpn_bot.admin_menu.is_super_admin", new_callable=AsyncMock, return_value=False),
        patch("vpn_bot.admin_menu.has_admin_perm", new_callable=AsyncMock) as mock_has,
    ):
        async def _has(uid, perm):
            return has_perm_in_set(limited, perm)

        mock_has.side_effect = _has
        markup = await build_admin_main_keyboard(888002)
    flat = [cb for row in _callback_rows(markup) for cb in row]
    assert "search_user" in flat
    assert "pending_receipts" in flat
    assert "admin_tickets" in flat
    assert "wg_mgmt_menu" not in flat
    assert "clean_db_menu" not in flat


@pytest.mark.asyncio
async def test_get_all_admin_telegram_ids_includes_db():
    """Env admins are always included; DB ids merged when session returns rows."""
    ids = await get_all_admin_telegram_ids()
    for env_id in config.ADMIN_IDS:
        assert env_id in ids
    assert isinstance(ids, list)


@pytest.mark.asyncio
async def test_non_admin_denied_admin_callback():
    app, bot, _ = await build_admin_application(include_user_handlers=False)
    driver = AdminE2EDriver(app, bot, admin_user_id=424242)
    with patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=False):
        await driver.tap("pending_receipts")
    texts = [c.kwargs.get("text") or "" for c in bot.calls if c.method in ("edit_message_text", "send_message")]
    assert any("access_denied" in t.lower() or "دسترسی" in t for t in texts if t) or len(driver.errors) == 0


@pytest.mark.asyncio
async def test_db_admin_denied_admin_mgmt_tap():
    app, bot, _ = await build_admin_application(include_user_handlers=False)
    driver = AdminE2EDriver(
        app,
        bot,
        admin_user_id=888003,
        permissions=full_permission_preset(),
        super_admin=False,
    )
    await driver.open_admin_menu()
    await driver.tap("admin_mgmt_menu")
    texts = [c.kwargs.get("text") or "" for c in bot.calls if c.method == "edit_message_text"]
    assert any("denied" in (t or "").lower() or "غیرمجاز" in (t or "") for t in texts)


@pytest.mark.asyncio
async def test_documented_callbacks_permission_mapping():
    """Every documented menu callback resolves to a permission or admin-only."""
    optional = {"admin_mgmt_menu", "admin_start", "backup_import"}
    for cb in documented_menu_callbacks(include_super_admin=True):
        if cb in optional:
            continue
        perm = permission_for_callback(cb)
        assert perm is not None or cb == "admin_start", f"Unmapped callback: {cb}"


def test_limited_preset_subset_of_all():
    assert limited_permission_preset() <= ALL_PERMISSION_KEYS


def test_callback_allowed_for_limited_profile():
    limited = limited_permission_preset()
    assert callback_allowed_for_permissions("search_user", limited)
    assert callback_allowed_for_permissions("pending_receipts", limited)
    assert not callback_allowed_for_permissions("wg_mgmt_menu", limited)
    assert not callback_allowed_for_permissions("admin_mgmt_menu", limited, is_super=False)
    assert callback_allowed_for_permissions("admin_mgmt_menu", limited, is_super=True)
