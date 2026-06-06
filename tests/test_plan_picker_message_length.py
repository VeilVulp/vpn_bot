"""Regression: plan picker must not exceed Telegram message/button limits."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from vpn_bot.bot_handler import (
    TELEGRAM_MESSAGE_SAFE_MAX,
    INLINE_BUTTON_LABEL_MAX,
    build_plan_picker_message,
    buy_wg_service,
    format_plan_button_label,
    truncate_telegram_button,
)
from vpn_bot.utils import LanguageManager


@pytest.fixture(autouse=True)
def _load_locales():
    LanguageManager.load_locales()


def test_truncate_telegram_button():
    assert len(truncate_telegram_button("short")) <= INLINE_BUTTON_LABEL_MAX
    long_name = "A" * 80
    out = truncate_telegram_button(long_name)
    assert len(out) <= INLINE_BUTTON_LABEL_MAX
    assert out.endswith("…")


def test_format_plan_button_label_name_and_volume_only():
    LanguageManager._current_lang = "fa"
    label = format_plan_button_label(name="WG-5G", gb=5)
    assert "WG-5G" in label
    assert "5" in label
    assert "گیگ" in label
    assert label.index("5") < label.index("WG-5G")
    assert "|" not in label
    assert len(label) <= INLINE_BUTTON_LABEL_MAX


@pytest.mark.asyncio
async def test_build_plan_picker_message_includes_plan_items():
    LanguageManager._current_lang = "fa"
    profiles = [
        SimpleNamespace(name="WG-5G", duration_days=30, volume_gb=5, price_toman=125_000),
        SimpleNamespace(name="WG-10G", duration_days=30, volume_gb=10, price_toman=250_000),
    ]

    async def price_fn(p):
        return f"{p.price_toman:,}"

    text = await build_plan_picker_message(
        "wg.buy_title",
        profiles,
        days_attr="duration_days",
        gb_attr="volume_gb",
        price_for_profile=price_fn,
    )
    assert "WG-5G" in text
    assert "مدت" in text or "30" in text
    assert len(text) < TELEGRAM_MESSAGE_SAFE_MAX


@pytest.mark.asyncio
async def test_build_plan_picker_message_four_production_plans_fa():
    LanguageManager._current_lang = "fa"
    profiles = [
        SimpleNamespace(name=f"WG-{gb}G", duration_days=30, volume_gb=gb, price_toman=gb * 25_000)
        for gb in (5, 10, 15, 20)
    ]

    async def price_fn(p):
        return f"{p.price_toman:,}"

    text = await build_plan_picker_message(
        "wg.buy_title",
        profiles,
        days_attr="duration_days",
        gb_attr="volume_gb",
        price_for_profile=price_fn,
    )
    assert text.count("➖") >= 4
    assert len(text) < TELEGRAM_MESSAGE_SAFE_MAX


@pytest.mark.asyncio
async def test_buy_wg_many_plans_message_within_limit():
    """buy_wg_service with many profiles must not build an oversized message."""
    profiles = []
    for i in range(60):
        p = MagicMock()
        p.id = i + 1
        p.name = f"WG Plan {i + 1}"
        p.duration_days = 30
        p.volume_gb = 50 + i
        p.price_toman = 100_000 + i * 1000
        profiles.append(p)

    update = MagicMock()
    update.callback_query = None
    update.effective_user = MagicMock(id=424242)
    update.message = MagicMock()
    update.message.text = LanguageManager.get("menu.buy_wg")
    update.message.reply_text = AsyncMock()

    context = MagicMock()
    context.user_data = {"last_seen_lang": LanguageManager._current_lang}

    mock_session = MagicMock()
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = profiles
    mock_session.execute = AsyncMock(return_value=mock_result)
    mock_cm = MagicMock()
    mock_cm.__aenter__ = AsyncMock(return_value=mock_session)
    mock_cm.__aexit__ = AsyncMock(return_value=None)

    with (
        patch("vpn_bot.utils.check_maintenance_status", new_callable=AsyncMock, return_value=(False, None)),
        patch("vpn_bot.bot_handler.check_sales_status", new_callable=AsyncMock, return_value=(False, None)),
        patch("vpn_bot.bot_handler.block_if_current_user_banned", new_callable=AsyncMock, return_value=False),
        patch("vpn_bot.bot_handler.check_user_registration", new_callable=AsyncMock, return_value=None),
        patch("vpn_bot.bot_handler.AsyncSessionLocal", return_value=mock_cm),
        patch("vpn_bot.bot_handler.format_currency", new_callable=AsyncMock, side_effect=lambda v, **kw: f"{v:,}"),
    ):
        await buy_wg_service(update, context)

    update.message.reply_text.assert_awaited_once()
    sent_text = update.message.reply_text.await_args.args[0]
    assert len(sent_text) < TELEGRAM_MESSAGE_SAFE_MAX
    markup = update.message.reply_text.await_args.kwargs.get("reply_markup")
    for row in markup.inline_keyboard:
        for btn in row:
            assert len(btn.text) <= INLINE_BUTTON_LABEL_MAX
            assert "|" not in btn.text
