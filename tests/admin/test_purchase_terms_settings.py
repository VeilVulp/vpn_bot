"""Admin settings helpers for purchase terms."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from vpn_bot.admin_settings_service import (
    PURCHASE_TERMS_MODE_ONCE,
    get_purchase_terms_mode,
    get_purchase_terms_version,
    is_purchase_terms_enabled,
    set_purchase_terms_enabled,
    set_purchase_terms_mode,
    set_purchase_terms_text,
)


@pytest.mark.asyncio
async def test_toggle_purchase_terms_enabled():
    with patch("vpn_bot.admin_settings_service.set_admin_setting", new_callable=AsyncMock) as mock_set:
        await set_purchase_terms_enabled(True)
        mock_set.assert_called_once_with("purchase_terms_enabled", True)


@pytest.mark.asyncio
async def test_set_purchase_terms_text_bumps_version():
    with (
        patch("vpn_bot.admin_settings_service.set_admin_setting", new_callable=AsyncMock) as mock_set,
        patch(
            "vpn_bot.admin_settings_service.get_purchase_terms_version",
            new_callable=AsyncMock,
            return_value="3",
        ),
    ):
        new_v = await set_purchase_terms_text("New terms body")
        assert new_v == "4"
        assert mock_set.await_count == 2


@pytest.mark.asyncio
async def test_get_purchase_terms_mode_defaults_once():
    with patch(
        "vpn_bot.admin_settings_service.get_admin_setting",
        new_callable=AsyncMock,
        return_value=None,
    ):
        assert await get_purchase_terms_mode() == PURCHASE_TERMS_MODE_ONCE


@pytest.mark.asyncio
async def test_is_purchase_terms_disabled_by_default():
    with patch(
        "vpn_bot.admin_settings_service.get_admin_setting",
        new_callable=AsyncMock,
        return_value=None,
    ):
        assert await is_purchase_terms_enabled() is False
