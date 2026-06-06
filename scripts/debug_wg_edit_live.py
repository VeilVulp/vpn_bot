#!/usr/bin/env python3
"""Live debug: WG edit flows vs MikroTik (upstream list, routing tables, apply automation)."""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


async def _first_server_id() -> int | None:
    from sqlalchemy import select

    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.models import Server

    async with AsyncSessionLocal() as session:
        row = (await session.execute(select(Server.id).limit(1))).scalar()
        return row


async def _first_iface(server_id: int):
    from sqlalchemy import select

    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.models import WireGuardInterface

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WireGuardInterface)
            .where(WireGuardInterface.server_id == server_id)
            .order_by(WireGuardInterface.id.asc())
            .limit(1)
        )
        return res.scalars().first()


async def main() -> int:
    parser = argparse.ArgumentParser(description="Debug WG admin edit MikroTik calls")
    parser.add_argument("--server-id", type=int, help="Server id (default: first in DB)")
    parser.add_argument("--apply", action="store_true", help="Run apply_wg_automation_timed on first iface")
    args = parser.parse_args()

    from vpn_bot.admin_wg_service import (
        apply_wg_automation_timed,
        fetch_routing_tables_timed,
        fetch_upstream_interfaces_timed,
    )
    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.models import Server

    server_id = args.server_id or await _first_server_id()
    if not server_id:
        print("No server in DB")
        return 1

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
    if not server:
        print(f"Server id={server_id} not found")
        return 1

    print(f"Server: {server.name} ({server.host}:{server.port})")

    t0 = time.monotonic()
    upstream, u_err = await fetch_upstream_interfaces_timed(server)
    print(f"upstream ({time.monotonic() - t0:.2f}s): count={len(upstream or [])} err={u_err}")
    for row in (upstream or [])[:8]:
        print(f"  - {row}")
    if upstream and len(upstream) > 8:
        print(f"  ... +{len(upstream) - 8} more")

    t0 = time.monotonic()
    marks, m_err = await fetch_routing_tables_timed(server)
    print(f"routing_tables ({time.monotonic() - t0:.2f}s): {marks} err={m_err}")

    if args.apply:
        iface = await _first_iface(server_id)
        if not iface:
            print("No WG interface on server")
            return 1
        print(f"Applying automation for {iface.name} (id={iface.id}) …")
        t0 = time.monotonic()
        ok, err, route_applied = await apply_wg_automation_timed(iface.id)
        print(f"apply ok={ok} route_applied={route_applied} err={err!r}")
        print(f"apply ({time.monotonic() - t0:.2f}s): ok={ok} err={err}")

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
