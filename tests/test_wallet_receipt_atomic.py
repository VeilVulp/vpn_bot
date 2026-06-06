"""Unit tests for wallet receipt approval (no MikroTik)."""

import pytest
from sqlalchemy import select

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import PaymentReceipt, Transaction, User
from vpn_bot.wallet_manager import WalletManager


@pytest.fixture
async def wallet_test_user():
    tg_id = 999888777
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = res.scalars().first()
        if not user:
            user = User(telegram_id=tg_id, username="wallet_test", wallet_balance=0.0)
            session.add(user)
            await session.commit()
            await session.refresh(user)
        else:
            user.wallet_balance = 0.0
            await session.commit()
        yield user


@pytest.mark.asyncio
async def test_approve_receipt_credits_balance_atomically(wallet_test_user):
    user = wallet_test_user
    amount = 50000.0

    async with AsyncSessionLocal() as session:
        receipt = PaymentReceipt(
            user_id=user.id,
            amount=amount,
            receipt_file_id="test_file_id",
            status="pending",
        )
        session.add(receipt)
        await session.commit()
        await session.refresh(receipt)
        receipt_id = receipt.id

    ok = await WalletManager.approve_receipt(receipt_id, admin_id=1)
    assert ok is True

    async with AsyncSessionLocal() as session:
        r = await session.get(PaymentReceipt, receipt_id)
        assert r.status == "approved"
        u = await session.get(User, user.id)
        assert u.wallet_balance == amount
        txns = (
            await session.execute(
                select(Transaction).where(
                    Transaction.user_id == user.id,
                    Transaction.type == "deposit_card",
                )
            )
        ).scalars().all()
        assert any(t.amount == amount for t in txns)


@pytest.mark.asyncio
async def test_approve_receipt_twice_fails(wallet_test_user):
    user = wallet_test_user
    async with AsyncSessionLocal() as session:
        receipt = PaymentReceipt(
            user_id=user.id,
            amount=1000.0,
            receipt_file_id="dup_test",
            status="pending",
        )
        session.add(receipt)
        await session.commit()
        await session.refresh(receipt)
        rid = receipt.id

    assert await WalletManager.approve_receipt(rid, 1) is True
    assert await WalletManager.approve_receipt(rid, 1) is False
