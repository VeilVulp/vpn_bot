"""Simulate 100 concurrent operations with mock MikroTik."""

import asyncio
import time

import pytest
from sqlalchemy import select

from vpn_bot.admin_receipt_service import approve_payment_receipt
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import PaymentReceipt, Server, User
from tests.db.helpers.invariant_checker import run_all_checks
from vpn_bot.user_features import checkout_subscription, finalize_wg_purchase
from vpn_bot.utils import get_profile_price
from vpn_bot.wallet_manager import WalletManager

pytestmark = [pytest.mark.load, pytest.mark.db]


async def _seed_users(count: int, balance: float, server_id: int) -> list[User]:
    import random

    users = []
    async with AsyncSessionLocal() as session:
        for i in range(count):
            u = User(
                telegram_id=random.randint(710_000_000_000, 719_999_999_999),
                username=f"load_{i}",
                wallet_balance=balance,
            )
            session.add(u)
            users.append(u)
        await session.commit()
        for u in users:
            await session.refresh(u)
    return users


@pytest.mark.asyncio
async def test_concurrent_100_mixed_ops(
    db_initialized, mock_mikrotik, mock_server, db_ovpn_profile, db_wg_profile, db_wg_interface
):
    price = await get_profile_price(db_ovpn_profile)
    users = await _seed_users(55, price * 2, mock_server.id)
    race_users = users[:10]

    receipt_ids = []
    async with AsyncSessionLocal() as session:
        for i in range(15):
            u = users[40 + (i % 15)]
            r = PaymentReceipt(
                user_id=u.id,
                amount=10_000.0,
                receipt_file_id=f"load_rcpt_{i}",
                status="pending",
            )
            session.add(r)
        await session.commit()
        res = await session.execute(
            select(PaymentReceipt).where(PaymentReceipt.receipt_file_id.like("load_rcpt_%"))
        )
        receipt_ids = [r.id for r in res.scalars().all()]

    metrics = {"ok": 0, "fail": 0, "errors": [], "latencies": []}
    sem = asyncio.Semaphore(10)

    async def _timed(coro):
        t0 = time.perf_counter()
        async with sem:
            try:
                r = await coro
                metrics["latencies"].append(time.perf_counter() - t0)
                return r
            except Exception as e:
                metrics["errors"].append(str(e))
                metrics["latencies"].append(time.perf_counter() - t0)
                raise

    tasks = []

    for i, u in enumerate(users[:40]):
        tasks.append(
            _timed(checkout_subscription(u.telegram_id, db_ovpn_profile.id))
        )

    for u in race_users:
        tasks.append(_timed(checkout_subscription(u.telegram_id, db_ovpn_profile.id)))
        tasks.append(_timed(checkout_subscription(u.telegram_id, db_ovpn_profile.id)))

    for i in range(15):
        u = users[40 + i]
        tasks.append(_timed(WalletManager.deposit(u.id, 5000.0, f"load dep {i}")))

    for rid in receipt_ids:
        tasks.append(_timed(approve_payment_receipt(rid, 1)))

    wg_users = users[50:60] if len(users) >= 60 else users[:10]
    for u in wg_users:
        tasks.append(
            _timed(
                finalize_wg_purchase(
                    u.telegram_id, db_wg_profile.id, context=None, is_tg_id=True
                )
            )
        )

    while len(tasks) < 100:
        u = users[len(tasks) % len(users)]
        tasks.append(_timed(WalletManager.deposit(u.id, 100.0, "pad")))

    results = await asyncio.gather(*tasks[:100], return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            metrics["fail"] += 1
        elif r is True or (isinstance(r, tuple) and r[0]):
            metrics["ok"] += 1
        else:
            metrics["fail"] += 1

    user_ids = [u.id for u in users]
    await run_all_checks(
        user_ids=user_ids,
        receipt_ids=receipt_ids,
        wg_server_id=mock_server.id,
    )

    pool_errs = [e for e in metrics["errors"] if "QueuePool" in e]
    assert len(pool_errs) / max(len(tasks), 1) <= 0.15, f"Too many pool timeouts: {len(pool_errs)}"
    lat = sorted(metrics["latencies"])
    p95 = lat[int(len(lat) * 0.95)] if lat else 0
    assert p95 < 60.0, f"p95 latency too high: {p95}s"
