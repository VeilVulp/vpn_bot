"""Tests for user-facing datetime formatting (Iran timezone)."""

from datetime import datetime, timezone

import pytest

from vpn_bot.utils import LanguageManager, format_datetime, to_iran_local


@pytest.fixture(autouse=True)
def _load_locales():
    LanguageManager.load_locales()


@pytest.fixture(autouse=True)
def _restore_lang():
    prev = LanguageManager._current_lang
    yield
    LanguageManager._current_lang = prev


def test_to_iran_local_from_utc():
    utc = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    local = to_iran_local(utc)
    assert local.hour == 15
    assert local.minute == 30
    assert str(local.tzinfo) == "Asia/Tehran"


def test_to_iran_local_naive_treated_as_utc():
    naive = datetime(2026, 6, 6, 12, 0)
    local = to_iran_local(naive)
    assert local.hour == 15
    assert local.minute == 30


@pytest.mark.asyncio
async def test_format_datetime_english_uses_iran_time():
    LanguageManager._current_lang = "en"
    utc = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    result = await format_datetime(utc, include_time=True)
    assert result == "2026-06-06 15:30"


@pytest.mark.asyncio
async def test_format_datetime_persian_uses_iran_time():
    LanguageManager._current_lang = "fa"
    utc = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    result = await format_datetime(utc, include_time=True)
    assert "15:30" in result
