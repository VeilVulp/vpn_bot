"""Tests for wallet receipt FIFO attribution in purchase history."""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from vpn_bot.user_features import (
    _attribute_wallet_receipts,
    _parse_legacy_receipt_id,
    _resolve_receipt_display_id,
)
from vpn_bot.utils import LanguageManager


@pytest.fixture(autouse=True)
def _load_locales():
    LanguageManager.load_locales()


def _txn(txn_id, amount, txn_type, *, receipt_id=None, description=None):
    return SimpleNamespace(
        id=txn_id,
        amount=amount,
        type=txn_type,
        receipt_id=receipt_id,
        description=description,
        currency_unit="TOMAN",
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


def _receipt(rid, unique_id="ABC12345"):
    return SimpleNamespace(id=rid, unique_id=unique_id)


def test_parse_legacy_receipt_id():
    assert _parse_legacy_receipt_id("تایید رسید شماره #392") == 392
    assert _parse_legacy_receipt_id("Receipt #12 Approved") == 12
    assert _parse_legacy_receipt_id("no id here") is None


def test_resolve_receipt_display_id_from_receipt_row():
    txn = _txn(1, 125000, "deposit_card", receipt_id=392)
    receipts = {392: _receipt(392, "A1B2C3D4")}
    assert _resolve_receipt_display_id(txn, receipts) == "RCP-392-A1B2C3D4"


def test_resolve_receipt_display_id_legacy_description():
    txn = _txn(1, 125000, "deposit_card", description="تایید رسید شماره #390")
    receipts = {390: _receipt(390, "XYZ98765")}
    assert _resolve_receipt_display_id(txn, receipts) == "RCP-390-XYZ98765"


def test_fifo_attributes_first_receipt_to_purchase():
    receipts = {
        1: _receipt(1, "AAAA1111"),
        2: _receipt(2, "BBBB2222"),
    }
    txns = [
        _txn(10, 125000, "deposit_card", receipt_id=1),
        _txn(11, 250000, "deposit_card", receipt_id=2),
        _txn(12, -125000, "purchase"),
    ]
    result = _attribute_wallet_receipts(txns, receipts)
    assert result == {12: "RCP-1-AAAA1111"}


def test_fifo_skips_non_receipt_deposit_then_links_receipt_lot():
    receipts = {2: _receipt(2, "BBBB2222")}
    txns = [
        _txn(10, 100000, "deposit"),
        _txn(11, 125000, "deposit_card", receipt_id=2),
        _txn(12, -125000, "purchase"),
    ]
    result = _attribute_wallet_receipts(txns, receipts)
    assert result == {12: "RCP-2-BBBB2222"}


def test_deposit_card_locale_template():
    LanguageManager._current_lang = "fa"
    item = LanguageManager.get(
        "history.item_deposit_card",
        type="شارژ با تایید فیش",
        receipt_id="RCP-392-A1B2C3D4",
        coupon_line="",
        amount="125,000 تومان",
        date="1405/03/16 13:17",
    )
    assert "RCP-392-A1B2C3D4" in item
    assert "شارژ با تایید فیش" in item


def test_history_type_deposit_card_locale_exists():
    LanguageManager._current_lang = "fa"
    assert LanguageManager.get("history.type_deposit_card") == "شارژ با تایید فیش"
    LanguageManager._current_lang = "en"
    assert LanguageManager.get("history.type_deposit_card") == "Receipt wallet credit"
