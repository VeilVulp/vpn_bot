#!/usr/bin/env python3
"""Production health check — exit 0 when DB (and optional MikroTik sample) are reachable."""

from __future__ import annotations

import asyncio
import os
import sys

from dotenv import load_dotenv

from vpn_bot._paths import PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")


async def check_database() -> bool:
    from sqlalchemy import text

    from vpn_bot.database import AsyncSessionLocal

    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception as exc:
        print(f"DB check failed: {exc}", file=sys.stderr)
        return False


async def check_mikrotik_sample() -> bool:
    host = os.getenv("MIKROTIK_TEST_HOST") or os.getenv("MIKROTIK_HOST", "").strip()
    if not host:
        print("MikroTik sample skipped (no MIKROTIK_TEST_HOST / MIKROTIK_HOST)")
        return True

    from vpn_bot.mikrotik_manager import MikroTikManager

    username = os.getenv("MIKROTIK_TEST_USERNAME") or os.getenv("MIKROTIK_USERNAME", "")
    password = os.getenv("MIKROTIK_TEST_PASSWORD") or os.getenv("MIKROTIK_PASSWORD", "")
    port = int(os.getenv("MIKROTIK_TEST_PORT") or os.getenv("MIKROTIK_PORT", "8728"))

    try:
        mgr = MikroTikManager(host, username, password, port=port, use_pool=False)
        await asyncio.to_thread(mgr.connect)
        mgr.close()
        return True
    except Exception as exc:
        print(f"MikroTik check failed: {exc}", file=sys.stderr)
        return False


async def main() -> int:
    db_ok = await check_database()
    mt_ok = await check_mikrotik_sample()
    if db_ok and mt_ok:
        print("health: ok")
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
