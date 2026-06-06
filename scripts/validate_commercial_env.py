#!/usr/bin/env python3
"""Validate production .env against commercial deployment checklist (read-only)."""

from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

from vpn_bot._paths import PROJECT_ROOT

load_dotenv(PROJECT_ROOT / ".env")

WARNINGS: list[str] = []
ERRORS: list[str] = []


def _check(name: str, ok: bool, *, error_msg: str, warn: bool = False) -> None:
    if ok:
        return
    (WARNINGS if warn else ERRORS).append(f"{name}: {error_msg}")


def main() -> int:
    enc = os.getenv("ENCRYPTION_KEY", "").strip()
    _check("ENCRYPTION_KEY", bool(enc), error_msg="not set")
    _check("DEBUG", os.getenv("DEBUG", "False").lower() != "true", error_msg="DEBUG must be false in production", warn=False)

    admin_ids = os.getenv("ADMIN_IDS", "").strip()
    _check("ADMIN_IDS", bool(admin_ids), error_msg="no super-admin IDs configured")

    bot_token = os.getenv("BOT_TOKEN", "").strip()
    _check("BOT_TOKEN", bool(bot_token), error_msg="not set")

    db_url = os.getenv("DATABASE_URL", "").strip()
    _check("DATABASE_URL", db_url.startswith("postgresql"), error_msg="PostgreSQL URL required")

    ssl_verify = os.getenv("MIKROTIK_SSL_VERIFY", "true").lower()
    _check(
        "MIKROTIK_SSL_VERIFY",
        ssl_verify in ("true", "1", "yes"),
        error_msg=f"expected true for production TLS (got {ssl_verify!r})",
        warn=True,
    )

    if not os.getenv("REDIS_URL", "").strip():
        WARNINGS.append("REDIS_URL: not set (required for multi-instance rate limiting)")

    if not os.getenv("BACKUP_GROUP_ID", "").strip():
        WARNINGS.append("BACKUP_GROUP_ID: not set (Telegram DB backups disabled)")

    if not os.getenv("SENTRY_DSN", "").strip():
        WARNINGS.append("SENTRY_DSN: not set (optional exception alerting)")

    for msg in WARNINGS:
        print(f"WARN  {msg}")
    for msg in ERRORS:
        print(f"ERROR {msg}", file=sys.stderr)

    if ERRORS:
        print("\nCommercial env validation FAILED.", file=sys.stderr)
        return 1
    if WARNINGS:
        print("\nCommercial env validation passed with warnings.")
    else:
        print("\nCommercial env validation passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
