#!/usr/bin/env python3
"""
Purge test plans from MikroTik + DB, then create production OVPN & WG plans.

Pricing: 25,000 Toman per GB (5→125k, 10→250k, 15→375k, 20→500k).
Default currency: TOMAN.

Usage (from project root):
  python3 scripts/setup_production_plans.py
  python3 scripts/setup_production_plans.py --server-id 2
  python3 scripts/setup_production_plans.py --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(str(ROOT / ".env"), override=True)
os.environ.setdefault("DEBUG", "false")

from sqlalchemy import delete, select, update

from vpn_bot.admin_profile_service import create_profile_full, delete_profile_full
from vpn_bot.admin_server_service import (
    format_server_list_text,
    get_multi_server_health,
    get_server_by_id,
    get_servers_for_admin_list,
)
from vpn_bot.server_secrets import purge_dbtest_mock_servers
from vpn_bot.admin_wg_service import create_wg_profile, delete_wg_profile
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Profile, Server, WireGuardProfile
from vpn_bot.settings_utils import get_admin_setting, set_admin_setting
from vpn_bot.utils import get_currency_unit, get_profile_price

PRICE_PER_GB_TOMAN = 25_000
VOLUMES_GB = (5, 10, 15, 20)
VALIDITY_DAYS = 30

PRODUCTION_OVPN_NAMES = {f"OVPN-{gb}G" for gb in VOLUMES_GB}
PRODUCTION_WG_NAMES = {f"WG-{gb}G" for gb in VOLUMES_GB}

TEST_NAME_RE = re.compile(
    r"^(INTG_|DBTEST_|INACTIVE_PROF|CHAOS_|Admin-Test|Test )",
    re.I,
)


def is_test_profile_name(name: str) -> bool:
    if not name or name.lower() == "default":
        return False
    if name in PRODUCTION_OVPN_NAMES or name in PRODUCTION_WG_NAMES:
        return False
    return bool(TEST_NAME_RE.match(name)) or "DBTEST" in name.upper()


def should_purge_profile(name: str, *, replace_all: bool) -> bool:
    """Decide if a DB/MikroTik profile row should be removed before recreating plans."""
    if not name or name.lower() == "default":
        return False
    if replace_all:
        return True
    return is_test_profile_name(name)


def price_for_gb(gb: int) -> int:
    return gb * PRICE_PER_GB_TOMAN


async def resolve_live_server(server_id: int | None) -> Server:
    async with AsyncSessionLocal() as session:
        if server_id:
            srv = await session.get(Server, server_id)
            if srv and srv.is_active:
                return srv
            raise SystemExit(f"Active server id={server_id} not found")
        res = await session.execute(
            select(Server).where(Server.is_active == True).order_by(Server.id.desc())
        )
        servers = res.scalars().all()
        real = [s for s in servers if s.host and not s.host.startswith("127.") and s.host != "192.0.2.10"]
        if real:
            return real[0]
        for s in servers:
            if "Live" in (s.name or "") and "[DBTEST]" not in (s.name or ""):
                return s
        if servers:
            return servers[0]
    raise SystemExit("No active server found")


async def deactivate_extra_servers(keep_id: int) -> int:
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Server))
        n = 0
        for s in res.scalars().all():
            if s.id != keep_id and s.is_active:
                s.is_active = False
                n += 1
        await session.commit()
        return n


async def purge_db_profiles(server_id: int, *, replace_all: bool) -> dict:
    stats = {"ovpn_deleted": 0, "ovpn_archived": 0, "wg_deleted": 0, "wg_archived": 0}
    async with AsyncSessionLocal() as session:
        ovpn_rows = (
            await session.execute(select(Profile).where(Profile.server_id == server_id))
        ).scalars().all()
        wg_rows = (
            await session.execute(
                select(WireGuardProfile).where(WireGuardProfile.server_id == server_id)
            )
        ).scalars().all()
        if replace_all:
            wg_rows += (
                await session.execute(
                    select(WireGuardProfile).where(WireGuardProfile.server_id.is_(None))
                )
            ).scalars().all()

    for p in ovpn_rows:
        if not should_purge_profile(p.name, replace_all=replace_all):
            continue
        ok, detail = await delete_profile_full(p.id)
        if detail and "Archived" in detail:
            stats["ovpn_archived"] += 1
        elif ok:
            stats["ovpn_deleted"] += 1

    seen_wg: set[int] = set()
    for p in wg_rows:
        if p.id in seen_wg:
            continue
        seen_wg.add(p.id)
        if not should_purge_profile(p.name, replace_all=replace_all):
            continue
        result = await delete_wg_profile(p.id)
        if isinstance(result, tuple):
            ok, detail = result
        else:
            ok, detail = bool(result), ""
        if detail == "Archived":
            stats["wg_archived"] += 1
        elif ok:
            stats["wg_deleted"] += 1

    if replace_all:
        async with AsyncSessionLocal() as session:
            for p in (
                await session.execute(select(Profile).where(Profile.server_id == server_id))
            ).scalars().all():
                if p.name in PRODUCTION_OVPN_NAMES:
                    continue
                await session.delete(p)
                stats["ovpn_deleted"] += 1
            for p in (
                await session.execute(
                    select(WireGuardProfile).where(
                        (WireGuardProfile.server_id == server_id)
                        | (WireGuardProfile.server_id.is_(None))
                    )
                )
            ).scalars().all():
                if p.name in PRODUCTION_WG_NAMES:
                    continue
                await session.delete(p)
                stats["wg_deleted"] += 1
            await session.commit()
    return stats


async def purge_um_profiles_on_router(server: Server, dry_run: bool, *, replace_all: bool) -> list[str]:
    """Remove test User-Manager profiles from MikroTik (keeps default + production)."""
    from vpn_bot.mikrotik_manager import get_mikrotik_manager

    removed = []
    mgr = get_mikrotik_manager(server)
    await asyncio.to_thread(mgr.connect)
    try:
        prof_api = mgr._get_resource("/user-manager/profile")
        lim_api = mgr._get_resource("/user-manager/limitation")
        pl_api = mgr._get_resource("/user-manager/profile-limitation")

        all_profs = await asyncio.to_thread(prof_api.get)
        for row in all_profs or []:
            name = row.get("name") or ""
            if not should_purge_profile(name, replace_all=replace_all):
                continue
            if dry_run:
                removed.append(name)
                continue
            lim_name = f"lim_{name}"
            try:
                links = await asyncio.to_thread(pl_api.get, profile=name)
                for link in links or []:
                    await asyncio.to_thread(pl_api.remove, id=link["id"])
                lims = await asyncio.to_thread(lim_api.get, name=lim_name)
                if lims:
                    await asyncio.to_thread(lim_api.remove, id=lims[0]["id"])
                await asyncio.to_thread(prof_api.remove, id=row["id"])
                removed.append(name)
            except Exception as e:
                print(f"  WARN: could not remove UM profile {name}: {e}")
    finally:
        await asyncio.to_thread(mgr.close)
    return removed


async def ensure_currency_toman() -> str:
    cur = await get_admin_setting("currency_unit", "USD")
    cur = (cur or "USD").strip('"').strip("'")
    if cur != "TOMAN":
        await set_admin_setting("currency_unit", "TOMAN")
        return "TOMAN (updated)"
    return "TOMAN (already set)"


async def ensure_sales_on() -> None:
    for key in ("sales_global_active", "sales_ovpn_active", "sales_wg_active"):
        val = await get_admin_setting(key, "true")
        if str(val).lower() in ("false", "0", "no"):
            await set_admin_setting(key, "true")


async def create_production_plans(server_id: int, dry_run: bool) -> dict:
    created = {"ovpn": [], "wg": []}
    if dry_run:
        for gb in VOLUMES_GB:
            created["ovpn"].append(f"OVPN-{gb}G")
            created["wg"].append(f"WG-{gb}G")
        return created

    for gb in VOLUMES_GB:
        price = price_for_gb(gb)
        name = f"OVPN-{gb}G"
        prof, err = await create_profile_full(
            {
                "name": name,
                "server_id": server_id,
                "days": VALIDITY_DAYS,
                "limit": gb,
                "price_toman": price,
                "price_usd": 0,
            }
        )
        if not prof:
            raise RuntimeError(f"OVPN plan {name}: {err}")
        created["ovpn"].append(name)

    for gb in VOLUMES_GB:
        price = price_for_gb(gb)
        name = f"WG-{gb}G"
        prof = await create_wg_profile(
            {
                "name": name,
                "volume": gb,
                "days": VALIDITY_DAYS,
                "price_toman": price,
                "server_id": server_id,
            }
        )
        created["wg"].append(prof.name)

    return created


async def verify_setup(server_id: int) -> bool:
    ok = True
    unit = await get_currency_unit()
    print(f"\n=== Verification ===")
    print(f"Currency unit: {unit}")
    if unit != "TOMAN":
        print("  FAIL: expected TOMAN")
        ok = False

    async with AsyncSessionLocal() as session:
        ovpn = (
            await session.execute(
                select(Profile).where(
                    Profile.server_id == server_id,
                    Profile.is_active == True,
                )
            )
        ).scalars().all()
        wg = (
            await session.execute(
                select(WireGuardProfile).where(
                    WireGuardProfile.server_id == server_id,
                    WireGuardProfile.is_active == True,
                )
            )
        ).scalars().all()

    print(f"Active OVPN plans on server {server_id}: {len(ovpn)}")
    for p in sorted(ovpn, key=lambda x: x.data_limit_gb):
        price = await get_profile_price(p)
        expected = price_for_gb(p.data_limit_gb)
        mark = "OK" if int(price) == expected else "MISMATCH"
        print(f"  {p.name}: {p.data_limit_gb}GB, {int(price):,} Toman [{mark}]")
        if mark != "OK":
            ok = False
        if is_test_profile_name(p.name):
            print(f"  FAIL: test name still active: {p.name}")
            ok = False

    print(f"Active WG plans on server {server_id}: {len(wg)}")
    for p in sorted(wg, key=lambda x: x.volume_gb or 0):
        expected = price_for_gb(p.volume_gb or 0)
        mark = "OK" if int(p.price_toman) == expected else "MISMATCH"
        print(f"  {p.name}: {p.volume_gb}GB, {int(p.price_toman):,} Toman [{mark}]")
        if mark != "OK":
            ok = False

    servers = await get_servers_for_admin_list()
    health = await get_multi_server_health(servers)
    text = await format_server_list_text(servers, health)
    print("\nAdmin server list preview:")
    print(text[:1200] + ("..." if len(text) > 1200 else ""))

    live = await get_server_by_id(server_id)
    if live:
        from vpn_bot.server_secrets import try_decrypt_stored

        ok_pw, _ = try_decrypt_stored(live._password)
        print(f"\nLive server credentials decrypt: {'OK' if ok_pw else 'FAIL'}")
        if not ok_pw:
            ok = False

    expected_ovpn = len(PRODUCTION_OVPN_NAMES)
    expected_wg = len(PRODUCTION_WG_NAMES)
    if len(ovpn) != expected_ovpn or len(wg) != expected_wg:
        print(
            f"  FAIL: expected {expected_ovpn} OVPN + {expected_wg} WG, "
            f"got {len(ovpn)} + {len(wg)}"
        )
        ok = False

    print("\nPurchase flow (code paths):")
    print("  OVPN: buy_service → buy_plan_* → confirm_pay → checkout_subscription → send_config_files (L2TP/SSTP + OVPN files if configured)")
    print("  WG:   buy_wg → buy_wg_plan_* → finalize_wg_purchase → QR/config delivery")
    return ok


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--server-id", type=int, default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--replace-all",
        action="store_true",
        help="Remove ALL non-default plans on the target server (not only test names), then recreate 5/10/15/20 GB.",
    )
    args = parser.parse_args()

    server = await resolve_live_server(args.server_id)
    print(f"Target server: id={server.id} name={server.name} host={server.host}")

    if not args.dry_run:
        n = await deactivate_extra_servers(server.id)
        if n:
            print(f"Deactivated {n} duplicate/test server row(s)")
        purged = await purge_dbtest_mock_servers()
        if purged:
            print(f"DBTEST server cleanup: {purged} row(s) processed")

    mode = "replace-all" if args.replace_all else "test-only"
    print(f"\n--- Purge mode: {mode} ---")

    print("\n--- Purge MikroTik User-Manager profiles ---")
    mt_removed = await purge_um_profiles_on_router(
        server, args.dry_run, replace_all=args.replace_all
    )
    print(f"UM profiles to remove: {len(mt_removed)}")
    for name in mt_removed[:30]:
        print(f"  - {name}")
    if len(mt_removed) > 30:
        print(f"  ... and {len(mt_removed) - 30} more")

    if not args.dry_run:
        print("\n--- Purge DB profiles ---")
        stats = await purge_db_profiles(server.id, replace_all=args.replace_all)
        if args.replace_all:
            extra = await purge_db_profiles(server.id, replace_all=False)
            for k in stats:
                stats[k] += extra.get(k, 0)
        print(f"DB purge stats: {stats}")

    print("\n--- Currency & sales ---")
    if not args.dry_run:
        print(f"Currency: {await ensure_currency_toman()}")
        await ensure_sales_on()
        print("Sales toggles: enabled (global, ovpn, wg)")
    else:
        print("(dry-run: skipped)")

    print("\n--- Create production plans ---")
    if not args.dry_run:
        # Remove stale production rows if re-running
        async with AsyncSessionLocal() as session:
            for name in PRODUCTION_OVPN_NAMES | PRODUCTION_WG_NAMES:
                for model, col in ((Profile, Profile.name), (WireGuardProfile, WireGuardProfile.name)):
                    row = (
                        await session.execute(select(model).where(col == name))
                    ).scalars().first()
                    if row:
                        if model is Profile:
                            await delete_profile_full(row.id)
                        else:
                            await delete_wg_profile(row.id)

    created = await create_production_plans(server.id, args.dry_run)
    print(f"OVPN: {created['ovpn']}")
    print(f"WG:   {created['wg']}")

    if args.dry_run:
        print("\nDry-run complete. Re-run without --dry-run to apply.")
        return 0

    if not await verify_setup(server.id):
        return 1
    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
