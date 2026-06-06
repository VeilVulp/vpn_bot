"""Fixtures for admin panel Telegram E2E tests (real MikroTik)."""

from __future__ import annotations

import asyncio
import os

import pytest

from vpn_bot.config import config
from vpn_bot.utils import LanguageManager

pytestmark = [pytest.mark.live_mt, pytest.mark.live_admin_e2e]


@pytest.fixture(scope="session", autouse=True)
def _require_production_ack():
    from tests.helpers.admin_e2e_harness import production_ack_required

    production_ack_required()


@pytest.fixture(scope="session")
def admin_telegram_id():
    if not config.ADMIN_IDS:
        pytest.skip("ADMIN_IDS must be set in .env for admin E2E")
    return int(config.ADMIN_IDS[0])


@pytest.fixture
async def mt_sem():
    n = int(os.getenv("ADMIN_E2E_MT_SEM", "3"))
    return asyncio.Semaphore(n)


@pytest.fixture
async def admin_app():
    from tests.helpers.admin_e2e_harness import build_admin_application

    app, bot, real_bot = await build_admin_application(include_user_handlers=True)
    yield app, bot
    app.bot = real_bot
    await app.shutdown()


@pytest.fixture
async def admin_driver(admin_app, admin_telegram_id):
    from tests.helpers.admin_e2e_harness import AdminE2EDriver

    app, bot = admin_app
    driver = AdminE2EDriver(app, bot, admin_telegram_id)
    yield driver


@pytest.fixture
async def e2e_test_user(db_initialized, live_server):
    """User with INTG_E2E prefix data for admin user-management flows."""
    import time

    from sqlalchemy import select

    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.models import User

    tg = int(os.getenv("ADMIN_E2E_TEST_TG", "880009001"))
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.telegram_id == tg))
        user = res.scalars().first()
        if not user:
            user = User(
                telegram_id=tg,
                username=f"intg_e2e_{int(time.time()) % 100000}",
                full_name="INTG_E2E Admin Test User",
                wallet_balance=5_000_000.0,
            )
            session.add(user)
        else:
            user.wallet_balance = max(user.wallet_balance or 0, 5_000_000.0)
        await session.commit()
        await session.refresh(user)
        yield user


@pytest.fixture(autouse=True)
def _load_locales():
    if not LanguageManager._loaded:
        LanguageManager.load_locales()


@pytest.fixture
async def e2e_cleanup(live_server):
    """Remove INTG_E2E_* UM users and WG peers after each admin E2E test."""
    import asyncio

    from sqlalchemy import delete, select

    from tests.conftest import _is_test_um_username
    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.mikrotik_manager import get_mikrotik_manager
    from tests.live.helpers.wg_workflow_live import load_sub, teardown_wg_sub
    from vpn_bot.models import PaymentReceipt, Subscription, User, WireGuardSubscription

    yield

    mgr = get_mikrotik_manager(live_server)
    try:
        await asyncio.to_thread(mgr.connect)
        user_api = mgr._get_resource("/user-manager/user")
        users = await asyncio.to_thread(user_api.get)
        for u in users or []:
            name = u.get("name", "")
            if name.startswith("INTG_E2E") or (
                _is_test_um_username(name) and name.startswith("INTG_")
            ):
                await asyncio.to_thread(mgr.delete_user, name)
    except Exception:
        pass

    tg = int(os.getenv("ADMIN_E2E_TEST_TG", "880009001"))
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(User).where(User.telegram_id == tg))
            user = res.scalars().first()
            if user:
                await session.execute(
                    delete(PaymentReceipt).where(
                        PaymentReceipt.user_id == user.id,
                        PaymentReceipt.receipt_file_id.like("intg_e2e%"),
                    )
                )
                wg_res = await session.execute(
                    select(WireGuardSubscription).where(
                        WireGuardSubscription.user_id == user.id
                    )
                )
                for wg_sub in wg_res.scalars().all():
                    full = await load_sub(wg_sub.id)
                    if full:
                        try:
                            await teardown_wg_sub(full, live_server)
                        except Exception:
                            pass
                await session.execute(
                    delete(WireGuardSubscription).where(
                        WireGuardSubscription.user_id == user.id
                    )
                )
                await session.execute(
                    delete(Subscription).where(
                        Subscription.user_id == user.id,
                        Subscription.mikrotik_username.like("INTG_E2E%"),
                    )
                )
                await session.commit()
    except Exception:
        pass
