"""
Live WG stress orchestrator on real MikroTik.

Combines: wallet/receipt Telegram E2E, concurrent purchase/renew, multi-interface
capacity, slot reuse after expiry, cleanup, spillover, and mixed finale load.
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest

from tests.conftest_db import FakeBot
from tests.db.helpers.invariant_checker import (
    check_wg_duplicate_assigned_ips,
)
from tests.live.helpers.chaos_report import new_metrics
from tests.live.helpers.wg_stress_live import (
    active_on_iface,
    assert_no_capacity_violations,
    burst_purchase,
    burst_renew,
    count_ifaces,
    ensure_mt_ready,
    expire_subs_batch,
    fund_users,
    guarded,
    reconcile_guarded,
    seed_stress_users,
    subs_on_iface,
    teardown_stress_run,
    write_stress_report,
)
from tests.live.helpers.wg_wallet_e2e import (
    assert_wallet_has_tx,
    run_wallet_wg_purchase_e2e,
    run_wallet_wg_renewal_e2e,
)
from tests.live.helpers.wg_workflow_live import (
    assert_peer_disabled,
    assert_peer_enabled,
    fetch_latest_wg_sub,
    load_sub,
    run_wg_reconcile,
    seed_wg_usage_at_quota,
    teardown_wg_sub,
)
from vpn_bot.admin_cleanup import AdminCleanup
from vpn_bot.admin_receipt_service import approve_payment_receipt
from vpn_bot.admin_wg_service import add_wg_subscription_data
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from sqlalchemy import delete, select

from vpn_bot.models import PaymentReceipt, User, WireGuardSubscription
from vpn_bot.user_features import finalize_wg_purchase

pytest_plugins = ["tests.live.admin_e2e_conftest"]

pytestmark = [
    pytest.mark.live_mt,
    pytest.mark.live_admin_e2e,
    pytest.mark.wg_stress_live,
]

STRESS_USER_COUNT = int(os.getenv("LIVE_WG_STRESS_USERS", "12"))
STRESS_CONCURRENCY = int(os.getenv("LIVE_WG_STRESS_CONCURRENCY", "4"))
MT_SEM_SIZE = int(os.getenv("LIVE_WG_STRESS_MT_SEM", "2"))


@pytest.fixture
async def user_driver(admin_app, e2e_test_user):
    from tests.helpers.admin_e2e_harness import AdminE2EDriver

    app, bot = admin_app
    driver = AdminE2EDriver(app, bot, e2e_test_user.telegram_id)
    yield driver


@pytest.fixture
async def wg_stress_mt_sem():
    return asyncio.Semaphore(MT_SEM_SIZE)


@pytest.fixture
async def wg_stress_cleanup(live_server, intg_wg_interface_tiny):
    state = {
        "subs": [],
        "tiny_id": intg_wg_interface_tiny.id,
        "server": live_server,
    }
    yield state

    await teardown_stress_run(
        state["subs"],
        live_server,
        tiny_id=state["tiny_id"],
        keep_tiny_iface=True,
    )

    stress_tg_start = 880_003_000
    stress_tg_end = stress_tg_start + STRESS_USER_COUNT
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(
                delete(PaymentReceipt).where(
                    PaymentReceipt.receipt_file_id.like("intg_e2e_wg_stress_%")
                )
            )
            await session.execute(
                delete(WireGuardSubscription).where(
                    WireGuardSubscription.user_id.in_(
                        select(User.id).where(
                            User.telegram_id >= stress_tg_start,
                            User.telegram_id < stress_tg_end,
                        )
                    )
                )
            )
            await session.commit()
    except Exception:
        pass


def _track_sub(state: dict, sub: WireGuardSubscription | None) -> None:
    if sub and sub.id not in {s.id for s in state["subs"]}:
        state["subs"].append(sub)


@pytest.mark.asyncio
async def test_live_wg_stress_full_orchestrator(
    user_driver,
    admin_driver,
    e2e_test_user,
    intg_wg_profile_long_days,
    intg_wg_interface_tiny,
    live_server,
    no_wg_preempt,
    wg_stress_mt_sem,
    wg_stress_cleanup,
):
    tiny_id = intg_wg_interface_tiny.id
    profile = intg_wg_profile_long_days
    price = float(profile.price_toman)
    mgr = get_mikrotik_manager(live_server)
    metrics = new_metrics()
    buy_sem = asyncio.Semaphore(STRESS_CONCURRENCY)

    stress_users = await seed_stress_users(STRESS_USER_COUNT)
    iface_count_start = await count_ifaces(live_server.id)

    # --- Phase 0: Wallet Telegram E2E (receipt → approve → buy → quota → renew) ---
    e2e_sub = await run_wallet_wg_purchase_e2e(
        user_driver, admin_driver, e2e_test_user, profile
    )
    await assert_wallet_has_tx(e2e_test_user.id, min_tx_count=1)

    peer = await asyncio.to_thread(
        mgr.get_wg_peer, e2e_sub.interface.name, e2e_sub.peer_public_key
    )
    assert peer is not None

    await seed_wg_usage_at_quota(e2e_sub.id, at_cap=True, server=live_server)
    await run_wg_reconcile(live_server)
    disabled = await load_sub(e2e_sub.id)
    assert disabled.status == "disabled"
    await assert_peer_disabled(mgr, e2e_sub.interface.name, e2e_sub.peer_public_key)

    renewed = await run_wallet_wg_renewal_e2e(
        user_driver, admin_driver, e2e_test_user, e2e_sub, price
    )
    await assert_peer_enabled(mgr, renewed.interface.name, renewed.peer_public_key)
    await assert_wallet_has_tx(e2e_test_user.id, min_tx_count=2)
    # E2E sub lives on tiny fixture — remove before capacity stress phases.
    await teardown_wg_sub(renewed, live_server)

    # --- Phase A: Burst concurrent purchases ---
    burst_users = stress_users[: max(STRESS_CONCURRENCY, 6)]
    await fund_users(burst_users)
    phase_a = await burst_purchase(burst_users, profile.id, buy_sem, metrics)
    assert not any(not ok for _, ok, _ in phase_a), phase_a
    assert all(ok for _, ok, _ in phase_a)

    for _, _, sub in phase_a:
        _track_sub(wg_stress_cleanup, sub)

    assert await active_on_iface(tiny_id) == 3
    assert await count_ifaces(live_server.id) >= iface_count_start + 1
    await assert_no_capacity_violations(live_server.id)

    tiny_subs = await subs_on_iface(tiny_id)
    occupying = [s for s in tiny_subs if s.status in ("active", "pending")]
    assert len(occupying) == 3

    # --- Phase B: Concurrent renew + add_data + reconcile (one sub only) ---
    renew_target = occupying[0]
    expire_targets = occupying[1:3]
    assert len(expire_targets) == 2
    b_tasks = [
        burst_renew([renew_target], buy_sem, metrics),
        guarded(
            add_wg_subscription_data(renew_target.id, 1),
            wg_stress_mt_sem,
            metrics,
            "add_data",
        ),
        reconcile_guarded(live_server, wg_stress_mt_sem, metrics),
    ]
    b_results = await asyncio.gather(*b_tasks, return_exceptions=True)
    exc = [r for r in b_results if isinstance(r, Exception)]
    assert not exc, exc
    assert await active_on_iface(tiny_id) == 3

    # --- Phase C: Expire 2 subs on tiny (not renewed in phase B) ---
    expire_ids = [s.id for s in expire_targets]
    expired_peer_keys: list[tuple[str, str]] = []
    for sid in expire_ids:
        row = await load_sub(sid)
        expired_peer_keys.append((row.interface.name, row.peer_public_key))

    await expire_subs_batch(expire_ids)
    await ensure_mt_ready(live_server)
    await run_wg_reconcile(live_server)

    for sid in expire_ids:
        row = await load_sub(sid)
        assert row.status == "expired"
        await assert_peer_disabled(mgr, row.interface.name, row.peer_public_key)

    assert await active_on_iface(tiny_id) == 1

    # --- Phase D: Slot reuse (2 parallel buys on freed tiny slots) ---
    iface_before_d = await count_ifaces(live_server.id)
    reuse_users = stress_users[6:8]
    await fund_users(reuse_users)
    phase_d = await burst_purchase(reuse_users, profile.id, buy_sem, metrics)
    assert all(ok for _, ok, _ in phase_d), phase_d

    for _, _, sub in phase_d:
        _track_sub(wg_stress_cleanup, sub)
        assert sub.interface_id == tiny_id, (
            f"expected reuse on tiny {tiny_id}, got {sub.interface_id}"
        )

    assert await active_on_iface(tiny_id) == 3
    assert await count_ifaces(live_server.id) == iface_before_d

    # --- Phase E: Cleanup delete expired ---
    deleted = await AdminCleanup.clean_expired_wg_subscriptions(
        bot=FakeBot(), mode="delete", seconds=0
    )
    assert deleted >= 2

    for sid in expire_ids:
        assert await load_sub(sid) is None

    for iface_name, pubkey in expired_peer_keys:
        peer = await asyncio.to_thread(mgr.get_wg_peer, iface_name, pubkey)
        assert peer is None, f"expired peer {pubkey[:12]} still on {iface_name}"

    assert await active_on_iface(tiny_id) == 3

    # --- Phase F: Spillover when tiny is full ---
    await ensure_mt_ready(live_server)
    spill_user = stress_users[8]
    await fund_users([spill_user])
    assert await finalize_wg_purchase(
        spill_user.telegram_id, profile.id, context=None, is_tg_id=True
    )
    spill_sub = await fetch_latest_wg_sub(spill_user.id, profile.id)
    assert spill_sub is not None
    assert spill_sub.interface_id != tiny_id
    _track_sub(wg_stress_cleanup, spill_sub)
    assert await active_on_iface(tiny_id) == 3

    # --- Phase G: Mixed finale load ---
    await ensure_mt_ready(live_server)
    finale_users = stress_users[9:12]
    await fund_users(finale_users)

    receipt_ids: list[int] = []
    async with AsyncSessionLocal() as session:
        for i, u in enumerate(finale_users[:2]):
            r = PaymentReceipt(
                user_id=u.id,
                amount=price,
                receipt_file_id=f"intg_e2e_wg_stress_finale_{uuid.uuid4().hex[:8]}",
                status="pending",
            )
            session.add(r)
            await session.flush()
            receipt_ids.append(r.id)
        await session.commit()

    active_for_renew = [s for s in await subs_on_iface(tiny_id) if s.status == "active"][:2]

    async def _approve(rid: int):
        return await approve_payment_receipt(rid, admin_id=1)

    # Serialize MT-touching finale work (shared router API pool); receipt approvals stay parallel.
    finale_mt = asyncio.Lock()

    finale_buy_sem = asyncio.Semaphore(1)

    async def _g_purchase():
        async with finale_mt:
            return await burst_purchase(
                finale_users, profile.id, finale_buy_sem, metrics
            )

    async def _g_renew():
        async with finale_mt:
            return await burst_renew(
                active_for_renew, finale_buy_sem, metrics
            )

    async def _g_reconcile():
        async with finale_mt:
            return await reconcile_guarded(live_server, wg_stress_mt_sem, metrics)

    g_tasks = [
        _g_purchase(),
        _g_renew(),
        _g_reconcile(),
        guarded(_approve(receipt_ids[0]), wg_stress_mt_sem, metrics, "receipt_approve"),
        guarded(_approve(receipt_ids[1]), wg_stress_mt_sem, metrics, "receipt_approve"),
    ]
    g_results = await asyncio.gather(*g_tasks, return_exceptions=True)
    g_exc = [r for r in g_results if isinstance(r, Exception)]
    assert not g_exc, g_exc

    # Track finale subs
    for u in finale_users:
        sub = await fetch_latest_wg_sub(u.id, profile.id)
        _track_sub(wg_stress_cleanup, sub)

    await assert_no_capacity_violations(live_server.id)
    assert await check_wg_duplicate_assigned_ips() == []

    summary = write_stress_report(metrics)
    assert summary["fail"] == 0, summary
    assert summary["success_rate"] == 1.0

