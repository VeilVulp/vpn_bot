#!/usr/bin/env python3
"""Remove ALL discount codes and related redemption rows from the database."""
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
    from vpn_bot.admin_discount_service import purge_all_discount_codes
    from vpn_bot.database import init_db

    parser = argparse.ArgumentParser(description="Purge all discount codes from DB")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only print counts; do not delete",
    )
    parser.add_argument(
        "--confirm",
        action="store_true",
        help="Required to perform actual deletion",
    )
    args = parser.parse_args()

    if not args.dry_run and not args.confirm:
        print("Refusing to purge without --confirm (use --dry-run to preview counts).")
        return 1

    await init_db()
    stats = await purge_all_discount_codes(dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else "DONE"
    print(f"[{mode}] Discount code purge:")
    for key, val in sorted(stats.items()):
        print(f"  {key}: {val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
