"""Unit tests for purchase history Markdown safety."""

from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import BadRequest
from telegram.helpers import escape_markdown

from vpn_bot.user_features import (
    _escape_history_desc,
    _format_history_separators_rtl,
    _history_send,
)
from vpn_bot.utils import LanguageManager, RLM


@pytest.fixture(autouse=True)
def _load_locales():
    LanguageManager.load_locales()


def test_escape_history_desc_special_markdown_chars():
    raw = "Premium_1Month *bold* `code` [link"
    escaped = _escape_history_desc(raw)
    assert escaped == escape_markdown(raw, version=1)
    assert "\\_" in escaped
    assert "\\*" in escaped
    assert "\\`" in escaped
    assert "\\[" in escaped


def test_escape_history_desc_empty():
    assert _escape_history_desc("") == ""
    assert _escape_history_desc(None) == ""


def test_history_item_template_with_escaped_desc():
    desc = _escape_history_desc("تمدید وایرگارد: Plan_A (u1abc_xyz)")
    item = LanguageManager.get(
        "history.item_purchase",
        type="خرید از کیف پول",
        desc=desc,
        receipt_line=LanguageManager.get(
            "history.receipt_linked", receipt_id="RCP-1-TEST1234"
        ),
        coupon_line="",
        amount="10,000 تومان",
        date="1404/01/01 12:00",
    )
    assert "Plan\\_A" in item
    assert "u1abc\\_xyz" in item
    assert "RCP-1-TEST1234" in item
    assert item.count("**") % 2 == 0


def test_history_separators_rtl_only():
    LanguageManager._current_lang = "fa"
    raw = "‏📅 تاریخ: `1405/03/16 13:17`\n━━━━━━━━━━"
    out = _format_history_separators_rtl(raw)
    lines = out.split("\n")
    assert lines[0] == "‏📅 تاریخ: `1405/03/16 13:17`"
    assert lines[1].startswith(RLM)
    assert "━" in lines[1]


def test_history_separators_rtl_en_unchanged():
    LanguageManager._current_lang = "en"
    raw = "📅 2026/03/16 13:17\n━━━━━━━━━━"
    assert _format_history_separators_rtl(raw) == raw


@pytest.mark.asyncio
async def test_history_send_fallback_on_bad_markdown():
    update = MagicMock()
    update.callback_query = MagicMock()
    update.callback_query.edit_message_text = AsyncMock(
        side_effect=[
            BadRequest("Can't parse entities: can't find end of the entity"),
            None,
        ]
    )
    update.message = None
    markup = MagicMock()

    await _history_send(update, update.callback_query, "test *markdown* text", markup)

    assert update.callback_query.edit_message_text.await_count == 2
    first_kwargs = update.callback_query.edit_message_text.await_args_list[0].kwargs
    second_kwargs = update.callback_query.edit_message_text.await_args_list[1].kwargs
    assert first_kwargs.get("parse_mode") == "Markdown"
    assert "parse_mode" not in second_kwargs
