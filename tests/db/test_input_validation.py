"""Input validation on DB writers."""

import pytest

from vpn_bot.admin_user_service import update_user_balance
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import PaymentReceipt, User
from vpn_bot.wallet_manager import WalletManager

pytestmark = pytest.mark.db


@pytest.mark.asyncio
async def test_deposit_invalid_user():
    assert await WalletManager.deposit(999999999, 100.0) is False


@pytest.mark.asyncio
async def test_deduct_zero_amount(db_user):
    assert await WalletManager.deduct(db_user.id, 0.0, "zero") is True
    async with AsyncSessionLocal() as session:
        u = await session.get(User, db_user.id)
        assert u.wallet_balance == pytest.approx(1_000_000.0)


@pytest.mark.asyncio
async def test_negative_deposit_still_applies(db_user):
    """Documents behavior: deposit adds negative amount (admin should not do this)."""
    before = db_user.wallet_balance
    ok = await WalletManager.deposit(db_user.id, -100.0, "negative test")
    assert ok is True
    async with AsyncSessionLocal() as session:
        u = await session.get(User, db_user.id)
        assert u.wallet_balance == pytest.approx(before - 100.0)


@pytest.mark.asyncio
async def test_admin_balance_overwrite(db_user):
    ok = await update_user_balance(db_user.id, -500.0, log_adjust=True)
    assert ok is True
    async with AsyncSessionLocal() as session:
        u = await session.get(User, db_user.id)
        assert u.wallet_balance == -500.0


@pytest.mark.asyncio
async def test_receipt_negative_amount_stored(db_user):
    async with AsyncSessionLocal() as session:
        r = PaymentReceipt(
            user_id=db_user.id,
            amount=-100.0,
            receipt_file_id="neg",
            status="pending",
        )
        session.add(r)
        await session.commit()
        assert r.id is not None
