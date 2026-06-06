"""Server password encryption health checks and repair helpers."""

from __future__ import annotations

import os
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken

from vpn_bot.utils import logger


def get_fernet() -> Fernet:
    from vpn_bot.models import KEY

    return Fernet(KEY.encode())


def try_decrypt_stored(blob: str) -> tuple[bool, Optional[str]]:
    """Return (ok, plaintext). Does not use DEBUG fallback."""
    if not blob:
        return False, None
    try:
        return True, get_fernet().decrypt(blob.encode()).decode()
    except (InvalidToken, Exception):
        return False, None


def encrypt_plaintext(plain: str) -> str:
    from vpn_bot.models import encrypt_text

    return encrypt_text(plain)


def resolve_plaintext_for_server(host: str, username: str) -> Optional[str]:
    """Map server row to env credentials (test or default MikroTik vars)."""
    host = (host or "").strip()
    user = (username or "").strip()

    test_host = os.getenv("MIKROTIK_TEST_HOST", "").strip()
    test_user = os.getenv("MIKROTIK_TEST_USERNAME", "").strip()
    test_pass = os.getenv("MIKROTIK_TEST_PASSWORD", "").strip()
    if test_host and test_pass and host == test_host:
        if not test_user or user == test_user:
            return test_pass

    def_host = os.getenv("MIKROTIK_HOST", "").strip()
    def_user = os.getenv("MIKROTIK_USERNAME", "").strip()
    def_pass = os.getenv("MIKROTIK_PASSWORD", "").strip()
    if def_host and def_pass and host == def_host:
        if not def_user or user == def_user:
            return def_pass

    return None


async def purge_dbtest_mock_servers() -> int:
    """Remove pytest mock servers, test users, profiles, and WG interfaces."""
    from vpn_bot.test_data import purge_all_test_data

    stats = await purge_all_test_data(dry_run=False)
    total = sum(stats.values())
    if total:
        logger.info(f"Test data cleanup: {stats}")
    return total


async def validate_all_server_passwords(active_only: bool = True) -> list[dict]:
    """Return list of servers that cannot be decrypted with current ENCRYPTION_KEY."""
    from sqlalchemy import select

    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.models import Server

    bad = []
    async with AsyncSessionLocal() as session:
        q = select(Server)
        if active_only:
            q = q.where(Server.is_active == True)
        servers = (await session.execute(q)).scalars().all()
        for s in servers:
            ok, _ = try_decrypt_stored(s._password)
            if not ok:
                bad.append(
                    {
                        "id": s.id,
                        "name": s.name,
                        "host": s.host,
                        "username": s.username,
                    }
                )
    return bad


async def reencrypt_server_passwords(
    *,
    server_id: int | None = None,
    password_override: str | None = None,
    dry_run: bool = False,
) -> dict:
    """
    Re-encrypt server._password with current ENCRYPTION_KEY.
    Uses password_override, or env MIKROTIK_TEST_* / MIKROTIK_* matching host.
    """
    from sqlalchemy import select

    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.models import Server

    stats = {"ok": 0, "fixed": 0, "failed": []}

    async with AsyncSessionLocal() as session:
        q = select(Server)
        if server_id is not None:
            q = q.where(Server.id == server_id)
        servers = (await session.execute(q)).scalars().all()

        for s in servers:
            ok, plain = try_decrypt_stored(s._password)
            if ok:
                stats["ok"] += 1
                continue

            plain = password_override or resolve_plaintext_for_server(s.host, s.username)
            if not plain:
                stats["failed"].append(
                    {
                        "id": s.id,
                        "name": s.name,
                        "host": s.host,
                        "reason": "no_plaintext_source",
                    }
                )
                continue

            if dry_run:
                stats["fixed"] += 1
                logger.info(f"[dry-run] Would re-encrypt server {s.name} ({s.host})")
                continue

            s.password = plain
            stats["fixed"] += 1
            logger.info(f"Re-encrypted password for server {s.name} (id={s.id})")

        if not dry_run:
            await session.commit()

    return stats
