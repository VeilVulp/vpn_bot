"""Fixtures shared by live MikroTik integration tests."""

import time
import uuid

import pytest
from sqlalchemy import delete, select

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Profile, User, WireGuardInterface, WireGuardProfile, WireGuardSubscription
from vpn_bot.utils import LanguageManager

pytestmark = pytest.mark.live_mt

INTG_TG_ID = 880001001
INTG_PROFILE_PREFIX = "INTG_OVPN_"
INTG_WG_PROFILE_PREFIX = "INTG_WG_PLAN_"


@pytest.fixture
def no_wg_preempt(monkeypatch):
    """Disable background preemptive WG interface creation during tests."""

    async def _noop():
        return []

    monkeypatch.setattr(
        "vpn_bot.admin_wg_service.check_and_preemptively_create_interfaces",
        _noop,
    )
    monkeypatch.setattr(
        "vpn_bot.user_features.check_and_preemptively_create_interfaces",
        _noop,
        raising=False,
    )


@pytest.fixture(autouse=True)
def _load_locales():
    if not LanguageManager._loaded:
        LanguageManager.load_locales()


@pytest.fixture(autouse=True)
async def _cleanup_orphan_um_users(live_server):
    """Remove leftover UM users from failed test runs (u*, INTG_*)."""
    import asyncio

    from vpn_bot.mikrotik_manager import get_mikrotik_manager
    from tests.conftest import _is_test_um_username

    mgr = get_mikrotik_manager(live_server)
    try:
        await asyncio.to_thread(mgr.connect)
        user_api = mgr._get_resource("/user-manager/user")
        users = await asyncio.to_thread(user_api.get)
        for u in users or []:
            name = u.get("name", "")
            if _is_test_um_username(name):
                await asyncio.to_thread(mgr.delete_user, name)
    except Exception:
        pass
    yield
    try:
        user_api = mgr._get_resource("/user-manager/user")
        users = await asyncio.to_thread(user_api.get)
        for u in users or []:
            name = u.get("name", "")
            if _is_test_um_username(name):
                await asyncio.to_thread(mgr.delete_user, name)
        await asyncio.to_thread(mgr.close)
    except Exception:
        pass


@pytest.fixture
async def intg_user(db_initialized, live_server):
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.telegram_id == INTG_TG_ID))
        user = res.scalars().first()
        if not user:
            user = User(
                telegram_id=INTG_TG_ID,
                username="intg_pytest",
                full_name="Integration Test User",
                wallet_balance=10_000_000.0,
            )
            session.add(user)
        else:
            user.wallet_balance = 10_000_000.0
        await session.commit()
        await session.refresh(user)
        yield user


@pytest.fixture
async def intg_ovpn_profile(live_server):
    """Create UM profile on router + DB row; delete after test."""
    from vpn_bot.admin_profile_service import create_profile_full, delete_profile_full

    name = f"{INTG_PROFILE_PREFIX}{int(time.time()) % 100000}"
    prof, err = await create_profile_full(
        {
            "server_id": live_server.id,
            "name": name,
            "days": 7,
            "limit": 1,
            "price_usd": 1.0,
            "price_toman": 1000.0,
        }
    )
    if not prof:
        pytest.fail(f"Could not create test OVPN profile: {err}")
    yield prof
    await delete_profile_full(prof.id)


@pytest.fixture
async def intg_wg_profile(live_server):
    """WireGuard plan linked to live test server."""
    from vpn_bot.admin_wg_service import create_wg_profile, delete_wg_profile

    name = f"{INTG_WG_PROFILE_PREFIX}{uuid.uuid4().hex[:8]}"
    prof = await create_wg_profile(
        {
            "name": name,
            "volume": 1,
            "days": 7,
            "price_toman": 1000,
            "price_usd": 0.0,
            "server_id": live_server.id,
        }
    )
    if not prof:
        pytest.fail("Could not create WG test profile")
    yield prof
    try:
        await delete_wg_profile(prof.id)
    except Exception:
        pass


@pytest.fixture
async def intg_wg_profile_unlimited(live_server):
    """WG plan with unlimited traffic (volume_gb=None); time expiry only."""
    from vpn_bot.admin_wg_service import create_wg_profile, delete_wg_profile

    name = f"{INTG_WG_PROFILE_PREFIX}UNL_{uuid.uuid4().hex[:6]}"
    prof = await create_wg_profile(
        {
            "name": name,
            "volume": None,
            "days": 7,
            "price_toman": 1000,
            "price_usd": 0.0,
            "server_id": live_server.id,
        }
    )
    if not prof:
        pytest.fail("Could not create unlimited WG test profile")
    yield prof
    try:
        await delete_wg_profile(prof.id)
    except Exception:
        pass


@pytest.fixture
async def intg_wg_profile_long_days(live_server):
    """WG plan with long validity (traffic-focused scenarios)."""
    from vpn_bot.admin_wg_service import create_wg_profile, delete_wg_profile

    name = f"{INTG_WG_PROFILE_PREFIX}LONG_{uuid.uuid4().hex[:6]}"
    prof = await create_wg_profile(
        {
            "name": name,
            "volume": 1,
            "days": 365,
            "price_toman": 1000,
            "price_usd": 0.0,
            "server_id": live_server.id,
        }
    )
    if not prof:
        pytest.fail("Could not create long-days WG test profile")
    yield prof
    try:
        await delete_wg_profile(prof.id)
    except Exception:
        pass


@pytest.fixture
async def intg_wg_interface_tiny(live_server):
    """Small-capacity WG interface (max_users=3) for live capacity tests."""
    import asyncio

    from vpn_bot.mikrotik_manager import get_mikrotik_manager

    mgr = get_mikrotik_manager(live_server)
    params = await asyncio.to_thread(mgr.find_available_wg_interface_params)
    if not params:
        pytest.skip("No free WG interface params on router")

    keys = await asyncio.to_thread(
        mgr.create_wg_interface, params["name"], params["listen_port"], params["address"]
    )
    if not keys:
        pytest.skip("Could not create tiny WG interface on router")

    async with AsyncSessionLocal() as session:
        iface = WireGuardInterface(
            server_id=live_server.id,
            name=params["name"],
            public_key=keys["public_key"],
            private_key=keys.get("private_key", "test"),
            listen_port=keys.get("listen_port", params["listen_port"]),
            address=params["address"],
            max_users=3,
            current_users=0,
            is_active=True,
        )
        session.add(iface)
        await session.commit()
        await session.refresh(iface)
        yield iface

        await session.execute(
            delete(WireGuardSubscription).where(WireGuardSubscription.interface_id == iface.id)
        )
        await session.delete(iface)
        await session.commit()

    try:
        await asyncio.to_thread(mgr.cleanup_wg_interface_automation, iface.name)
        await asyncio.to_thread(mgr.delete_wg_interface, iface.name)
    except Exception:
        pass


@pytest.fixture
async def intg_wg_interface(live_server):
    """Ensure at least one WG interface exists on the test server."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WireGuardInterface).where(
                WireGuardInterface.server_id == live_server.id,
                WireGuardInterface.is_active == True,
            )
        )
        iface = res.scalars().first()
        if iface:
            yield iface
            return

    from vpn_bot.mikrotik_manager import get_mikrotik_manager

    mgr = get_mikrotik_manager(live_server)
    params = await __import__("asyncio").to_thread(mgr.find_available_wg_interface_params)
    if not params:
        pytest.skip("No free WG interface params on router")

    keys = await __import__("asyncio").to_thread(
        mgr.create_wg_interface, params["name"], params["listen_port"], params["address"]
    )
    if not keys:
        pytest.skip("Could not create WG interface on router")

    async with AsyncSessionLocal() as session:
        iface = WireGuardInterface(
            server_id=live_server.id,
            name=params["name"],
            public_key=keys["public_key"],
            private_key=keys.get("private_key", "test"),
            listen_port=keys.get("listen_port", params["listen_port"]),
            address=params["address"],
            max_users=50,
            current_users=0,
            is_active=True,
        )
        session.add(iface)
        await session.commit()
        await session.refresh(iface)
        yield iface

        await session.execute(
            delete(WireGuardSubscription).where(WireGuardSubscription.interface_id == iface.id)
        )
        await session.delete(iface)
        await session.commit()

    try:
        await __import__("asyncio").to_thread(mgr.cleanup_wg_interface_automation, iface.name)
        await __import__("asyncio").to_thread(mgr.delete_wg_interface, iface.name)
    except Exception:
        pass
