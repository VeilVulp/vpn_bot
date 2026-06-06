"""WireGuard profile list/edit must use volume_gb and duration_days (not OVPN fields)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from vpn_bot.admin_panel import edit_wg_profile_start, list_wg_profiles
from vpn_bot.admin_wg_service import update_wg_profile
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import WireGuardProfile


def _callback_rows(markup) -> list[str]:
    return [btn.callback_data for row in markup.inline_keyboard for btn in row]


def _mock_wg_profile(**overrides):
    prof = MagicMock()
    prof.id = overrides.get("id", 1)
    prof.name = overrides.get("name", "WG-5G")
    prof.volume_gb = overrides.get("volume_gb", 5)
    prof.duration_days = overrides.get("duration_days", 30)
    prof.price_usd = overrides.get("price_usd", 0.0)
    prof.price_toman = overrides.get("price_toman", 50_000)
    prof.rate_limit = overrides.get("rate_limit", None)
    return prof


@pytest.mark.asyncio
async def test_list_wg_profiles_uses_volume_and_duration_fields():
    prof = _mock_wg_profile(volume_gb=5, duration_days=30)

    query = MagicMock()
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.edit_text = AsyncMock()
    query.message.reply_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.message = None
    context = MagicMock()
    context.user_data = {}

    with (
        patch(
            "vpn_bot.admin_panel.get_all_wg_profiles",
            new=AsyncMock(return_value=[prof]),
        ),
        patch(
            "vpn_bot.admin_panel.get_profile_price",
            new=AsyncMock(return_value=50_000),
        ),
        patch(
            "vpn_bot.admin_panel.format_currency",
            new=AsyncMock(return_value="50,000"),
        ),
    ):
        await list_wg_profiles(update, context)

    call = query.message.edit_text.await_args
    assert call is not None
    body = call.args[0]
    assert "5" in body
    assert "30" in body
    markup = call.kwargs.get("reply_markup")
    cbs = _callback_rows(markup)
    assert f"edit_wg_prof_{prof.id}" in cbs


@pytest.mark.asyncio
async def test_edit_wg_profile_start_loads_volume_and_duration():
    prof = _mock_wg_profile(id=7, volume_gb=5, duration_days=30)

    query = MagicMock()
    query.data = f"edit_wg_prof_{prof.id}"
    query.answer = AsyncMock()
    query.message = MagicMock()
    query.message.reply_text = AsyncMock()
    update = MagicMock()
    update.callback_query = query
    update.message = None
    context = MagicMock()
    context.user_data = {}

    with (
        patch(
            "vpn_bot.admin_panel.get_wg_profile_by_id",
            new=AsyncMock(return_value=prof),
        ),
        patch(
            "vpn_bot.admin_panel.admin_conv_prompt",
            new=AsyncMock(),
        ),
    ):
        await edit_wg_profile_start(update, context)

    assert context.user_data["new_wg_profile"]["volume"] == 5
    assert context.user_data["new_wg_profile"]["days"] == 30


@pytest.mark.asyncio
async def test_update_wg_profile_persists_volume_and_duration(db_wg_profile):
    ok = await update_wg_profile(
        db_wg_profile.id,
        {"volume": 15, "days": 60},
    )
    assert ok is True

    async with AsyncSessionLocal() as session:
        row = await session.scalar(
            select(WireGuardProfile).where(WireGuardProfile.id == db_wg_profile.id)
        )
    assert row is not None
    assert row.volume_gb == 15
    assert row.duration_days == 60
