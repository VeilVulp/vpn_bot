"""Full pg_dump / pg_restore roundtrip on isolated BACKUP_TEST_DATABASE_URL."""

from __future__ import annotations

import asyncio
import os

import pytest

from tests.db.helpers.backup_roundtrip import (
    assert_snapshots_equal,
    capture_backup_snapshot,
    junk_user_exists,
    mutate_database_after_backup,
    seed_full_bot_dataset,
)
from vpn_bot.backup_manager import BackupManager, _find_pg_tool
from vpn_bot.database import AsyncSessionLocal

pytest_plugins = ["tests.db.backup_conftest"]

pytestmark = [pytest.mark.db, pytest.mark.db_backup]


async def _seed_ids():
    async with AsyncSessionLocal() as session:
        return await seed_full_bot_dataset(session)


async def _reinit_engine():
    from vpn_bot.database import engine, init_db

    await engine.dispose()
    await init_db()


@pytest.mark.asyncio
async def test_pg_dump_creates_nonempty_custom_format_file(
    backup_roundtrip_lock,
    refresh_db_engine,
):
    """B1: backup file exists and is valid custom pg_dump format."""
    await _seed_ids()
    mgr = BackupManager()
    path = None
    try:
        path = await mgr.create_backup_file()
        assert os.path.isfile(path)
        assert os.path.getsize(path) > 0

        env = os.environ.copy()
        from vpn_bot.backup_manager import _parse_pg_url
        from vpn_bot.config import config

        params = _parse_pg_url(config.DATABASE_URL)
        env["PGPASSWORD"] = params["password"]
        proc = await asyncio.create_subprocess_exec(
            _find_pg_tool("pg_restore"),
            "--list",
            path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        assert proc.returncode == 0, stderr.decode()
        assert b"TABLE" in stdout or b"TABLE DATA" in stdout
    finally:
        if path and os.path.exists(path):
            os.remove(path)


@pytest.mark.asyncio
async def test_backup_restore_roundtrip_preserves_all_seeded_data(
    backup_roundtrip_lock,
    refresh_db_engine,
):
    """B2: seed → dump → mutate → restore → snapshot equals pre-mutate."""
    ids = await _seed_ids()
    before = await capture_backup_snapshot(ids)

    mgr = BackupManager()
    path = None
    try:
        path = await mgr.create_backup_file()
        await mutate_database_after_backup(ids)
        assert await junk_user_exists()

        ok = await BackupManager.restore_database(path)
        assert ok is True
        await _reinit_engine()

        after = await capture_backup_snapshot(ids)
        assert_snapshots_equal(before, after)
    finally:
        if path and os.path.exists(path):
            os.remove(path)


@pytest.mark.asyncio
async def test_restore_replaces_not_merges(backup_roundtrip_lock, refresh_db_engine):
    """B3: junk rows and wallet zeroing are gone after restore."""
    ids = await _seed_ids()
    path = None
    try:
        path = await BackupManager().create_backup_file()
        await mutate_database_after_backup(ids)
        assert await junk_user_exists()

        ok = await BackupManager.restore_database(path)
        assert ok is True
        await _reinit_engine()

        assert not await junk_user_exists()
        async with AsyncSessionLocal() as session:
            from vpn_bot.models import User

            active = await session.get(User, ids.user_active_id)
            banned = await session.get(User, ids.user_banned_id)
            assert active is not None
            assert banned is not None
            assert active.wallet_balance == pytest.approx(125_000.5)
    finally:
        if path and os.path.exists(path):
            os.remove(path)


@pytest.mark.asyncio
async def test_encrypted_server_password_survives_roundtrip(
    backup_roundtrip_lock,
    refresh_db_engine,
):
    """B4: Fernet-encrypted server password decrypts correctly after restore."""
    ids = await _seed_ids()
    path = None
    try:
        path = await BackupManager().create_backup_file()
        await mutate_database_after_backup(ids)
        ok = await BackupManager.restore_database(path)
        assert ok is True
        await _reinit_engine()

        async with AsyncSessionLocal() as session:
            from vpn_bot.models import Server

            server = await session.get(Server, ids.server_id)
            assert server is not None
            assert server.password == ids.server_plaintext_password
    finally:
        if path and os.path.exists(path):
            os.remove(path)


@pytest.mark.asyncio
async def test_wallet_and_transactions_survive_roundtrip(
    backup_roundtrip_lock,
    refresh_db_engine,
):
    """B5: wallet balances and transaction rows survive restore."""
    ids = await _seed_ids()
    before = await capture_backup_snapshot(ids)
    path = None
    try:
        path = await BackupManager().create_backup_file()
        await mutate_database_after_backup(ids)
        ok = await BackupManager.restore_database(path)
        assert ok is True
        await _reinit_engine()

        after = await capture_backup_snapshot(ids)
        assert before["user_active"]["wallet_balance"] == after["user_active"]["wallet_balance"]
        assert before["user_banned"]["wallet_balance"] == after["user_banned"]["wallet_balance"]
        assert len(after["transactions"]) == 3
        amounts = sorted(t["amount"] for t in after["transactions"])
        assert amounts == sorted(t["amount"] for t in before["transactions"])
    finally:
        if path and os.path.exists(path):
            os.remove(path)


@pytest.mark.asyncio
async def test_admin_settings_json_survives_roundtrip(
    backup_roundtrip_lock,
    refresh_db_engine,
):
    """B6: admin_settings value_json preserved through roundtrip."""
    ids = await _seed_ids()
    before = await capture_backup_snapshot(ids)
    json_key = ids.admin_setting_keys[1]
    path = None
    try:
        path = await BackupManager().create_backup_file()
        async with AsyncSessionLocal() as session:
            from vpn_bot.models import AdminSetting

            row = await session.get(AdminSetting, json_key)
            row.value_json = {"tampered": True}
            await session.commit()

        ok = await BackupManager.restore_database(path)
        assert ok is True
        await _reinit_engine()

        after = await capture_backup_snapshot(ids)
        before_json = next(s for s in before["admin_settings"] if s["key"] == json_key)
        after_json = next(s for s in after["admin_settings"] if s["key"] == json_key)
        assert after_json["value_json"] == before_json["value_json"]
    finally:
        if path and os.path.exists(path):
            os.remove(path)
