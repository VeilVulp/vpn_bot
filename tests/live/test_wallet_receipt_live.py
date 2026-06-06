"""Live wallet + receipt approval (DB layer, no Telegram)."""

import pytest
from sqlalchemy import select

from vpn_bot.admin_receipt_service import approve_payment_receipt, reject_payment_receipt
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import PaymentReceipt, Transaction, User
from vpn_bot.wallet_manager import WalletManager

pytestmark = pytest.mark.live_mt


@pytest.mark.asyncio
async def test_wallet_deposit_deduct_roundtrip(intg_user):
    uid = intg_user.id
    async with AsyncSessionLocal() as session:
        u = await session.get(User, uid)
        start = u.wallet_balance

    assert await WalletManager.deposit(uid, 3000, "live test deposit")
    assert await WalletManager.deduct(uid, 1500, "live test purchase")

    async with AsyncSessionLocal() as session:
        u = await session.get(User, uid)
        assert u.wallet_balance == start + 3000 - 1500


@pytest.mark.asyncio
async def test_receipt_approve_and_reject(intg_user):
    async with AsyncSessionLocal() as session:
        pending = PaymentReceipt(
            user_id=intg_user.id,
            amount=7500,
            receipt_file_id="live_wallet_rcpt",
            status="pending",
        )
        reject = PaymentReceipt(
            user_id=intg_user.id,
            amount=100,
            receipt_file_id="live_wallet_reject",
            status="pending",
        )
        session.add_all([pending, reject])
        await session.commit()
        await session.refresh(pending)
        await session.refresh(reject)
        pid, rid = pending.id, reject.id

    async with AsyncSessionLocal() as session:
        bal_before = (await session.get(User, intg_user.id)).wallet_balance

    ok, msg = await approve_payment_receipt(pid, admin_id=1)
    assert ok is True, msg

    async with AsyncSessionLocal() as session:
        r = await session.get(PaymentReceipt, pid)
        assert r.status == "approved"
        u = await session.get(User, intg_user.id)
        assert u.wallet_balance >= bal_before + 7500 - 1

    assert await reject_payment_receipt(rid, 1) is True
    async with AsyncSessionLocal() as session:
        r = await session.get(PaymentReceipt, rid)
        assert r.status == "rejected"
