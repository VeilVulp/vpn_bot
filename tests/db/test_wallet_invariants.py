"""Wallet deposit/deduct invariant tests."""

import pytest
from sqlalchemy import select

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Transaction, User
from vpn_bot.wallet_manager import WalletManager

pytestmark = pytest.mark.db


@pytest.mark.asyncio
async def test_deposit_increases_balance(db_user):
    ok = await WalletManager.deposit(db_user.id, 5000.0, "test deposit")
    assert ok is True
    async with AsyncSessionLocal() as session:
        u = await session.get(User, db_user.id)
        assert u.wallet_balance == pytest.approx(1_000_000.0 + 5000.0)


@pytest.mark.asyncio
async def test_deduct_insufficient_fails(db_user):
    ok = await WalletManager.deduct(db_user.id, 9_999_999_999.0, "too much")
    assert ok is False
    async with AsyncSessionLocal() as session:
        u = await session.get(User, db_user.id)
        assert u.wallet_balance == pytest.approx(1_000_000.0)


@pytest.mark.asyncio
async def test_approve_receipt_uses_deposit_card_type(db_user):
    from vpn_bot.models import PaymentReceipt

    async with AsyncSessionLocal() as session:
        r = PaymentReceipt(
            user_id=db_user.id,
            amount=2500.0,
            receipt_file_id="inv_test",
            status="pending",
        )
        session.add(r)
        await session.commit()
        rid = r.id

    ok = await WalletManager.approve_receipt(rid, admin_id=1, txn_type="deposit_card")
    assert ok is True

    async with AsyncSessionLocal() as session:
        txns = (
            await session.execute(
                select(Transaction).where(
                    Transaction.user_id == db_user.id,
                    Transaction.type == "deposit_card",
                )
            )
        ).scalars().all()
        assert any(t.amount == 2500.0 for t in txns)


@pytest.mark.asyncio
async def test_admin_receipt_service_delegates_to_wallet(db_user):
    from vpn_bot.admin_receipt_service import approve_payment_receipt
    from vpn_bot.models import PaymentReceipt

    async with AsyncSessionLocal() as session:
        r = PaymentReceipt(
            user_id=db_user.id,
            amount=3000.0,
            receipt_file_id="deleg_test",
            status="pending",
        )
        session.add(r)
        await session.commit()
        rid = r.id
        before = (await session.get(User, db_user.id)).wallet_balance

    ok, result = await approve_payment_receipt(rid, admin_id=1)
    assert ok is True
    user, amt = result
    assert amt == 3000.0

    async with AsyncSessionLocal() as session:
        u = await session.get(User, db_user.id)
        assert u.wallet_balance == pytest.approx(before + 3000.0)
