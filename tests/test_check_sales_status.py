"""Regression: check_sales_status must not invert assert_new_purchase_capacity result."""

import pytest
from unittest.mock import AsyncMock, patch

from vpn_bot.bot_handler import check_sales_status


@pytest.mark.asyncio
async def test_wg_sales_open_when_capacity_available():
    """WG toggles on + capacity OK → not blocked (was wrongly blocked before fix)."""
    with (
        patch(
            "vpn_bot.bot_handler.get_admin_setting",
            AsyncMock(side_effect=lambda key, default=None: {
                "sales_global_active": True,
                "sales_wg_active": True,
            }.get(key, default)),
        ),
        patch(
            "vpn_bot.admin_sales_service.assert_new_purchase_capacity",
            AsyncMock(return_value=(True, None)),
        ),
    ):
        blocked, msg = await check_sales_status(None, None, "wg")

    assert blocked is False
    assert msg is None


@pytest.mark.asyncio
async def test_wg_blocked_when_protocol_disabled():
    with (
        patch(
            "vpn_bot.bot_handler.get_admin_setting",
            AsyncMock(side_effect=lambda key, default=None: {
                "sales_global_active": True,
                "sales_wg_active": False,
            }.get(key, default if key != "sales_wg_msg" else "WG off")),
        ),
        patch(
            "vpn_bot.admin_sales_service.assert_new_purchase_capacity",
            AsyncMock(return_value=(True, None)),
        ),
    ):
        blocked, msg = await check_sales_status(None, None, "wg")

    assert blocked is True
    assert msg == "WG off"


@pytest.mark.asyncio
async def test_global_sales_off_string_false_blocks():
    with (
        patch(
            "vpn_bot.bot_handler.get_admin_setting",
            AsyncMock(side_effect=lambda key, default=None: {
                "sales_global_active": "false",
            }.get(key, default)),
        ),
        patch(
            "vpn_bot.admin_sales_service.assert_new_purchase_capacity",
            AsyncMock(return_value=(True, None)),
        ),
    ):
        blocked, msg = await check_sales_status(None, None, "ovpn")

    assert blocked is True


@pytest.mark.asyncio
async def test_wg_blocked_when_capacity_full():
    cap_msg = "capacity full"
    with (
        patch(
            "vpn_bot.bot_handler.get_admin_setting",
            AsyncMock(side_effect=lambda key, default=None: {
                "sales_global_active": True,
                "sales_wg_active": True,
            }.get(key, default)),
        ),
        patch(
            "vpn_bot.admin_sales_service.assert_new_purchase_capacity",
            AsyncMock(return_value=(False, cap_msg)),
        ),
    ):
        blocked, msg = await check_sales_status(None, None, "wg")

    assert blocked is True
    assert msg == cap_msg
