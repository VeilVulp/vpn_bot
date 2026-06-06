#!/usr/bin/env bash
# Run database + mock-load tests (no live MikroTik).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f .env.test ]]; then
  set -a
  # shellcheck disable=SC1091
  source <(grep -v '^#' .env.test | sed 's/^/export /')
  set +a
elif [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source <(grep -v '^#' .env | sed 's/^/export /')
  set +a
fi

export DEBUG="${DEBUG:-true}"
PYTHON="${PYTHON:-python3}"
if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "=== DB + load + security test suite (mock MikroTik) ==="
"$PYTHON" -m pytest tests/db tests/load tests/security tests/admin/test_group_security.py \
  tests/test_wallet_receipt_atomic.py \
  -m "db or security" \
  -v --tb=short \
  "$@"

REPORT="${ROOT}/.pytest_db_report.json"
if command -v pytest >/dev/null 2>&1; then
  "$PYTHON" -m pytest tests/db tests/load --co -q >/dev/null 2>&1 || true
fi

if [[ "${RUN_BACKUP_ROUNDTRIP:-0}" == "1" ]]; then
  if [[ "${ALLOW_DESTRUCTIVE_BACKUP_TEST:-0}" != "1" ]]; then
    echo "SKIP backup roundtrip: set ALLOW_DESTRUCTIVE_BACKUP_TEST=1 and BACKUP_TEST_DATABASE_URL"
  elif [[ -z "${BACKUP_TEST_DATABASE_URL:-}" ]]; then
    echo "SKIP backup roundtrip: BACKUP_TEST_DATABASE_URL not set"
  else
    echo ""
    echo "=== Backup roundtrip (isolated DB, serial) ==="
    "$PYTHON" -m pytest tests/db/test_backup_restore_roundtrip.py -m db_backup -v --tb=short
  fi
fi

echo ""
echo "Done. See docs/DATABASE_TESTING.md for findings catalog (V1–V10) and backup B1–B6."
echo "Optional JSON report: pytest --json-report --json-report-file=$REPORT"
