"""Inline admin button labels: short variants and pair layout."""

from unittest.mock import AsyncMock, patch

import pytest

from vpn_bot.admin_menu import (
    _fits_inline_pair,
    admin_button_label,
    build_wg_mgmt_keyboard,
    keyboard_row_pair,
)


def test_admin_button_label_uses_short_variant():
    with patch("vpn_bot.admin_menu.LanguageManager.get") as mock_get:
        mock_get.side_effect = lambda k, **kw: {
            "admin.wg.btn_plans": "Long plans label",
            "admin.wg.btn_plans_short": "📋 Plans",
        }.get(k, k)
        assert admin_button_label("admin.wg.btn_plans", short=True) == "📋 Plans"


def test_keyboard_row_pair_splits_when_too_long():
    with patch("vpn_bot.admin_menu.LanguageManager.get") as mock_get:
        mock_get.side_effect = lambda k, **kw: {
            "a": "Short",
            "a_short": "Short",
            "b": "X" * 30,
            "b_short": "X" * 30,
        }.get(k, k)
        rows = keyboard_row_pair("a", "cb1", "b", "cb2")
    assert len(rows) == 2
    assert len(rows[0]) == 1
    assert len(rows[1]) == 1


@pytest.mark.asyncio
async def test_wg_mgmt_keyboard_all_single_column():
    with (
        patch("vpn_bot.admin_menu.LanguageManager.get") as mock_get,
        patch("vpn_bot.admin_menu.has_admin_perm", new_callable=AsyncMock, return_value=True),
    ):
        def _get(k, **kw):
            if k == "common.back":
                return "Back"
            return "Btn"
        mock_get.side_effect = _get
        markup = await build_wg_mgmt_keyboard(1)
    for row in markup.inline_keyboard:
        assert len(row) == 1
    callbacks = [row[0].callback_data for row in markup.inline_keyboard]
    assert callbacks[-1] == "admin_start"
    assert "list_wg_profiles" in callbacks


def test_fits_inline_pair_persian_short():
    assert _fits_inline_pair("📋 رسیدها")
    assert not _fits_inline_pair("🛡️ مدیریت OpenVPN/L2TP و تنظیمات بیشتر")
