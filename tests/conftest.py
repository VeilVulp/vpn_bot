"""
Shared pytest fixtures for VPN Bot.

Live MikroTik tests load credentials from .env.test (see .env.test.example).
"""

import asyncio
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]

# Load production .env first (ENCRYPTION_KEY), then .env.test for test overrides
load_dotenv(ROOT / ".env")
_env_test = ROOT / ".env.test"
if _env_test.exists():
    load_dotenv(_env_test, override=True)
    # Do not let an empty ENCRYPTION_KEY in .env.test wipe the key from .env
    if not os.getenv("ENCRYPTION_KEY", "").strip():
        load_dotenv(ROOT / ".env", override=True)

if not os.getenv("ENCRYPTION_KEY", "").strip():
    raise RuntimeError(
        "ENCRYPTION_KEY must be set in .env (and not cleared by .env.test). "
        "Use the same key for pytest and bot when sharing DATABASE_URL."
    )


def _mt_config() -> dict | None:
    host = os.getenv("MIKROTIK_TEST_HOST") or os.getenv("MIKROTIK_HOST")
    user = os.getenv("MIKROTIK_TEST_USERNAME") or os.getenv("MIKROTIK_USERNAME")
    password = os.getenv("MIKROTIK_TEST_PASSWORD") or os.getenv("MIKROTIK_PASSWORD")
    if not host or not user or not password:
        return None
    port = int(os.getenv("MIKROTIK_TEST_PORT", os.getenv("MIKROTIK_PORT", "8728")))
    port_ssl = int(os.getenv("MIKROTIK_TEST_PORT_SSL", "443"))
    return {
        "host": host,
        "username": user,
        "password": password,
        "port": port,
        "port_ssl": port_ssl,
    }


@pytest.fixture(scope="session")
def mt_config():
    cfg = _mt_config()
    if not cfg:
        pytest.skip("MIKROTIK_TEST_* (or MIKROTIK_*) not set — copy .env.test.example to .env.test")
    return cfg


@pytest.fixture(autouse=True)
async def _fresh_db_engine_per_test():
    """Reset module-global async engine so asyncpg binds to the current event loop."""
    from vpn_bot.database import engine, init_db

    await engine.dispose()
    await init_db()
    yield
    await engine.dispose()


@pytest.fixture
async def db_initialized():
    """Tables exist after autouse engine init."""
    yield


@pytest.fixture
async def live_server(db_initialized, mt_config):
    """Upsert a Server row pointing at the live test router."""
    from sqlalchemy import select

    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.models import Server

    name = "[TEST] Live MikroTik"
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Server).where(Server.name == name))
        server = res.scalars().first()
        if not server:
            server = Server(
                name=name,
                host=mt_config["host"],
                port=mt_config["port"],
                username=mt_config["username"],
                password=mt_config["password"],
                is_active=True,
                location="pytest-live",
            )
            session.add(server)
        else:
            server.host = mt_config["host"]
            server.port = mt_config["port"]
            server.username = mt_config["username"]
            server.password = mt_config["password"]
            server.is_active = True
        await session.commit()
        await session.refresh(server)
        yield server


@pytest.fixture
def mikrotik_manager(mt_config):
    from vpn_bot.mikrotik_manager import MikroTikManager

    mgr = MikroTikManager(
        host=mt_config["host"],
        username=mt_config["username"],
        password=mt_config["password"],
        port=mt_config["port"],
        use_pool=False,
    )
    yield mgr
    try:
        mgr.close()
    except Exception:
        pass


@pytest.fixture
def mikrotik_manager_ssl(mt_config):
    from vpn_bot.mikrotik_manager import MikroTikManager

    mgr = MikroTikManager(
        host=mt_config["host"],
        username=mt_config["username"],
        password=mt_config["password"],
        port=mt_config["port_ssl"],
        use_pool=False,
    )
    yield mgr
    try:
        mgr.close()
    except Exception:
        pass


INTG_USERNAME_PREFIXES = ("u", "INTG_", "intg_", "INTG_E2E")


def _is_test_um_username(name: str) -> bool:
    if not name:
        return False
    if name.startswith("INTG_") or name.startswith("intg_") or name.startswith("INTG_E2E"):
        return True
    # Bot-generated: u + base36 subscription id from integration tests
    if name.startswith("u") and len(name) <= 12 and name[1:].isalnum():
        return True
    return False


@pytest.fixture(scope="session", autouse=True)
def cleanup_live_mt_users(request, mt_config):
    """Remove INTG_* / test UM users after the live_mt session."""

    def _fin():
        marker = request.config.getoption("-m", default="")
        # Only run heavy cleanup when live tests were collected
        if "live_mt" not in str(marker) and not any(
            item.get_closest_marker("live_mt") for item in request.session.items
        ):
            return
        try:
            from vpn_bot.mikrotik_manager import MikroTikManager

            mgr = MikroTikManager(
                host=mt_config["host"],
                username=mt_config["username"],
                password=mt_config["password"],
                port=mt_config["port"],
                use_pool=False,
            )
            mgr.connect()
            user_api = mgr._get_resource("/user-manager/user")
            users = user_api.get()
            for u in users or []:
                name = u.get("name", "")
                if _is_test_um_username(name):
                    try:
                        mgr.delete_user(name)
                    except Exception:
                        pass
            mgr.close()
        except Exception:
            pass

    request.addfinalizer(_fin)
