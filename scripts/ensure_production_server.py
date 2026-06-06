#!/usr/bin/env python3
"""Re-create the production Server row from .env if missing after test-data purge."""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(str(ROOT / ".env"), override=True)


async def main() -> int:
    from sqlalchemy import select

    from vpn_bot.database import AsyncSessionLocal, init_db
    from vpn_bot.models import Server
    from vpn_bot.test_data import is_test_server_row

    host = (os.getenv("MIKROTIK_HOST") or "").strip()
    user = (os.getenv("MIKROTIK_USERNAME") or "").strip()
    password = (os.getenv("MIKROTIK_PASSWORD") or "").strip()
    port = int(os.getenv("MIKROTIK_PORT", "8728"))

    if not host or not user or not password:
        print("Set MIKROTIK_HOST, MIKROTIK_USERNAME, MIKROTIK_PASSWORD in .env")
        return 1

    await init_db()
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Server).where(Server.host == host))
        existing = res.scalars().first()
        if existing and not is_test_server_row(existing):
            existing.is_active = True
            existing.username = user
            existing.password = password
            existing.port = port
            await session.commit()
            print(f"Updated existing server id={existing.id} name={existing.name!r} host={host}")
            return 0

        name = os.getenv("MIKROTIK_SERVER_NAME", "MikroTik").strip() or "MikroTik"
        srv = Server(
            name=name,
            host=host,
            port=port,
            username=user,
            password=password,
            is_active=True,
            location="production",
        )
        session.add(srv)
        await session.commit()
        await session.refresh(srv)
        print(f"Created server id={srv.id} name={name!r} host={host}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
