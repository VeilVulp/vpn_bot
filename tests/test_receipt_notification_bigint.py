"""ReceiptNotification must store Telegram admin chat ids (often > int32)."""

from vpn_bot.models import ReceiptNotification
from sqlalchemy import BigInteger, Integer


def test_receipt_notification_uses_bigint_for_telegram_ids():
    assert isinstance(ReceiptNotification.__table__.c.admin_id.type, BigInteger)
    assert isinstance(ReceiptNotification.__table__.c.message_id.type, BigInteger)
    assert isinstance(ReceiptNotification.__table__.c.receipt_id.type, Integer)
