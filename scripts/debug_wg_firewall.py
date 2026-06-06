#!/usr/bin/env python3
"""
Compare WireGuard interface firewall settings in DB vs MikroTik router rules.

Usage:
  python scripts/debug_wg_firewall.py --server-id 1
  python scripts/debug_wg_firewall.py --interface-id 5
"""

from __future__ import annotations

import argparse
import asyncio
import ipaddress
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _subnet(address: str) -> str:
    try:
        return str(ipaddress.ip_interface(address).network)
    except ValueError:
        return address


def _fetch_rules(mgr, resource: str, comment: str) -> list[dict]:
    mgr.connect()
    try:
        api = mgr.api.get_resource(resource)
        return api.get(comment=comment) or []
    finally:
        mgr.close()


async def _audit_interface(interface_id: int) -> int:
    from sqlalchemy import select

    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.mikrotik_manager import get_mikrotik_manager
    from vpn_bot.models import Server, WireGuardInterface

    async with AsyncSessionLocal() as session:
        iface = await session.get(WireGuardInterface, interface_id)
        if not iface:
            print(f"Interface id={interface_id} not found in DB")
            return 1
        server = await session.get(Server, iface.server_id)
        if not server:
            print(f"Server id={iface.server_id} not found")
            return 1

    print(f"\n=== DB: {iface.name} (id={iface.id}, server={server.name}) ===")
    print(f"  address:            {iface.address}")
    print(f"  listen_port:        {iface.listen_port}")
    print(f"  upstream_interface: {iface.upstream_interface}")
    print(f"  routing_mark:       {iface.routing_mark}")
    print(f"  nat_routing_mark:   {iface.nat_routing_mark}")
    print(f"  nat_dst_address:    {iface.nat_dst_address}")
    print(f"  gateway:            {iface.gateway}")
    print(f"  current/max users:  {iface.current_users}/{iface.max_users}")

    subnet = _subnet(iface.address)
    mgr = get_mikrotik_manager(server)
    prefixes = ("nat", "mangle", "filter", "route")
    mismatches = 0

    print(f"\n=== Router rules (subnet={subnet}) ===")
    for kind in prefixes:
        comment = f"managed-by-bot-wg-{kind}-{iface.name}"
        resource = {
            "nat": "/ip/firewall/nat",
            "mangle": "/ip/firewall/mangle",
            "filter": "/ip/firewall/filter",
            "route": "/ip/route",
        }[kind]
        rules = await asyncio.to_thread(_fetch_rules, mgr, resource, comment)
        print(f"\n  [{kind}] comment={comment} count={len(rules)}")
        for r in rules:
            print(f"    {r}")
        if kind == "nat" and iface.upstream_interface:
            if not rules:
                print("    MISMATCH: expected NAT rule, none found")
                mismatches += 1
            elif rules[0].get("out-interface") != iface.upstream_interface:
                print(
                    f"    MISMATCH: out-interface={rules[0].get('out-interface')} "
                    f"expected {iface.upstream_interface}"
                )
                mismatches += 1
            if iface.nat_routing_mark:
                if rules[0].get("routing-mark") != iface.nat_routing_mark:
                    print(
                        f"    MISMATCH: routing-mark={rules[0].get('routing-mark')} "
                        f"expected {iface.nat_routing_mark}"
                    )
                    mismatches += 1
            elif rules and rules[0].get("routing-mark"):
                print(
                    f"    NOTE: NAT has routing-mark={rules[0].get('routing-mark')} "
                    "but DB nat_routing_mark is empty"
                )
        if kind == "mangle" and iface.routing_mark:
            if not rules:
                print("    MISMATCH: expected Mangle rule, none found")
                mismatches += 1
            elif rules[0].get("new-routing-mark") != iface.routing_mark:
                print(
                    f"    MISMATCH: new-routing-mark={rules[0].get('new-routing-mark')} "
                    f"expected {iface.routing_mark}"
                )
                mismatches += 1

    if mismatches:
        print(f"\nAudit finished with {mismatches} mismatch(es).")
        return 2
    print("\nAudit OK (no obvious mismatches).")
    return 0


async def _list_server_interfaces(server_id: int) -> int:
    from sqlalchemy import select

    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.models import WireGuardInterface

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WireGuardInterface)
            .where(WireGuardInterface.server_id == server_id)
            .order_by(WireGuardInterface.id.asc())
        )
        ifaces = res.scalars().all()

    if not ifaces:
        print(f"No interfaces for server_id={server_id}")
        return 1

    print(f"Interfaces on server_id={server_id}:")
    for i in ifaces:
        print(
            f"  id={i.id} name={i.name} users={i.current_users}/{i.max_users} "
            f"upstream={i.upstream_interface} rm={i.routing_mark}"
        )
    return 0


async def main() -> int:
    from vpn_bot.database import init_db

    parser = argparse.ArgumentParser(description="Audit WG firewall DB vs MikroTik")
    parser.add_argument("--server-id", type=int, help="List interfaces on server")
    parser.add_argument("--interface-id", type=int, help="Audit one interface")
    args = parser.parse_args()

    await init_db()

    if args.interface_id:
        return await _audit_interface(args.interface_id)
    if args.server_id:
        return await _list_server_interfaces(args.server_id)

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
