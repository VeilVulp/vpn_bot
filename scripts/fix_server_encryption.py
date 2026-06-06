#!/usr/bin/env python3
"""
Re-encrypt servers.password with the ENCRYPTION_KEY from .env.

When pytest used DEBUG=true without ENCRYPTION_KEY, passwords were encrypted
with a random per-process key. This script restores them using MIKROTIK_TEST_*
or MIKROTIK_* env vars matching server host/username.

Usage:
  python scripts/fix_server_encryption.py
  python scripts/fix_server_encryption.py --dry-run
  python scripts/fix_server_encryption.py --server-id 1 --password 'secret'
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

import os

load_dotenv(str(ROOT / ".env"), override=True)
os.environ["DEBUG"] = "false"
_env_test = ROOT / ".env.test"
if _env_test.exists():
    load_dotenv(_env_test, override=False)


async def main():
    parser = argparse.ArgumentParser(description="Fix server password encryption")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--server-id", type=int, default=None)
    parser.add_argument("--password", type=str, default=None)
    parser.add_argument(
        "--purge-dbtest",
        action="store_true",
        default=True,
        help="Delete orphan [DBTEST] Mock servers from pytest (default: on)",
    )
    parser.add_argument(
        "--no-purge-dbtest",
        action="store_false",
        dest="purge_dbtest",
        help="Skip purging [DBTEST] mock servers",
    )
    args = parser.parse_args()

    from vpn_bot.server_secrets import (
        purge_dbtest_mock_servers,
        reencrypt_server_passwords,
        validate_all_server_passwords,
    )

    if args.purge_dbtest:
        n = await purge_dbtest_mock_servers()
        print(f"Purged orphan DBTEST mock servers: {n}")

    before = await validate_all_server_passwords(active_only=True)
    print(f"Servers needing fix: {len(before)}")
    for s in before:
        print(f"  - id={s['id']} {s['name']} ({s['host']})")

    stats = await reencrypt_server_passwords(
        server_id=args.server_id,
        password_override=args.password,
        dry_run=args.dry_run,
    )
    print(f"Already OK: {stats['ok']}, fixed: {stats['fixed']}, failed: {len(stats['failed'])}")
    for f in stats["failed"]:
        print(f"  FAILED id={f['id']} {f['name']}: {f['reason']}")

    if not args.dry_run:
        after = await validate_all_server_passwords(active_only=True)
        if after:
            print("Still broken:", after)
            sys.exit(1)
        print("All server passwords decrypt with current ENCRYPTION_KEY.")


if __name__ == "__main__":
    asyncio.run(main())
