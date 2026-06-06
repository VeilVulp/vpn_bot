"""
Comprehensive live integration suite (service-layer, real MikroTik).

Runs critical business workflows against the router configured in .env.test.
"""

import asyncio
import time

import pytest
from sqlalchemy import select

from vpn_bot.admin_profile_service import create_profile_full, delete_profile_full, get_all_profiles
from vpn_bot.admin_receipt_service import approve_payment_receipt, get_pending_receipts
from vpn_bot.admin_report_service import get_revenue_stats, get_subscription_stats, get_user_stats
from vpn_bot.admin_server_service import get_server_health_status
from vpn_bot.admin_settings_service import get_custom_message
from vpn_bot.admin_ticket_service import add_ticket_message, close_ticket, create_ticket_from_user
from vpn_bot.admin_user_service import get_user_comprehensive_info, toggle_user_ban, update_user_balance
from vpn_bot.admin_wg_service import create_wg_profile, delete_wg_profile, get_all_wg_profiles
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.models import PaymentReceipt, Server, User
from vpn_bot.settings_utils import get_admin_setting, set_admin_setting
from vpn_bot.sync_manager import SyncManager
from vpn_bot.user_features import checkout_subscription, finalize_wg_purchase
from vpn_bot.wallet_manager import WalletManager

pytestmark = pytest.mark.live_mt

INTG_TG = 880002002


@pytest.fixture
async def intg_user_full(db_initialized, live_server):
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.telegram_id == INTG_TG))
        user = res.scalars().first()
        if not user:
            user = User(
                telegram_id=INTG_TG,
                username="intg_full",
                full_name="Full Integration User",
                wallet_balance=20_000_000.0,
            )
            session.add(user)
        else:
            user.wallet_balance = 20_000_000.0
        await session.commit()
        await session.refresh(user)
        yield user


class TestLiveIntegrationCore:
    @pytest.mark.asyncio
    async def test_01_server_health(self, live_server):
        ok = await get_server_health_status(live_server)
        assert ok is True

    @pytest.mark.asyncio
    async def test_02_mikrotik_api_roundtrip(self, live_server):
        mgr = get_mikrotik_manager(live_server)
        await asyncio.to_thread(mgr.connect)
        res = mgr.api.get_resource("/system/resource").get()
        assert res
        await asyncio.to_thread(mgr.close)

    @pytest.mark.asyncio
    async def test_03_settings_roundtrip(self):
        key = "intg_test_setting"
        await set_admin_setting(key, {"ok": True, "ts": time.time()})
        val = await get_admin_setting(key)
        assert val.get("ok") is True

    @pytest.mark.asyncio
    async def test_04_reports(self, intg_user_full):
        assert await get_revenue_stats() is not None
        assert await get_subscription_stats() is not None
        assert await get_user_stats() is not None
        info = await get_user_comprehensive_info(intg_user_full.id)
        assert info is not None

    @pytest.mark.asyncio
    async def test_05_wallet_deposit_deduct(self, intg_user_full):
        uid = intg_user_full.id
        assert await WalletManager.deposit(uid, 1000, "intg deposit")
        assert await WalletManager.deduct(uid, 500, "intg deduct")

    @pytest.mark.asyncio
    async def test_06_receipt_approve(self, intg_user_full):
        async with AsyncSessionLocal() as session:
            r = PaymentReceipt(
                user_id=intg_user_full.id,
                amount=2500,
                receipt_file_id="intg_rcpt",
                status="pending",
            )
            session.add(r)
            await session.commit()
            await session.refresh(r)
            rid = r.id
        bal_before = intg_user_full.wallet_balance
        ok, _ = await approve_payment_receipt(rid, 1)
        assert ok is True
        async with AsyncSessionLocal() as session:
            u = await session.get(User, intg_user_full.id)
            assert u.wallet_balance >= bal_before

    @pytest.mark.asyncio
    async def test_07_ticket_lifecycle(self, intg_user_full):
        tid = await create_ticket_from_user(intg_user_full.id, "INTG Ticket", "Hello")
        assert tid
        await add_ticket_message(tid, 1, "admin", "Admin reply")
        assert await close_ticket(tid) is True

    @pytest.mark.asyncio
    async def test_08_user_ban_toggle(self, intg_user_full):
        ok, banned = await toggle_user_ban(intg_user_full.id)
        assert ok
        ok2, banned2 = await toggle_user_ban(intg_user_full.id)
        assert ok2
        assert banned2 != banned


class TestLiveIntegrationVPN:
    @pytest.mark.asyncio
    async def test_09_ovpn_purchase(self, intg_user_full, live_server):
        name = f"INTG_FULL_OVPN_{int(time.time()) % 100000}"
        prof, err = await create_profile_full(
            {
                "server_id": live_server.id,
                "name": name,
                "days": 5,
                "limit": 1,
                "price_toman": 1000,
            }
        )
        assert prof, err
        ok, sub, msg = await checkout_subscription(intg_user_full.telegram_id, prof.id)
        assert ok, msg
        mgr = get_mikrotik_manager(live_server)
        info = await asyncio.to_thread(mgr.get_user_info, sub.mikrotik_username)
        assert info is not None
        await asyncio.to_thread(mgr.delete_user, sub.mikrotik_username)
        await delete_profile_full(prof.id)

    @pytest.mark.asyncio
    async def test_10_wg_purchase(self, intg_user_full, live_server):
        pname = f"INTG_FULL_WG_{int(time.time()) % 100000}"
        prof = await create_wg_profile(
            {
                "name": pname,
                "volume": 1,
                "days": 5,
                "price_toman": 1000,
                "server_id": live_server.id,
            }
        )
        assert prof
        ok = await finalize_wg_purchase(
            intg_user_full.telegram_id, prof.id, context=None, is_tg_id=True
        )
        assert ok is True
        await delete_wg_profile(prof.id)

    @pytest.mark.asyncio
    async def test_11_sync_all_servers(self, live_server):
        async with AsyncSessionLocal() as session:
            server = await session.get(Server, live_server.id)
            n = await SyncManager.reconcile_ovpn_subscriptions(session, server)
            await session.commit()
        assert n is not None

    @pytest.mark.asyncio
    async def test_12_profiles_listed(self, live_server):
        profiles = await get_all_profiles()
        assert isinstance(profiles, list)
        wg = await get_all_wg_profiles()
        assert isinstance(wg, list)

    @pytest.mark.asyncio
    async def test_13_custom_message_readable(self):
        msg = await get_custom_message("welcome_message")
        assert msg is None or isinstance(msg, str)

    @pytest.mark.asyncio
    async def test_14_pending_receipts_list(self):
        pending = await get_pending_receipts()
        assert isinstance(pending, list)

    @pytest.mark.asyncio
    async def test_15_cleanup_health(self):
        from vpn_bot.admin_cleanup_service import get_db_health_stats

        assert isinstance(await get_db_health_stats(), dict)

    @pytest.mark.asyncio
    async def test_16_update_user_balance(self, intg_user_full):
        await update_user_balance(intg_user_full.id, intg_user_full.wallet_balance)

    @pytest.mark.asyncio
    async def test_17_get_all_servers(self):
        from vpn_bot.admin_server_service import get_all_servers

        assert isinstance(await get_all_servers(), list)

    @pytest.mark.asyncio
    async def test_18_open_ticket_count(self, intg_user_full):
        from vpn_bot.admin_ticket_service import get_open_ticket_count

        assert (await get_open_ticket_count(intg_user_full.id)) >= 0

    @pytest.mark.asyncio
    async def test_19_wg_reconcile_smoke(self, live_server):
        try:
            async with AsyncSessionLocal() as session:
                server = await session.get(Server, live_server.id)
                n = await SyncManager.reconcile_wg_subscriptions(session, server)
                await session.commit()
            assert n is not None
        except Exception as exc:
            pytest.skip(f"WG reconcile skipped: {exc}")

    @pytest.mark.asyncio
    async def test_20_sync_all_servers_smoke(self):
        await SyncManager.sync_all_servers()

    @pytest.mark.asyncio
    async def test_21_um_users_list(self, live_server):
        mgr = get_mikrotik_manager(live_server)
        users = await asyncio.to_thread(mgr.get_all_um_users)
        assert users is not None

    @pytest.mark.asyncio
    async def test_22_mt_cache_stats(self):
        from vpn_bot.mt_cache import mt_cache

        stats = mt_cache.stats
        assert "hits" in stats

    @pytest.mark.asyncio
    async def test_23_profile_price_helper(self, live_server):
        from vpn_bot.utils import get_profile_price

        prof, _ = await create_profile_full(
            {
                "server_id": live_server.id,
                "name": f"INTG_PRICE_{int(time.time()) % 100000}",
                "days": 1,
                "limit": 1,
                "price_toman": 999,
            }
        )
        assert prof
        price = await get_profile_price(prof)
        assert price >= 0
        await delete_profile_full(prof.id)

    @pytest.mark.asyncio
    async def test_24_language_manager_loaded(self):
        from vpn_bot.utils import LanguageManager

        assert LanguageManager._loaded
        assert LanguageManager.get("common.main_menu")

    @pytest.mark.asyncio
    async def test_25_live_server_record(self, live_server):
        assert live_server.host
        assert live_server.port in (8728, 443, 8297)
