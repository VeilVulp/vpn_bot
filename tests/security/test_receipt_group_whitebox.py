"""White-box tests for receipt group notification and duplicate approve handling."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select

from tests.helpers.admin_e2e_harness import RecordingFakeBot
from vpn_bot.admin_panel import confirm_receipt_action
from vpn_bot.admin_receipt_service import notify_admins_new_receipt
from vpn_bot.admin_settings_service import set_receipt_group_id, set_receipt_notif_mode
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import PaymentReceipt, ReceiptNotification, User
from vpn_bot.utils import LanguageManager

RECEIPT_GROUP_ID = -100333444555
ADMIN_ID = 920_001

pytestmark = [pytest.mark.security, pytest.mark.db]


@pytest.fixture(autouse=True)
def _load_locales():
    LanguageManager.load_locales()


@pytest.mark.asyncio
async def test_notify_group_mode_posts_to_group_only(db_user_factory, mock_mikrotik):
    user = await db_user_factory(balance=100_000.0)
    await set_receipt_group_id(RECEIPT_GROUP_ID)
    await set_receipt_notif_mode("group")

    async with AsyncSessionLocal() as session:
        receipt = PaymentReceipt(
            user_id=user.id,
            amount=50_000.0,
            receipt_file_id="REF-123",
            status="pending",
            unique_id=uuid.uuid4().hex[:8].upper(),
            receipt_type="text",
            currency_unit="TOMAN",
        )
        session.add(receipt)
        await session.commit()
        await session.refresh(receipt)

    bot = RecordingFakeBot()
    await notify_admins_new_receipt(
        bot,
        receipt,
        user,
        receipt_text="REF-123",
        tg_display_name="Test User",
        tg_username="tester",
    )

    send_calls = [c for c in bot.calls if c.method == "send_message"]
    assert len(send_calls) == 1
    chat = send_calls[0].kwargs.get("chat")
    assert chat.id == RECEIPT_GROUP_ID

    async with AsyncSessionLocal() as session:
        rows = (
            await session.execute(
                select(ReceiptNotification).where(ReceiptNotification.receipt_id == receipt.id)
            )
        ).scalars().all()
    assert len(rows) == 1
    assert int(rows[0].admin_id) == RECEIPT_GROUP_ID


@pytest.mark.asyncio
async def test_notify_both_mode_posts_group_and_dm(db_user_factory, mock_mikrotik):
    user = await db_user_factory(balance=100_000.0)
    await set_receipt_group_id(RECEIPT_GROUP_ID)
    await set_receipt_notif_mode("both")

    async with AsyncSessionLocal() as session:
        receipt = PaymentReceipt(
            user_id=user.id,
            amount=25_000.0,
            receipt_file_id="REF-456",
            status="pending",
            unique_id=uuid.uuid4().hex[:8].upper(),
            receipt_type="text",
            currency_unit="TOMAN",
        )
        session.add(receipt)
        await session.commit()
        await session.refresh(receipt)

    bot = RecordingFakeBot()

    with patch(
        "vpn_bot.admin_permissions.get_admins_for_permission",
        new_callable=AsyncMock,
        return_value=[ADMIN_ID],
    ):
        await notify_admins_new_receipt(
            bot,
            receipt,
            user,
            receipt_text="REF-456",
            tg_display_name="Both Mode",
            tg_username=None,
        )

    chat_ids = {c.kwargs.get("chat").id for c in bot.calls if c.method == "send_message"}
    assert RECEIPT_GROUP_ID in chat_ids
    assert ADMIN_ID in chat_ids


@pytest.mark.asyncio
async def test_group_duplicate_approve_sends_already_processed(db_user, mock_mikrotik):
    from tests.security.helpers.security_harness import _callback_update, bind_bot

    await set_receipt_group_id(RECEIPT_GROUP_ID)

    async with AsyncSessionLocal() as session:
        receipt = PaymentReceipt(
            user_id=db_user.id,
            amount=10_000.0,
            receipt_file_id="done",
            status="approved",
            unique_id="DUP01",
            receipt_type="text",
            admin_note="Approved by 999",
            submitted_at=datetime.now(timezone.utc),
        )
        session.add(receipt)
        await session.commit()
        await session.refresh(receipt)
        rid = receipt.id

    bot = RecordingFakeBot()
    update = _callback_update(ADMIN_ID, RECEIPT_GROUP_ID, f"receipt_approve_{rid}")
    bind_bot(update, bot)
    context = MagicMock()
    context.bot = bot
    context.user_data = {}

    with patch(
        "vpn_bot.admin_receipt_service.sync_receipt_admin_notifications",
        new_callable=AsyncMock,
    ):
        await confirm_receipt_action(update, context)

    alert_calls = [c for c in bot.calls if c.method == "answer_callback_query"]
    assert len(alert_calls) == 1
    assert LanguageManager.get("admin.receipt.status_approved") in str(alert_calls[0].kwargs.get("text", ""))
