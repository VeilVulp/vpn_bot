"""C1–C6: Wallet and receipt white-box security tests."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from telegram.ext import ApplicationHandlerStop

from tests.security.helpers.security_harness import _callback_update
from vpn_bot.admin_permissions import admin_callback_access_gate
from vpn_bot.admin_receipt_service import approve_payment_receipt
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import PaymentReceipt, User
from vpn_bot.wallet_manager import WalletManager

pytestmark = [pytest.mark.security, pytest.mark.db]

REGULAR_USER_ID = 920_001


@pytest.mark.asyncio
async def test_c1_double_approve_single_credit(db_user):
    async with AsyncSessionLocal() as session:
        r = PaymentReceipt(
            user_id=db_user.id,
            amount=25_000.0,
            receipt_file_id="sec_double",
            status="pending",
        )
        session.add(r)
        await session.commit()
        rid = r.id
        before = (await session.get(User, db_user.id)).wallet_balance

    results = await asyncio.gather(
        approve_payment_receipt(rid, 1),
        approve_payment_receipt(rid, 1),
        return_exceptions=True,
    )
    successes = sum(1 for r in results if isinstance(r, tuple) and r[0] is True)

    async with AsyncSessionLocal() as session:
        u = await session.get(User, db_user.id)
        receipt = await session.get(PaymentReceipt, rid)
        assert receipt.status == "approved"
        assert successes == 1
        assert u.wallet_balance == pytest.approx(before + 25_000.0)


@pytest.mark.asyncio
async def test_c2_approve_after_reject_fails(db_user):
    async with AsyncSessionLocal() as session:
        r = PaymentReceipt(
            user_id=db_user.id,
            amount=5_000.0,
            receipt_file_id="sec_reject",
            status="pending",
        )
        session.add(r)
        await session.commit()
        rid = r.id

    assert await WalletManager.reject_receipt(rid, admin_id=1) is True
    assert await WalletManager.approve_receipt(rid, admin_id=1) is False


@pytest.mark.asyncio
async def test_c3_deduct_over_balance_fails(db_user):
    async with AsyncSessionLocal() as session:
        u = await session.get(User, db_user.id)
        before = u.wallet_balance
        await session.commit()

    assert await WalletManager.deduct(db_user.id, before + 1.0, "overdraft") is False

    async with AsyncSessionLocal() as session:
        u = await session.get(User, db_user.id)
        assert u.wallet_balance == pytest.approx(before)


@pytest.mark.asyncio
async def test_c4_deposit_invalid_user():
    assert await WalletManager.deposit(999_999_999, 100.0) is False


@pytest.mark.asyncio
async def test_c5_non_admin_receipt_approve_callback_denied(db_user):
    async with AsyncSessionLocal() as session:
        r = PaymentReceipt(
            user_id=db_user.id,
            amount=1_000.0,
            receipt_file_id="sec_gate",
            status="pending",
        )
        session.add(r)
        await session.commit()
        rid = r.id

    update = _callback_update(REGULAR_USER_ID, REGULAR_USER_ID, f"receipt_approve_{rid}")
    with patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=False):
        with pytest.raises(ApplicationHandlerStop):
            await admin_callback_access_gate(update, None)
