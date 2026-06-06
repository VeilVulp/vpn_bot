#!/usr/bin/env python3
"""Remove all pytest / DBTEST data from the database (servers, users, profiles, WG)."""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(str(ROOT / ".env"), override=True)


async def main() -> int:
    from vpn_bot.database import init_db
    from vpn_bot.test_data import purge_all_test_data, purge_test_plans_only

    parser = argparse.ArgumentParser(description="Purge DBTEST and pytest rows from DB")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print what would be removed",
    )
    parser.add_argument(
        "--plans-only",
        action="store_true",
        help="Remove only test OVPN & WireGuard plans (not servers/users)",
    )
    args = parser.parse_args()

    await init_db()
    if args.plans_only:
        stats = await purge_test_plans_only(dry_run=args.dry_run)
    else:
        stats = await purge_all_test_data(dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else "DONE"
    print(f"[{mode}] Test data purge:")
    for key, val in sorted(stats.items()):
        if val:
            print(f"  {key}: {val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
