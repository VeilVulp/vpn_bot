"""Live load subset: 10–20 parallel operations with Semaphore(5)."""

import asyncio
import time
import uuid

import pytest
from sqlalchemy import select

from vpn_bot.admin_receipt_service import approve_payment_receipt
from vpn_bot.admin_subscription_service import extend_subscription_validity
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import PaymentReceipt
from vpn_bot.user_features import checkout_subscription, finalize_wg_purchase
from vpn_bot.wallet_manager import WalletManager

pytestmark = pytest.mark.live_mt

_LIVE_SEM = asyncio.Semaphore(5)


async def _guarded(coro):
    async with _LIVE_SEM:
        return await coro


@pytest.mark.asyncio
async def test_live_load_subset_parallel(intg_user, intg_ovpn_profile, intg_wg_profile, intg_wg_interface, live_server):
    """~17 parallel tasks: OVPN, WG, deposit, receipt, admin extend."""
    from sqlalchemy import select as sa_select
    from vpn_bot.models import User

    base_tg = 880_002_000
    extra_users = []
    async with AsyncSessionLocal() as session:
        for i in range(4):
            tg = base_tg + i
            res = await session.execute(sa_select(User).where(User.telegram_id == tg))
            u = res.scalars().first()
            if not u:
                u = User(
                    telegram_id=tg,
                    username=f"load_live_{i}",
                    wallet_balance=10_000_000.0,
                )
                session.add(u)
            else:
                u.wallet_balance = 10_000_000.0
            extra_users.append(u)
        await session.commit()
        for u in extra_users:
            await session.refresh(u)

    all_users = [intg_user] + extra_users
    metrics = {"ok": 0, "fail": 0}
    created_subs = []

    async def _ovpn(u):
        ok, sub, _ = await checkout_subscription(u.telegram_id, intg_ovpn_profile.id)
        if ok and sub:
            metrics["ok"] += 1
            created_subs.append(sub.mikrotik_username)
        else:
            metrics["fail"] += 1

    async def _wg(u):
        ok = await finalize_wg_purchase(u.telegram_id, intg_wg_profile.id, context=None, is_tg_id=True)
        metrics["ok" if ok else "fail"] += 1

    async def _deposit(u):
        ok = await WalletManager.deposit(u.id, 1000.0, "live load deposit")
        metrics["ok" if ok else "fail"] += 1

    tasks = []
    for u in all_users[:5]:
        tasks.append(_guarded(_ovpn(u)))
    for u in all_users[:5]:
        tasks.append(_guarded(_wg(u)))
    for u in all_users[:3]:
        tasks.append(_guarded(_deposit(u)))

    async with AsyncSessionLocal() as session:
        r = PaymentReceipt(
            user_id=intg_user.id,
            amount=5000.0,
            receipt_file_id=f"live_load_{uuid.uuid4().hex[:8]}",
            status="pending",
        )
        session.add(r)
        await session.commit()
        rid = r.id

    async def _approve():
        ok, _ = await approve_payment_receipt(rid, 1)
        metrics["ok" if ok else "fail"] += 1

    tasks.append(_guarded(_approve()))

    if created_subs:
        uname = created_subs[0]

        async def _extend():
            ok, _ = await extend_subscription_validity(uname, 1)
            metrics["ok" if ok else "fail"] += 1

        tasks.append(_guarded(_extend()))

    t0 = time.perf_counter()
    await asyncio.gather(*tasks, return_exceptions=False)
    elapsed = time.perf_counter() - t0

    assert metrics["ok"] >= 8, f"Too many failures: {metrics}"
    assert elapsed < 120.0, f"Load subset exceeded 120s: {elapsed:.1f}s"
