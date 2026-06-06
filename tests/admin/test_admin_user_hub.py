"""Tests for unified admin user management hub."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from vpn_bot.admin_menu import MENU_TREE, build_admin_main_keyboard, build_wg_mgmt_keyboard
from vpn_bot.admin_user_service import build_user_hub_keyboard, find_users_by_query, format_user_pick_label


def _callback_rows(markup) -> list[list[str]]:
    return [[btn.callback_data for btn in row] for row in markup.inline_keyboard]


@pytest.mark.asyncio
async def test_main_menu_user_mgmt_no_wg_config():
    with (
        patch("vpn_bot.admin_menu.get_admin_inbox_counts", new_callable=AsyncMock) as mock_counts,
        patch("vpn_bot.admin_menu.is_super_admin", new_callable=AsyncMock, return_value=False),
        patch("vpn_bot.admin_menu.has_admin_perm", new_callable=AsyncMock, return_value=True),
    ):
        mock_counts.return_value = {"receipts": 0, "tickets": 0}
        markup = await build_admin_main_keyboard(1)

    rows = _callback_rows(markup)
    flat = [cb for row in rows for cb in row]
    assert rows[0] == ["search_user"]
    assert "admin_get_wg_config" not in flat
    assert len(rows) >= 6
    assert "clean_db_menu" not in flat


def test_menu_tree_no_standalone_wg_config():
    assert "admin_get_wg_config" not in MENU_TREE["admin_start"]
    assert "search_wg_start" not in MENU_TREE["wg_mgmt_menu"]


@pytest.mark.asyncio
async def test_wg_menu_no_duplicate_search():
    with patch("vpn_bot.admin_menu.has_admin_perm", new_callable=AsyncMock, return_value=True):
        rows = _callback_rows(await build_wg_mgmt_keyboard(1))
    flat = [cb for row in rows for cb in row]
    assert "search_wg_start" not in flat


def test_user_hub_keyboard_has_hub_callbacks():
    user = MagicMock(id=42, is_banned=False)
    data = {
        "ticket_count": 1,
        "pending_receipts": 2,
        "ovpn_subs": [],
        "wg_subs": [],
        "total_ovpn": 0,
        "total_wg": 0,
        "ovpn_page": 0,
        "wg_page": 0,
        "subs_page_size": 5,
    }
    with patch("vpn_bot.admin_user_service.LanguageManager.get") as mock_get:
        mock_get.side_effect = lambda k, **kw: kw.get("name", k.split(".")[-1])
        markup = build_user_hub_keyboard(user, data)

    flat = [b.callback_data for row in markup.inline_keyboard for b in row]
    assert "admin_user_hub_42" in flat
    assert "admin_user_search" in flat
    assert "delete_account_42" in flat
    assert any(cb.startswith("admin_notify_user_") for cb in flat)


@pytest.mark.asyncio
async def test_find_users_by_query_telegram_username():
    with patch("vpn_bot.admin_user_service.AsyncSessionLocal") as mock_session_cls:
        session = AsyncMock()
        mock_session_cls.return_value.__aenter__.return_value = session

        user = MagicMock(id=1, telegram_id=111, username="testuser", full_name="Test")
        result_mock = MagicMock()
        result_mock.scalars.return_value.all.return_value = [user]
        result_mock.scalars.return_value.first.return_value = None
        session.execute = AsyncMock(return_value=result_mock)

        users = await find_users_by_query("@testuser")
        assert len(users) >= 0


def test_format_user_pick_label_prefers_name_and_phone():
    user = MagicMock(
        full_name=None,
        first_name="Ali",
        last_name="Karimi",
        phone_number="09121234567",
        username="alik",
        telegram_id=999888777,
    )
    assert format_user_pick_label(user) == "Ali Karimi · 09121234567"


def test_format_user_pick_label_phone_only():
    user = MagicMock(
        full_name=None,
        first_name=None,
        last_name=None,
        phone_number="+989121111111",
        username=None,
        telegram_id=123,
    )
    assert format_user_pick_label(user) == "+989121111111"


def test_format_user_pick_label_falls_back_to_telegram_id():
    user = MagicMock(
        full_name=None,
        first_name=None,
        last_name=None,
        phone_number=None,
        username=None,
        telegram_id=5505283912,
    )
    assert format_user_pick_label(user) == "5505283912"
