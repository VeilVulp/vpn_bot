"""Tests for admin main menu layout and inbox badges."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vpn_bot.admin_menu import (
    MENU_TREE,
    build_admin_main_keyboard,
    format_admin_menu_title,
    get_admin_inbox_counts,
)


def _callback_rows(markup) -> list[list[str]]:
    return [[btn.callback_data for btn in row] for row in markup.inline_keyboard]


@pytest.mark.asyncio
async def test_build_admin_main_keyboard_order_and_callbacks():
    with (
        patch("vpn_bot.admin_menu.get_admin_inbox_counts", new_callable=AsyncMock) as mock_counts,
        patch("vpn_bot.admin_menu.is_super_admin", new_callable=AsyncMock, return_value=False),
        patch("vpn_bot.admin_menu.has_admin_perm", new_callable=AsyncMock, return_value=True),
    ):
        mock_counts.return_value = {"receipts": 2, "tickets": 1}
        markup = await build_admin_main_keyboard(12345)

    rows = _callback_rows(markup)
    flat = [cb for row in rows for cb in row]
    assert len(rows) == 7
    assert rows[0] == ["search_user"]
    assert rows[1] == ["pending_receipts", "admin_tickets"]
    assert rows[2] == ["ovpn_l2tp_mgmt_menu", "wg_mgmt_menu"]
    assert rows[3] == ["sales_mgmt_menu", "admin_reports"]
    assert rows[4] == ["list_servers", "bot_config_menu"]
    assert rows[5] == ["shared_users_menu", "notification_menu"]
    assert rows[6] == ["backup_menu"]
    assert "clean_db_menu" not in flat
    assert "admin_get_wg_config" not in flat
    assert "admin_mgmt_menu" not in flat


@pytest.mark.asyncio
async def test_build_admin_main_keyboard_super_admin_row():
    with (
        patch("vpn_bot.admin_menu.get_admin_inbox_counts", new_callable=AsyncMock) as mock_counts,
        patch("vpn_bot.admin_menu.is_super_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_menu.has_admin_perm", new_callable=AsyncMock, return_value=True),
    ):
        mock_counts.return_value = {"receipts": 0, "tickets": 0}
        markup = await build_admin_main_keyboard(999)

    rows = _callback_rows(markup)
    assert len(rows) == 8
    assert rows[-1] == ["admin_mgmt_menu"]


@pytest.mark.asyncio
async def test_inbox_badge_labels_when_counts_positive():
    with (
        patch("vpn_bot.admin_menu.get_admin_inbox_counts", new_callable=AsyncMock) as mock_counts,
        patch("vpn_bot.admin_menu.is_super_admin", new_callable=AsyncMock, return_value=False),
        patch("vpn_bot.admin_menu.has_admin_perm", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_menu.LanguageManager.get") as mock_get,
    ):
        mock_counts.return_value = {"receipts": 3, "tickets": 0}

        def _get(key, **kwargs):
            if key == "admin.btn_with_count":
                return f"{kwargs['label']} ({kwargs['count']})"
            shorts = {
                "admin.btn_receipts_short": "Rcpt",
                "admin.btn_tickets_short": "Tkt",
            }
            return shorts.get(key, key[:12] if "_short" in key else key)

        mock_get.side_effect = _get
        markup = await build_admin_main_keyboard(1)

    receipt_btn = next(
        b for row in markup.inline_keyboard for b in row if b.callback_data == "pending_receipts"
    )
    assert "(3)" in receipt_btn.text
    ticket_btn = next(
        b for row in markup.inline_keyboard for b in row if b.callback_data == "admin_tickets"
    )
    assert "(3)" not in ticket_btn.text


def test_menu_tree_documents_admin_start_children():
    children = MENU_TREE["admin_start"]
    assert "search_user" in children
    assert "pending_receipts" in children
    assert children.index("search_user") < children.index("pending_receipts")


@pytest.mark.asyncio
async def test_get_admin_inbox_counts_uses_cache():
    from vpn_bot import admin_menu as menu_mod

    menu_mod._inbox_cache.update(ts=menu_mod.time.monotonic(), receipts=5, tickets=2)
    counts = await get_admin_inbox_counts()
    assert counts == {"receipts": 5, "tickets": 2}


def test_format_admin_menu_title_includes_sections_hint():
    with patch("vpn_bot.admin_menu.LanguageManager.get") as mock_get:
        mock_get.side_effect = lambda k, **_: {
            "admin.menu_title": "Title",
            "admin.menu_sections_hint": "Hint",
        }.get(k, k)
        title = format_admin_menu_title()
    assert "Title" in title
    assert "Hint" in title
