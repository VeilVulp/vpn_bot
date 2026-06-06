"""Fixtures for destructive pg_dump/pg_restore roundtrip tests (isolated DB)."""

from __future__ import annotations

import asyncio
import os

import pytest

_backup_roundtrip_lock = asyncio.Lock()


@pytest.fixture(scope="module")
def require_destructive_ack():
    if os.getenv("ALLOW_DESTRUCTIVE_BACKUP_TEST") != "1":
        pytest.skip("Set ALLOW_DESTRUCTIVE_BACKUP_TEST=1 for backup roundtrip tests")


@pytest.fixture(scope="module")
def require_pg_cli(require_destructive_ack):
    from vpn_bot.backup_manager import _find_pg_tool

    for tool in ("pg_dump", "pg_restore"):
        path = _find_pg_tool(tool)
        if path == tool or not os.path.isfile(path):
            pytest.skip(f"PostgreSQL CLI '{tool}' not found (install libpq/postgresql client)")


@pytest.fixture(scope="module")
def backup_test_db_url(require_pg_cli):
    url = os.getenv("BACKUP_TEST_DATABASE_URL", "").strip()
    if not url:
        pytest.skip("BACKUP_TEST_DATABASE_URL not set (use isolated vpnbot_backup_test)")
    return url


@pytest.fixture(scope="module", autouse=True)
def _verify_backup_database_exists(backup_test_db_url):
    """Skip early when the dedicated backup database was not created yet."""
    import asyncio

    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    async def _ping():
        engine = create_async_engine(backup_test_db_url, pool_pre_ping=True)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
        finally:
            await engine.dispose()

    try:
        asyncio.run(_ping())
    except Exception as exc:
        if "does not exist" in str(exc).lower():
            pytest.skip(
                "BACKUP_TEST_DATABASE_URL database missing — create with: "
                "createdb vpnbot_backup_test (as PostgreSQL superuser) "
                "or set BACKUP_TEST_DATABASE_URL to an existing empty test DB"
            )
        raise


@pytest.fixture(scope="module", autouse=True)
def _patch_backup_database_url(backup_test_db_url):
    from vpn_bot import config

    old_url = config.config.DATABASE_URL
    old_env = os.environ.get("DATABASE_URL")
    config.config.DATABASE_URL = backup_test_db_url
    os.environ["DATABASE_URL"] = backup_test_db_url
    yield
    config.config.DATABASE_URL = old_url
    if old_env is not None:
        os.environ["DATABASE_URL"] = old_env
    else:
        os.environ.pop("DATABASE_URL", None)


@pytest.fixture
async def backup_roundtrip_lock():
    async with _backup_roundtrip_lock:
        yield


@pytest.fixture
async def refresh_db_engine():
    """Dispose SQLAlchemy pool after pg_restore."""
    from vpn_bot.database import engine, init_db

    async def _refresh():
        await engine.dispose()
        await init_db()

    yield _refresh
    await engine.dispose()
    await init_db()
