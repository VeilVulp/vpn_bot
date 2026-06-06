"""DB tests: group-archived pending receipts are excluded from cleanup."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from vpn_bot.admin_cleanup_service import clean_pending_receipts_service
from vpn_bot.admin_settings_service import set_receipt_group_id
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import PaymentReceipt, ReceiptNotification

RECEIPT_GROUP_ID = -100222333444

pytestmark = [pytest.mark.db]


@pytest.mark.asyncio
async def test_cleanup_skips_receipts_archived_in_group(db_user, mock_mikrotik):
    await set_receipt_group_id(RECEIPT_GROUP_ID)
    old = datetime.now(timezone.utc) - timedelta(days=10)

    async with AsyncSessionLocal() as session:
        archived = PaymentReceipt(
            user_id=db_user.id,
            amount=5_000.0,
            receipt_file_id="arch",
            status="pending",
            unique_id=uuid.uuid4().hex[:8].upper(),
            receipt_type="text",
            submitted_at=old,
        )
        plain = PaymentReceipt(
            user_id=db_user.id,
            amount=3_000.0,
            receipt_file_id="plain",
            status="pending",
            unique_id=uuid.uuid4().hex[:8].upper(),
            receipt_type="text",
            submitted_at=old,
        )
        session.add(archived)
        session.add(plain)
        await session.commit()
        await session.refresh(archived)
        await session.refresh(plain)
        session.add(
            ReceiptNotification(
                receipt_id=archived.id,
                admin_id=RECEIPT_GROUP_ID,
                message_id=42,
            )
        )
        await session.commit()
        archived_id = archived.id
        plain_id = plain.id

    deleted = await clean_pending_receipts_service(days=5)
    assert deleted == 1

    async with AsyncSessionLocal() as session:
        still = await session.get(PaymentReceipt, archived_id)
        gone = await session.get(PaymentReceipt, plain_id)
        assert still is not None
        assert gone is None
