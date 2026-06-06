"""Tests for admin-configurable wallet custom amount limits."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from vpn_bot.admin_settings_service import (
    default_wallet_custom_limits,
    get_wallet_custom_limits,
    set_wallet_custom_max,
    set_wallet_custom_min,
)


def test_default_limits_by_currency():
    assert default_wallet_custom_limits("USD") == (5.0, 1000.0)
    assert default_wallet_custom_limits("TOMAN") == (50_000.0, 100_000_000.0)


@pytest.mark.asyncio
async def test_get_wallet_custom_limits_from_settings():
    with (
        patch("vpn_bot.admin_settings_service.get_currency_unit", new_callable=AsyncMock) as mock_unit,
        patch("vpn_bot.admin_settings_service.get_admin_setting", new_callable=AsyncMock) as mock_get,
    ):
        mock_unit.return_value = "TOMAN"
        mock_get.side_effect = lambda key, default=None: {
            "wallet_custom_min": 100_000,
            "wallet_custom_max": 50_000_000,
        }.get(key, default)

        min_v, max_v = await get_wallet_custom_limits()
        assert min_v == 100_000
        assert max_v == 50_000_000


@pytest.mark.asyncio
async def test_set_min_rejects_above_max():
    with patch(
        "vpn_bot.admin_settings_service.get_wallet_custom_limits",
        new_callable=AsyncMock,
        return_value=(50_000.0, 100_000.0),
    ):
        ok, err = await set_wallet_custom_min(200_000)
        assert ok is False
        assert err == "min_above_max"
