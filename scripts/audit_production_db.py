#!/usr/bin/env python3
"""Read-only audit: production vs test rows in the bot database."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(str(ROOT / ".env"), override=True)


async def audit_db() -> dict:
    from sqlalchemy import func, select

    from vpn_bot.database import AsyncSessionLocal, init_db
    from vpn_bot.models import (
        DiscountCode,
        PaymentReceipt,
        Profile,
        Server,
        Subscription,
        Transaction,
        User,
        WireGuardInterface,
        WireGuardProfile,
        WireGuardSubscription,
    )
    from vpn_bot.test_data import (
        is_test_profile_name,
        is_test_server_row,
        is_test_user_row,
        is_test_wg_interface_name,
        list_purchasable_ovpn_profiles,
        list_purchasable_wg_profiles,
    )

    await init_db()
    report: dict = {"issues": [], "ready_for_production": True}

    async with AsyncSessionLocal() as session:
        servers = (await session.execute(select(Server))).scalars().all()
        users = (await session.execute(select(User))).scalars().all()
        ovpn_profiles = (await session.execute(select(Profile))).scalars().all()
        wg_profiles = (await session.execute(select(WireGuardProfile))).scalars().all()
        ifaces = (await session.execute(select(WireGuardInterface))).scalars().all()

        test_servers = [s for s in servers if is_test_server_row(s)]
        prod_servers = [s for s in servers if s.is_active and not is_test_server_row(s)]
        test_users = [u for u in users if is_test_user_row(u)]
        test_ovpn = [p for p in ovpn_profiles if is_test_profile_name(p.name)]
        test_wg = [p for p in wg_profiles if is_test_profile_name(p.name)]
        test_ifaces = [i for i in ifaces if is_test_wg_interface_name(i.name)]

        report["counts"] = {
            "servers_total": len(servers),
            "servers_production_active": len(prod_servers),
            "servers_test": len(test_servers),
            "users_total": len(users),
            "users_test": len(test_users),
            "ovpn_profiles_total": len(ovpn_profiles),
            "ovpn_profiles_test": len(test_ovpn),
            "wg_profiles_total": len(wg_profiles),
            "wg_profiles_test": len(test_wg),
            "wg_interfaces_total": len(ifaces),
            "wg_interfaces_test": len(test_ifaces),
            "subscriptions_ovpn": int(
                (await session.execute(select(func.count(Subscription.id)))).scalar() or 0
            ),
            "subscriptions_wg": int(
                (await session.execute(select(func.count(WireGuardSubscription.id)))).scalar() or 0
            ),
            "transactions": int(
                (await session.execute(select(func.count(Transaction.id)))).scalar() or 0
            ),
            "receipts": int(
                (await session.execute(select(func.count(PaymentReceipt.id)))).scalar() or 0
            ),
            "discount_codes": int(
                (await session.execute(select(func.count(DiscountCode.id)))).scalar() or 0
            ),
        }

        purchasable_ovpn = await list_purchasable_ovpn_profiles()
        purchasable_wg = await list_purchasable_wg_profiles()
        report["purchasable_plans"] = {
            "ovpn": [p.name for p in purchasable_ovpn],
            "wg": [p.name for p in purchasable_wg],
        }

        if test_servers:
            report["issues"].append(f"{len(test_servers)} test/mock server(s) remain")
            report["test_server_names"] = [s.name for s in test_servers[:20]]
        if test_users:
            report["issues"].append(f"{len(test_users)} test user(s) remain")
        if test_ovpn or test_wg:
            report["issues"].append(
                f"{len(test_ovpn)} OVPN + {len(test_wg)} WG test plan(s) remain"
            )
        if test_ifaces:
            report["issues"].append(f"{len(test_ifaces)} test WG interface(s) remain")
        if not prod_servers:
            report["issues"].append("No active production server row found")
        if not purchasable_ovpn and not purchasable_wg:
            report["issues"].append("No purchasable plans for users")

        report["production_servers"] = [
            {"id": s.id, "name": s.name, "host": s.host, "active": s.is_active}
            for s in servers
            if not is_test_server_row(s)
        ]

        if report["issues"]:
            report["ready_for_production"] = False

    return report


def _print_report(report: dict) -> None:
    print("=== Production DB Audit ===\n")
    counts = report.get("counts", {})
    for key, val in sorted(counts.items()):
        print(f"  {key}: {val}")

    print("\n--- Production servers ---")
    for s in report.get("production_servers", []):
        status = "active" if s["active"] else "inactive"
        print(f"  id={s['id']} [{status}] {s['name']!r} @ {s['host']}")

    print("\n--- Purchasable plans (user menu) ---")
    plans = report.get("purchasable_plans", {})
    print(f"  OVPN: {plans.get('ovpn') or '(none)'}")
    print(f"  WG:   {plans.get('wg') or '(none)'}")

    issues = report.get("issues", [])
    if issues:
        print("\n--- Issues ---")
        for issue in issues:
            print(f"  ! {issue}")
        if names := report.get("test_server_names"):
            for n in names:
                print(f"      - {n!r}")
    else:
        print("\n--- Issues ---")
        print("  (none)")

    ready = report.get("ready_for_production", False)
    print(f"\nReady for production: {'YES' if ready else 'NO'}")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Audit DB for test vs production data")
    parser.add_argument("--json", action="store_true", help="Print JSON instead of text")
    args = parser.parse_args()

    report = await audit_db()
    if args.json:
        import json

        print(json.dumps(report, indent=2, default=str))
    else:
        _print_report(report)
    return 0 if report.get("ready_for_production") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
