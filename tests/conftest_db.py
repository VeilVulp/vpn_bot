"""Shared fixtures for database-focused tests (mock MikroTik)."""

import random
import uuid

import pytest
from sqlalchemy import select

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Profile, Server, User, WireGuardInterface, WireGuardProfile
from tests.helpers.mock_mikrotik import MockMikrotikManager, patch_get_mikrotik_manager
from vpn_bot.utils import LanguageManager

def _next_tg_id() -> int:
    return random.randint(700_000_000_000, 799_999_999_999)


@pytest.fixture(autouse=True)
def _db_load_locales():
    if not LanguageManager._loaded:
        LanguageManager.load_locales()


@pytest.fixture(autouse=True)
def _reset_mock_mt():
    MockMikrotikManager.reset_all()
    yield
    MockMikrotikManager.reset_all()


@pytest.fixture
def mock_mikrotik(monkeypatch):
    patch_get_mikrotik_manager(monkeypatch)
    yield


@pytest.fixture
async def mock_server(db_initialized, mock_mikrotik):
    """DB server row (no real router)."""
    name = f"[DBTEST] Mock {uuid.uuid4().hex[:8]}"
    async with AsyncSessionLocal() as session:
        server = Server(
            name=name,
            host="127.0.0.1",
            port=8728,
            username="mock",
            password="mock",
            is_active=True,
            location="pytest-db",
        )
        session.add(server)
        await session.commit()
        await session.refresh(server)
        yield server


@pytest.fixture
async def db_ovpn_profile(mock_server):
    async with AsyncSessionLocal() as session:
        prof = Profile(
            name=f"DBTEST_OVPN_{uuid.uuid4().hex[:6]}",
            price_usd=1.0,
            price_toman=10_000.0,
            validity_days=7,
            data_limit_gb=1,
            server_id=mock_server.id,
            is_active=True,
        )
        session.add(prof)
        await session.commit()
        await session.refresh(prof)
        yield prof


@pytest.fixture
async def db_wg_profile(mock_server):
    async with AsyncSessionLocal() as session:
        prof = WireGuardProfile(
            name=f"DBTEST_WG_{uuid.uuid4().hex[:6]}",
            volume_gb=1,
            duration_days=7,
            price_toman=10_000,
            price_usd=0.0,
            server_id=mock_server.id,
            is_active=True,
        )
        session.add(prof)
        await session.commit()
        await session.refresh(prof)
        yield prof


@pytest.fixture
async def db_wg_interface(mock_server):
    async with AsyncSessionLocal() as session:
        iface = WireGuardInterface(
            server_id=mock_server.id,
            name=f"wg_db_{uuid.uuid4().hex[:6]}",
            public_key="mock_pub_key",
            private_key="mock_priv_key",
            listen_port=51830,
            address="10.88.0.1/24",
            max_users=50,
            current_users=0,
            is_active=True,
            endpoint_host="127.0.0.1",
        )
        session.add(iface)
        await session.commit()
        await session.refresh(iface)
        yield iface


@pytest.fixture
async def db_wg_interface_tiny(mock_server):
    """Small-capacity WG interface for concurrent capacity tests."""
    from sqlalchemy import delete

    from vpn_bot.models import WireGuardSubscription

    async with AsyncSessionLocal() as session:
        iface_res = await session.execute(
            select(WireGuardInterface.id).where(WireGuardInterface.server_id == mock_server.id)
        )
        iface_ids = list(iface_res.scalars().all())
        if iface_ids:
            await session.execute(
                delete(WireGuardSubscription).where(
                    WireGuardSubscription.interface_id.in_(iface_ids)
                )
            )
            await session.execute(
                delete(WireGuardInterface).where(WireGuardInterface.server_id == mock_server.id)
            )
            await session.commit()

        iface = WireGuardInterface(
            server_id=mock_server.id,
            name=f"wg_tiny_{uuid.uuid4().hex[:6]}",
            public_key="mock_pub_tiny",
            private_key="mock_priv_tiny",
            listen_port=51831,
            address="10.89.0.1/24",
            max_users=3,
            current_users=0,
            is_active=True,
            endpoint_host="127.0.0.1",
        )
        session.add(iface)
        await session.commit()
        await session.refresh(iface)
        yield iface


@pytest.fixture
async def db_user_factory():
    """Create users with unique telegram_id and optional balance."""

    async def _make(balance: float = 1_000_000.0, banned: bool = False) -> User:
        tg = _next_tg_id()
        async with AsyncSessionLocal() as session:
            user = User(
                telegram_id=tg,
                username=f"dbtest_{tg}",
                wallet_balance=balance,
                is_banned=banned,
            )
            session.add(user)
            await session.commit()
            await session.refresh(user)
            return user

    yield _make


@pytest.fixture
async def db_user(db_user_factory):
    return await db_user_factory(balance=1_000_000.0)


@pytest.fixture
async def db_user_low_balance(db_user_factory, db_ovpn_profile):
    from vpn_bot.utils import get_profile_price

    price = await get_profile_price(db_ovpn_profile)
    return await db_user_factory(balance=price - 50)


class FakeBot:
    async def send_message(self, **kwargs):
        return True
