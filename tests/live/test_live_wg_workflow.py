"""
Live WireGuard workflow tests on real MikroTik.

Covers: purchase, config, traffic quota, time expiry (both models),
deactivation, service renewal, and admin cleanup warn/delete.
"""

import asyncio

import pytest
from sqlalchemy import select

from tests.conftest_db import FakeBot
from tests.live.helpers.wg_workflow_live import (
    assert_peer_disabled,
    assert_peer_enabled,
    backdate_wg_expiry,
    fetch_latest_wg_sub,
    load_sub,
    renew_wg_sub_for_test,
    run_wg_reconcile,
    seed_wg_usage_at_quota,
    set_wg_deletion_warning_sent,
    teardown_wg_sub,
)
from vpn_bot.admin_cleanup import AdminCleanup
from vpn_bot.admin_wg_service import generate_wg_subscription_config
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.models import Transaction, User, WireGuardSubscription
from vpn_bot.user_features import finalize_wg_purchase

pytestmark = pytest.mark.live_mt


async def _purchase(intg_user, profile):
    balance_before = intg_user.wallet_balance
    ok = await finalize_wg_purchase(
        intg_user.telegram_id,
        profile.id,
        context=None,
        is_tg_id=True,
    )
    assert ok is True
    sub = await fetch_latest_wg_sub(intg_user.id, profile.id)
    assert sub is not None
    return sub, balance_before


@pytest.mark.asyncio
async def test_wg_purchase_peer_db_and_config(
    intg_user, intg_wg_profile, intg_wg_interface, live_server
):
    sub, balance_before = await _purchase(intg_user, intg_wg_profile)

    assert sub.status == "active"
    assert sub.unique_identifier.startswith("WG-")
    assert sub.bytes_remaining == intg_wg_profile.volume_gb * 1024**3
    assert sub.peer_public_key
    assert sub.assigned_ip

    async with AsyncSessionLocal() as session:
        user = await session.get(User, intg_user.id)
        assert user.wallet_balance == balance_before - intg_wg_profile.price_toman
        tx = (
            await session.execute(
                select(Transaction)
                .where(Transaction.user_id == intg_user.id)
                .order_by(Transaction.id.desc())
            )
        ).scalars().first()
        assert tx is not None
        assert tx.amount == -intg_wg_profile.price_toman

    mgr = get_mikrotik_manager(live_server)
    peers = await asyncio.to_thread(mgr.get_all_wg_peers, sub.interface.name)
    peer_keys = [p.get("public-key") for p in (peers or [])]
    assert sub.peer_public_key in peer_keys

    cfg = await generate_wg_subscription_config(sub.id)
    assert cfg and cfg.get("config_text")
    assert sub.assigned_ip in cfg["config_text"]
    assert sub.interface.public_key in cfg["config_text"]

    await teardown_wg_sub(sub, live_server)


@pytest.mark.asyncio
async def test_wg_sync_baseline_aligns_router_counters(
    intg_user, intg_wg_profile, intg_wg_interface, live_server
):
    sub, _ = await _purchase(intg_user, intg_wg_profile)
    mgr = get_mikrotik_manager(live_server)

    await run_wg_reconcile(live_server)

    stats = await asyncio.to_thread(
        mgr.get_wg_peer_stats, sub.interface.name, sub.peer_public_key
    )
    refreshed = await load_sub(sub.id)
    assert refreshed.total_bytes_rx == 0
    assert refreshed.total_bytes_tx == 0
    assert refreshed.last_router_rx == int((stats or {}).get("rx", 0))
    assert refreshed.last_router_tx == int((stats or {}).get("tx", 0))

    await teardown_wg_sub(sub, live_server)


@pytest.mark.asyncio
async def test_wg_quota_exhaustion_disables_on_router(
    intg_user, intg_wg_profile_long_days, intg_wg_interface, live_server
):
    sub, _ = await _purchase(intg_user, intg_wg_profile_long_days)
    mgr = get_mikrotik_manager(live_server)

    await seed_wg_usage_at_quota(sub.id, at_cap=False, server=live_server)
    await run_wg_reconcile(live_server)
    mid = await load_sub(sub.id)
    assert mid.status == "active"
    await assert_peer_enabled(mgr, sub.interface.name, sub.peer_public_key)

    await seed_wg_usage_at_quota(sub.id, at_cap=True, server=live_server)
    await run_wg_reconcile(live_server)

    refreshed = await load_sub(sub.id)
    assert refreshed.status == "disabled"
    await assert_peer_disabled(mgr, sub.interface.name, sub.peer_public_key)

    await teardown_wg_sub(sub, live_server)


@pytest.mark.asyncio
async def test_wg_time_expiry_disables_on_router(
    intg_user, intg_wg_profile, intg_wg_interface, live_server
):
    sub, _ = await _purchase(intg_user, intg_wg_profile)
    mgr = get_mikrotik_manager(live_server)

    await backdate_wg_expiry(sub.id, hours_ago=2)
    await run_wg_reconcile(live_server)

    refreshed = await load_sub(sub.id)
    assert refreshed.status == "expired"
    await assert_peer_disabled(mgr, sub.interface.name, sub.peer_public_key)

    await run_wg_reconcile(live_server)
    again = await load_sub(sub.id)
    assert again.status == "expired"

    await teardown_wg_sub(sub, live_server)


@pytest.mark.asyncio
async def test_wg_unlimited_traffic_only_time_expires(
    intg_user, intg_wg_profile_unlimited, intg_wg_interface, live_server
):
    sub, _ = await _purchase(intg_user, intg_wg_profile_unlimited)
    assert sub.bytes_remaining == 0
    mgr = get_mikrotik_manager(live_server)

    async with AsyncSessionLocal() as session:
        row = await session.get(WireGuardSubscription, sub.id)
        row.total_bytes_rx = 50 * 1024**3
        row.total_bytes_tx = 50 * 1024**3
        await session.commit()

    await run_wg_reconcile(live_server)
    mid = await load_sub(sub.id)
    assert mid.status == "active"
    await assert_peer_enabled(mgr, sub.interface.name, sub.peer_public_key)

    await backdate_wg_expiry(sub.id, hours_ago=2)
    await run_wg_reconcile(live_server)

    refreshed = await load_sub(sub.id)
    assert refreshed.status == "expired"
    await assert_peer_disabled(mgr, sub.interface.name, sub.peer_public_key)

    await teardown_wg_sub(sub, live_server)


@pytest.mark.asyncio
async def test_wg_both_expired_time_wins_over_quota(
    intg_user, intg_wg_profile, intg_wg_interface, live_server
):
    sub, _ = await _purchase(intg_user, intg_wg_profile)
    mgr = get_mikrotik_manager(live_server)

    await seed_wg_usage_at_quota(sub.id, at_cap=True, server=live_server)
    await backdate_wg_expiry(sub.id, hours_ago=2)
    await run_wg_reconcile(live_server)

    refreshed = await load_sub(sub.id)
    assert refreshed.status == "expired"
    await assert_peer_disabled(mgr, sub.interface.name, sub.peer_public_key)

    await teardown_wg_sub(sub, live_server)


@pytest.mark.asyncio
async def test_wg_renewal_after_quota_reenables_peer(
    intg_user, intg_wg_profile_long_days, intg_wg_interface, live_server
):
    sub, _ = await _purchase(intg_user, intg_wg_profile_long_days)
    mgr = get_mikrotik_manager(live_server)

    await seed_wg_usage_at_quota(sub.id, at_cap=True, server=live_server)
    await run_wg_reconcile(live_server)
    assert (await load_sub(sub.id)).status == "disabled"

    ok, msg = await renew_wg_sub_for_test(intg_user.id, sub.id)
    assert ok is True, msg

    refreshed = await load_sub(sub.id)
    assert refreshed.status == "active"
    assert refreshed.total_bytes_rx == 0
    assert refreshed.total_bytes_tx == 0
    assert refreshed.bytes_remaining == intg_wg_profile_long_days.volume_gb * 1024**3
    await assert_peer_enabled(mgr, sub.interface.name, sub.peer_public_key)

    await teardown_wg_sub(sub, live_server)


@pytest.mark.asyncio
async def test_wg_renewal_after_expiry_reenables_peer(
    intg_user, intg_wg_profile, intg_wg_interface, live_server
):
    sub, _ = await _purchase(intg_user, intg_wg_profile)
    mgr = get_mikrotik_manager(live_server)
    old_expiry = sub.expiry_date

    await backdate_wg_expiry(sub.id, hours_ago=2)
    await run_wg_reconcile(live_server)
    assert (await load_sub(sub.id)).status == "expired"

    ok, msg = await renew_wg_sub_for_test(intg_user.id, sub.id)
    assert ok is True, msg

    refreshed = await load_sub(sub.id)
    assert refreshed.status == "active"
    assert refreshed.expiry_date > old_expiry
    await assert_peer_enabled(mgr, sub.interface.name, sub.peer_public_key)

    await teardown_wg_sub(sub, live_server)


@pytest.mark.asyncio
async def test_wg_cleanup_warn_then_delete(
    intg_user, intg_wg_profile, intg_wg_interface, live_server
):
    sub, _ = await _purchase(intg_user, intg_wg_profile)
    mgr = get_mikrotik_manager(live_server)
    pubkey = sub.peer_public_key
    iface_name = sub.interface.name
    uid = sub.unique_identifier

    await backdate_wg_expiry(sub.id, hours_ago=240)
    await run_wg_reconcile(live_server)
    assert (await load_sub(sub.id)).status == "expired"

    warned = await AdminCleanup.clean_expired_wg_subscriptions(
        bot=FakeBot(), mode="warn", seconds=0
    )
    assert warned >= 1

    warned_sub = await load_sub(sub.id)
    assert warned_sub.deletion_warning_sent_at is not None
    await assert_peer_disabled(mgr, iface_name, pubkey)

    await set_wg_deletion_warning_sent(sub.id, hours_ago=25)
    deleted = await AdminCleanup.clean_expired_wg_subscriptions(
        bot=None, mode="delete", seconds=0
    )
    assert deleted >= 1

    peer = await asyncio.to_thread(mgr.get_wg_peer, iface_name, pubkey)
    assert peer is None

    async with AsyncSessionLocal() as session:
        row = await session.get(WireGuardSubscription, sub.id)
        assert row is None

    try:
        await asyncio.to_thread(mgr.remove_wg_queue, uid)
    except Exception:
        pass
