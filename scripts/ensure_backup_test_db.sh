#!/usr/bin/env bash
# Create isolated PostgreSQL database for backup roundtrip tests (requires superuser).
set -euo pipefail

DB_NAME="${BACKUP_TEST_DB_NAME:-vpnbot_backup_test}"
DB_OWNER="${BACKUP_TEST_DB_OWNER:-vpnbot}"
PG_HOST="${PGHOST:-localhost}"
PG_PORT="${PGPORT:-5432}"
PG_SUPERUSER="${PG_SUPERUSER:-postgres}"

if command -v createdb >/dev/null 2>&1; then
  CREATEDB=createdb
elif [[ -x /opt/homebrew/opt/libpq/bin/createdb ]]; then
  CREATEDB=/opt/homebrew/opt/libpq/bin/createdb
else
  echo "createdb not found; install PostgreSQL client (libpq)" >&2
  exit 1
fi

if PGPASSWORD="${PG_SUPERUSER_PASSWORD:-}" "$CREATEDB" -h "$PG_HOST" -p "$PG_PORT" -U "$PG_SUPERUSER" -O "$DB_OWNER" "$DB_NAME" 2>/dev/null; then
  echo "Created database $DB_NAME (owner $DB_OWNER)"
else
  echo "Database $DB_NAME may already exist or superuser credentials required." >&2
  echo "Run manually as postgres: CREATE DATABASE $DB_NAME OWNER $DB_OWNER;" >&2
  exit 1
fi
