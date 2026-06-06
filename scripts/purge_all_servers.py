#!/usr/bin/env python3
"""Remove ALL servers and their linked plans/subs/interfaces from the database."""
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
    from vpn_bot.test_data import purge_all_servers

    parser = argparse.ArgumentParser(description="Delete every server row and dependencies")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    await init_db()
    stats = await purge_all_servers(dry_run=args.dry_run)
    mode = "DRY RUN" if args.dry_run else "DONE"
    print(f"[{mode}] All servers purge:")
    for key, val in sorted(stats.items()):
        if val:
            print(f"  {key}: {val}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
