"""
Live concurrent chaos: admin + user + maintenance on real MikroTik.

Production-like parallel load with full cleanup of INTG/u* test data.
"""

from __future__ import annotations

import asyncio
import time
import uuid

import pytest
from sqlalchemy import select

from vpn_bot.admin_cleanup import AdminCleanup
from vpn_bot.admin_receipt_service import approve_payment_receipt
from vpn_bot.admin_server_service import get_server_health_status
from vpn_bot.admin_subscription_service import extend_subscription_validity, toggle_subscription_status
from vpn_bot.admin_user_service import update_user_balance
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import PaymentReceipt, Subscription
from vpn_bot.settings_utils import get_admin_setting, set_admin_setting
from vpn_bot.sync_manager import SyncManager
from tests.db.helpers.invariant_checker import run_all_checks
from tests.live.helpers.chaos_report import record_op, write_report
from vpn_bot.user_features import checkout_subscription, finalize_wg_purchase
from vpn_bot.wallet_manager import WalletManager

pytest_plugins = ["tests.live.chaos_conftest"]

pytestmark = [pytest.mark.live_mt, pytest.mark.chaos_live]


async def _guarded(mt_sem, metrics, chaos_cleanup, op_name: str, coro):
    t0 = time.perf_counter()
    try:
        async with mt_sem:
            result = await coro
        record_op(metrics, op_name, True, time.perf_counter() - t0)
        return result
    except Exception as e:
        record_op(metrics, op_name, False, time.perf_counter() - t0, str(e))
        raise


@pytest.mark.asyncio
async def test_live_chaos_full_stack_parallel(
    live_server,
    chaos_users,
    chaos_ovpn_profile,
    intg_wg_profile,
    intg_wg_interface,
    chaos_metrics,
    chaos_cleanup,
    mt_sem,
    fake_bot,
):
    """~28 parallel tasks: user purchases, admin ops, sync, cleanup."""
    users = chaos_users
    chaos_cleanup["user_ids"] = [u.id for u in users]
    chaos_cleanup["chaos_tg_ids"] = [u.telegram_id for u in users]
    chaos_cleanup["profile_ids"] = [chaos_ovpn_profile.id]
    created_usernames: list[str] = []
    receipt_ids: list[int] = []
    tasks = []

    async def _ovpn(u, idx):
        ok, sub, err = await checkout_subscription(u.telegram_id, chaos_ovpn_profile.id)
        if ok and sub:
            created_usernames.append(sub.mikrotik_username)
            chaos_cleanup["usernames"].append(sub.mikrotik_username)
        return ok, err

    async def _wg(u):
        return await finalize_wg_purchase(
            u.telegram_id, intg_wg_profile.id, context=None, is_tg_id=True
        )

    async def _deposit(u):
        return await WalletManager.deposit(u.id, 2000.0, "chaos deposit")

    for i in range(6):
        u = users[i % len(users)]
        tasks.append(_guarded(mt_sem, chaos_metrics, chaos_cleanup, f"ovpn_{i}", _ovpn(u, i)))

    for i in range(5):
        u = users[(i + 1) % len(users)]
        tasks.append(_guarded(mt_sem, chaos_metrics, chaos_cleanup, f"wg_{i}", _wg(u)))

    for i in range(4):
        u = users[(i + 2) % len(users)]
        tasks.append(_guarded(mt_sem, chaos_metrics, chaos_cleanup, f"deposit_{i}", _deposit(u)))

    race_user = users[0]

    async def _race_checkout():
        return await asyncio.gather(
            checkout_subscription(race_user.telegram_id, chaos_ovpn_profile.id),
            checkout_subscription(race_user.telegram_id, chaos_ovpn_profile.id),
            return_exceptions=True,
        )

    tasks.append(_guarded(mt_sem, chaos_metrics, chaos_cleanup, "race_checkout", _race_checkout()))

    async def _make_receipts():
        ids = []
        async with AsyncSessionLocal() as session:
            for i in range(3):
                r = PaymentReceipt(
                    user_id=users[i].id,
                    amount=3000.0,
                    receipt_file_id=f"chaos_rcpt_{uuid.uuid4().hex[:8]}",
                    status="pending",
                )
                session.add(r)
            await session.commit()
            res = await session.execute(
                select(PaymentReceipt).where(
                    PaymentReceipt.receipt_file_id.like("chaos_rcpt_%")
                )
            )
            for r in res.scalars().all():
                ids.append(r.id)
        chaos_cleanup["receipt_ids"].extend(ids)
        return ids

    receipt_ids = await _make_receipts()

    for rid in receipt_ids:
        tasks.append(
            _guarded(
                mt_sem,
                chaos_metrics,
                chaos_cleanup,
                f"approve_{rid}",
                approve_payment_receipt(rid, 1),
            )
        )

    if created_usernames:
        uname = created_usernames[0]

        tasks.append(
            _guarded(
                mt_sem,
                chaos_metrics,
                chaos_cleanup,
                "extend_sub",
                extend_subscription_validity(uname, 1),
            )
        )
        tasks.append(
            _guarded(
                mt_sem,
                chaos_metrics,
                chaos_cleanup,
                "toggle_sub",
                toggle_subscription_status(uname),
            )
        )

    async def _settings():
        await set_admin_setting("chaos_sync_interval", "1h")
        await set_admin_setting("chaos_currency_test", "TOMAN")
        v = await get_admin_setting("chaos_sync_interval")
        return v

    tasks.append(_guarded(mt_sem, chaos_metrics, chaos_cleanup, "admin_settings", _settings()))

    tasks.append(
        _guarded(
            mt_sem,
            chaos_metrics,
            chaos_cleanup,
            "admin_balance",
            update_user_balance(users[3].id, users[3].wallet_balance, log_adjust=False),
        )
    )

    tasks.append(
        _guarded(mt_sem, chaos_metrics, chaos_cleanup, "sync_1", SyncManager.sync_all_servers())
    )
    tasks.append(
        _guarded(mt_sem, chaos_metrics, chaos_cleanup, "sync_2", SyncManager.sync_all_servers())
    )

    async def _cleanup_warn():
        return await AdminCleanup.clean_expired_subscriptions(
            bot=fake_bot, mode="warn", seconds=86400
        )

    async def _cleanup_delete():
        return await AdminCleanup.clean_expired_subscriptions(
            bot=None, mode="delete", seconds=0
        )

    tasks.append(
        _guarded(mt_sem, chaos_metrics, chaos_cleanup, "cleanup_warn", _cleanup_warn())
    )
    tasks.append(
        _guarded(mt_sem, chaos_metrics, chaos_cleanup, "cleanup_delete", _cleanup_delete())
    )

    tasks.append(
        _guarded(
            mt_sem,
            chaos_metrics,
            chaos_cleanup,
            "health",
            get_server_health_status(live_server),
        )
    )

    results = await asyncio.gather(*tasks, return_exceptions=True)
    for r in results:
        if isinstance(r, Exception):
            chaos_metrics["errors"].append(str(r))

    summary = write_report(chaos_metrics)

    if created_usernames:
        await run_all_checks(
            user_ids=[u.id for u in users],
            receipt_ids=receipt_ids,
            server_id=live_server.id,
            active_usernames=created_usernames,
        )
    else:
        await run_all_checks(user_ids=[u.id for u in users], receipt_ids=receipt_ids)

    assert summary["ok"] >= 18, f"Too few successes: {summary}"
    assert summary["fail"] <= 12, f"Too many failures: {summary}"
    assert summary["success_rate"] >= 0.55


@pytest.mark.asyncio
async def test_live_chaos_sync_during_purchase(
    live_server,
    chaos_users,
    chaos_ovpn_profile,
    chaos_metrics,
    chaos_cleanup,
    mt_sem,
):
    """Five parallel checkouts while sync runs twice."""
    users = chaos_users[:5]
    chaos_cleanup["user_ids"] = [u.id for u in users]
    created: list[str] = []

    async def _buy(u):
        ok, sub, _ = await checkout_subscription(u.telegram_id, chaos_ovpn_profile.id)
        if ok and sub:
            created.append(sub.mikrotik_username)
            chaos_cleanup["usernames"].append(sub.mikrotik_username)
        return ok

    tasks = [
        _guarded(mt_sem, chaos_metrics, chaos_cleanup, f"sync_buy_{i}", _buy(users[i]))
        for i in range(5)
    ]
    tasks.append(
        _guarded(mt_sem, chaos_metrics, chaos_cleanup, "sync_a", SyncManager.sync_all_servers())
    )
    tasks.append(
        _guarded(mt_sem, chaos_metrics, chaos_cleanup, "sync_b", SyncManager.sync_all_servers())
    )

    await asyncio.gather(*tasks, return_exceptions=True)

    await SyncManager.sync_all_servers()
    await run_all_checks(
        user_ids=[u.id for u in users],
        server_id=live_server.id,
        active_usernames=created,
    )


@pytest.mark.asyncio
async def test_live_chaos_cleanup_vs_active_purchase(
    live_server,
    chaos_users,
    chaos_ovpn_profile,
    chaos_expired_ovpn,
    chaos_metrics,
    chaos_cleanup,
    mt_sem,
    fake_bot,
):
    """Expired sub warn + three new purchases in parallel."""
    users = chaos_users[1:4]
    chaos_cleanup["user_ids"] = [u.id for u in users] + [chaos_expired_ovpn["user"].id]
    chaos_cleanup["usernames"].append(chaos_expired_ovpn["username"])
    created: list[str] = []

    async def _buy(u):
        ok, sub, _ = await checkout_subscription(u.telegram_id, chaos_ovpn_profile.id)
        if ok and sub:
            created.append(sub.mikrotik_username)
            chaos_cleanup["usernames"].append(sub.mikrotik_username)
        return ok

    tasks = [
        _guarded(mt_sem, chaos_metrics, chaos_cleanup, "cleanup_warn", AdminCleanup.clean_expired_subscriptions(bot=fake_bot, mode="warn", seconds=86400)),
        _guarded(mt_sem, chaos_metrics, chaos_cleanup, "buy_0", _buy(users[0])),
        _guarded(mt_sem, chaos_metrics, chaos_cleanup, "buy_1", _buy(users[1])),
        _guarded(mt_sem, chaos_metrics, chaos_cleanup, "buy_2", _buy(users[2])),
    ]
    await asyncio.gather(*tasks, return_exceptions=True)

    await SyncManager.sync_all_servers()

    async with AsyncSessionLocal() as session:
        exp_sub = await session.execute(
            select(Subscription).where(
                Subscription.mikrotik_username == chaos_expired_ovpn["username"]
            )
        )
        expired = exp_sub.scalar_one_or_none()
        if expired:
            assert expired.status in ("expired", "active") or expired.deletion_warning_sent_at

    await run_all_checks(
        user_ids=[u.id for u in users],
        server_id=live_server.id,
        active_usernames=created,
    )
