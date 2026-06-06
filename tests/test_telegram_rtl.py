"""Tests for Persian RTL bidi formatting in Telegram messages."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from vpn_bot.bot_handler import (
    TELEGRAM_MESSAGE_SAFE_MAX,
    build_plan_picker_message,
    format_plan_button_label,
)
from vpn_bot.utils import RLM, ensure_telegram_text, format_telegram_rtl
from vpn_bot.utils import LanguageManager


@pytest.fixture(autouse=True)
def _load_locales():
    LanguageManager.load_locales()


def test_format_telegram_rtl_fa_line_gets_rlm():
    LanguageManager._current_lang = "fa"
    out = format_telegram_rtl("سلام دنیا", lang="fa")
    assert out.startswith(RLM)
    assert "سلام" in out


def test_format_telegram_rtl_latin_plan_line_gets_rlm_and_emoji_end():
    LanguageManager._current_lang = "fa"
    out = format_telegram_rtl("🔷 **WG-5G**", lang="fa")
    assert out.startswith(RLM)
    assert "WG-5G" in out
    assert out.rstrip().endswith("🔷")
    assert not out.lstrip(RLM).startswith("🔷")


def test_format_telegram_rtl_separator_line_gets_rlm():
    LanguageManager._current_lang = "fa"
    out = format_telegram_rtl("➖➖➖➖➖➖", lang="fa")
    assert out.startswith(RLM)
    assert "➖" in out


def test_format_telegram_rtl_history_separator_gets_rlm():
    LanguageManager._current_lang = "fa"
    out = format_telegram_rtl("━━━━━━━━━━", lang="fa")
    assert out.startswith(RLM)
    assert "━" in out


def test_format_telegram_rtl_en_unchanged():
    text = "Hello world"
    assert format_telegram_rtl(text, lang="en") == text


def test_format_telegram_rtl_idempotent():
    LanguageManager._current_lang = "fa"
    raw = "جزئیات پلان در ادامه"
    once = format_telegram_rtl(raw, lang="fa")
    twice = format_telegram_rtl(once, lang="fa")
    assert once == twice


def test_ensure_telegram_text_applies_rtl_for_fa():
    LanguageManager._current_lang = "fa"
    out = ensure_telegram_text("متن تست")
    assert out.startswith(RLM)


def test_format_plan_button_label_fa_volume_before_name():
    LanguageManager._current_lang = "fa"
    label = format_plan_button_label(name="WG-5G", gb=5)
    assert label.index("5") < label.index("WG-5G")
    assert label.startswith(RLM)


@pytest.mark.asyncio
async def test_build_plan_picker_message_fa_plan_label_first():
    LanguageManager._current_lang = "fa"
    profiles = [
        SimpleNamespace(name="WG-5G", duration_days=30, volume_gb=5, price_toman=125_000),
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
    assert "پلان:" in text
    assert "WG-5G" in text
    rtl_text = ensure_telegram_text(text)
    assert RLM in rtl_text
    assert len(rtl_text) < TELEGRAM_MESSAGE_SAFE_MAX
