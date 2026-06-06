"""V4: concurrent receipt approval races."""

import asyncio

import pytest
from sqlalchemy import select

from vpn_bot.admin_receipt_service import approve_payment_receipt
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import PaymentReceipt, User
from tests.db.helpers.invariant_checker import check_no_negative_balances, run_all_checks

pytestmark = pytest.mark.db


@pytest.mark.asyncio
async def test_a3_double_approve_concurrent(db_user):
    async with AsyncSessionLocal() as session:
        r = PaymentReceipt(
            user_id=db_user.id,
            amount=50_000.0,
            receipt_file_id="race_receipt",
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
        assert u.wallet_balance == pytest.approx(before + 50_000.0)

    assert await check_no_negative_balances([db_user.id]) == []
    await run_all_checks(user_ids=[db_user.id], receipt_ids=[rid])
