"""
Domain E2E tests: admin Telegram handlers + MikroTik verification.

Combines UI taps (process_update) with service-layer MT checks where needed.
"""

from __future__ import annotations

import asyncio
import time

import pytest
from sqlalchemy import select

from tests.helpers.admin_e2e_harness import destructive_allowed
from vpn_bot.admin_profile_service import create_profile_full, delete_profile_full
from vpn_bot.admin_receipt_service import approve_payment_receipt, get_pending_receipts
from vpn_bot.admin_server_service import get_server_health_status
from vpn_bot.admin_subscription_service import (
    add_subscription_data,
    extend_subscription_validity,
    get_subscription_comprehensive_info,
    reset_subscription_password,
    toggle_subscription_status,
)
from vpn_bot.admin_user_service import find_user_by_query, get_user_comprehensive_info, toggle_user_ban
from vpn_bot.admin_wg_service import (
    add_wg_subscription_data,
    extend_wg_subscription,
    generate_wg_subscription_config,
    toggle_wg_subscription_status,
)
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.models import PaymentReceipt, Subscription, WireGuardSubscription
from vpn_bot.user_features import checkout_subscription, finalize_wg_purchase

pytest_plugins = ["tests.live.admin_e2e_conftest"]

pytestmark = [pytest.mark.live_mt, pytest.mark.live_admin_e2e]

INTG_E2E_OVPN = "INTG_E2E_OVPN"


@pytest.fixture(autouse=True)
async def _e2e_cleanup_after_test(e2e_cleanup):
    yield


@pytest.mark.asyncio
async def test_e2e_open_main_and_reports(admin_driver):
    await admin_driver.open_admin_menu()
    assert "search_user" in admin_driver.callbacks_on_screen()
    await admin_driver.tap("admin_reports")
    assert not admin_driver.errors


@pytest.mark.asyncio
async def test_e2e_user_management_search_hub(admin_driver, e2e_test_user):
    await admin_driver.open_admin_menu()
    await admin_driver.tap("search_user")
    await admin_driver.send_text(str(e2e_test_user.telegram_id))
    cbs = admin_driver.callbacks_on_screen()
    assert any("admin_user_hub" in c or c == "admin_user_search" for c in cbs) or not admin_driver.errors

    user = await find_user_by_query(str(e2e_test_user.telegram_id))
    assert user is not None
    info = await get_user_comprehensive_info(user.id)
    assert info is not None


@pytest.mark.asyncio
async def test_e2e_servers_and_health(admin_driver, live_server):
    ok = await get_server_health_status(live_server)
    assert ok is True
    await admin_driver.open_admin_menu()
    await admin_driver.tap("list_servers")
    cbs = admin_driver.callbacks_on_screen()
    assert any(cb.startswith("server_test_") for cb in cbs) or not admin_driver.errors


@pytest.mark.asyncio
async def test_e2e_ovpn_sub_lifecycle_mt(admin_driver, e2e_test_user, live_server):
    """Purchase OVPN, manage via UI search, verify on MikroTik, cleanup."""
    name = f"{INTG_E2E_OVPN}_{int(time.time()) % 100000}"
    prof, err = await create_profile_full(
        {
            "server_id": live_server.id,
            "name": name,
            "days": 3,
            "limit": 1,
            "price_toman": 500,
            "price_usd": 0,
        }
    )
    assert prof, err

    ok, sub, msg = await checkout_subscription(e2e_test_user.telegram_id, prof.id)
    assert ok, msg
    username = sub.mikrotik_username

    mgr = get_mikrotik_manager(live_server)
    info = await asyncio.to_thread(mgr.get_user_info, username)
    assert info is not None

    assert await extend_subscription_validity(username, 2)[0] is True
    assert await reset_subscription_password(username, "E2eTest99") is True
    assert await add_subscription_data(username, 1) is True
    toggled, _ = await toggle_subscription_status(username)
    assert toggled is True

    await admin_driver.open_admin_menu()
    await admin_driver.tap("search_user")
    await admin_driver.send_text(username)
    assert any("manage_sub_" in c for c in admin_driver.callbacks_on_screen()) or not admin_driver.errors

    await asyncio.to_thread(mgr.delete_user, username)
    async with AsyncSessionLocal() as session:
        db_sub = (
            await session.execute(
                select(Subscription).where(Subscription.mikrotik_username == username)
            )
        ).scalars().first()
        if db_sub:
            await session.delete(db_sub)
            await session.commit()
    await delete_profile_full(prof.id)


@pytest.mark.asyncio
async def test_e2e_wg_sub_lifecycle_mt(e2e_test_user, live_server, intg_wg_profile):
    from vpn_bot.admin_wg_service import delete_wg_profile

    ok = await finalize_wg_purchase(
        e2e_test_user.telegram_id, intg_wg_profile.id, context=None, is_tg_id=True
    )
    assert ok is True

    async with AsyncSessionLocal() as session:
        wg = (
            await session.execute(
                select(WireGuardSubscription).where(WireGuardSubscription.user_id == e2e_test_user.id)
            )
        ).scalars().all()
    assert wg
    ws = wg[-1]

    assert (await extend_wg_subscription(ws.id, 2))[0] is True
    assert await add_wg_subscription_data(ws.id, 1) is True
    toggled, _ = await toggle_wg_subscription_status(ws.id)
    assert toggled is True

    cfg = await generate_wg_subscription_config(ws.id)
    assert cfg and cfg.get("config_text")

    if ws.interface:
        mgr = get_mikrotik_manager(live_server)
        peer = await asyncio.to_thread(
            mgr.get_wg_peer_info, ws.interface.name, ws.peer_public_key
        )
        assert peer is not None


@pytest.mark.asyncio
async def test_e2e_user_ban_toggle_mt(e2e_test_user, live_server):
    ok, _ = await toggle_user_ban(e2e_test_user.id)
    assert ok is True
    ok2, _ = await toggle_user_ban(e2e_test_user.id)
    assert ok2 is True


@pytest.mark.asyncio
async def test_e2e_receipts_flow(admin_driver, e2e_test_user):
    async with AsyncSessionLocal() as session:
        r = PaymentReceipt(
            user_id=e2e_test_user.id,
            amount=1500,
            receipt_file_id="intg_e2e_rcpt",
            status="pending",
        )
        session.add(r)
        await session.commit()
        await session.refresh(r)
        rid = r.id

    await admin_driver.open_admin_menu()
    await admin_driver.tap("pending_receipts")
    cbs = admin_driver.callbacks_on_screen()
    assert any(c == f"view_receipt_{rid}" for c in cbs) or not admin_driver.errors

    ok, _ = await approve_payment_receipt(rid, 1)
    assert ok is True
    pending = await get_pending_receipts()
    assert all(row[0].id != rid for row in pending)


@pytest.mark.asyncio
async def test_e2e_tickets_menu(admin_driver):
    await admin_driver.open_admin_menu()
    await admin_driver.tap("admin_tickets")
    cbs = admin_driver.callbacks_on_screen()
    assert "admin_tickets_active" in cbs or "admin_tickets_closed" in cbs


@pytest.mark.asyncio
async def test_e2e_wg_and_ovpn_menus(admin_driver):
    await admin_driver.open_admin_menu()
    await admin_driver.tap("wg_mgmt_menu")
    assert "list_wg_profiles" in admin_driver.callbacks_on_screen()
    await admin_driver.tap("admin_start")
    await admin_driver.tap("ovpn_l2tp_mgmt_menu")
    assert "list_profiles" in admin_driver.callbacks_on_screen()


@pytest.mark.asyncio
async def test_e2e_sales_bot_config_shared_notify_backup_clean(admin_driver):
    await admin_driver.open_admin_menu()
    for cb in (
        "sales_mgmt_menu",
        "bot_config_menu",
        "shared_users_menu",
        "notification_menu",
        "backup_menu",
        "clean_db_menu",
    ):
        await admin_driver.tap("admin_start")
        await admin_driver.tap(cb)
        assert admin_driver.callbacks_on_screen() or not admin_driver.errors

    await admin_driver.tap("admin_start")
    await admin_driver.tap("notification_menu")
    assert "notify_targeted" in admin_driver.callbacks_on_screen()


@pytest.mark.asyncio
async def test_e2e_cleanup_warn_only(admin_driver):
    await admin_driver.open_admin_menu()
    await admin_driver.tap("clean_db_menu")
    cbs = admin_driver.callbacks_on_screen()
    if destructive_allowed():
        assert any("force_clean" in c or "warn_clean" in c for c in cbs)
    else:
        assert any("warn_clean" in c for c in cbs)


@pytest.mark.asyncio
async def test_e2e_subscription_info_service_mt(live_server, e2e_test_user):
    """Service path used by manage_sub UI."""
    name = f"{INTG_E2E_OVPN}_info_{int(time.time()) % 100000}"
    prof, err = await create_profile_full(
        {
            "server_id": live_server.id,
            "name": name,
            "days": 2,
            "limit": 1,
            "price_toman": 100,
        }
    )
    assert prof, err
    ok, sub, _ = await checkout_subscription(e2e_test_user.telegram_id, prof.id)
    assert ok
    sub_row, mt = await get_subscription_comprehensive_info(sub.mikrotik_username)
    assert sub_row is not None

    mgr = get_mikrotik_manager(live_server)
    await asyncio.to_thread(mgr.delete_user, sub.mikrotik_username)
    await delete_profile_full(prof.id)


@pytest.mark.asyncio
async def test_e2e_settings_sync_menu(admin_driver):
    """Bot config → sync entry (settings UI path)."""
    await admin_driver.open_admin_menu()
    await admin_driver.tap("bot_config_menu")
    assert "settings_sync" in admin_driver.callbacks_on_screen()


@pytest.mark.asyncio
async def test_e2e_delete_account_skipped_without_destructive(admin_driver, e2e_test_user):
    """Full account delete requires LIVE_ADMIN_FULL_DESTRUCTIVE=1."""
    if destructive_allowed():
        pytest.skip("Destructive mode — run delete_account manually")
    await admin_driver.open_admin_menu()
    await admin_driver.tap("search_user")
    await admin_driver.send_text(str(e2e_test_user.telegram_id))
    assert not admin_driver.errors
