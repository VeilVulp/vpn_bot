#!/usr/bin/env bash
# Live chaos suite: concurrent admin + user + sync/cleanup on real MikroTik.
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
export DB_POOL_SIZE="${DB_POOL_SIZE:-15}"
export DB_MAX_OVERFLOW="${DB_MAX_OVERFLOW:-25}"
export CHAOS_MT_SEM="${CHAOS_MT_SEM:-4}"

PYTHON="${PYTHON:-python3}"
if [[ -d .venv ]]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

mkdir -p reports

echo "=== Live chaos suite (real MikroTik) ==="
"$PYTHON" -m pytest tests/live/test_live_chaos_concurrent.py \
  -m "live_mt and chaos_live" \
  -v --tb=short \
  "$@"

if [[ -f reports/live_chaos_last.json ]]; then
  echo ""
  echo "Report: reports/live_chaos_last.json"
  "$PYTHON" scripts/load_report.py reports/live_chaos_last.json 2>/dev/null || true
fi

if [[ "${RUN_WG_STRESS:-0}" == "1" ]]; then
  export LIVE_ADMIN_ACK_PRODUCTION="${LIVE_ADMIN_ACK_PRODUCTION:-1}"
  export LIVE_WG_STRESS_MT_SEM="${LIVE_WG_STRESS_MT_SEM:-4}"
  echo ""
  echo "=== WG stress orchestrator (real MikroTik) ==="
  "$PYTHON" -m pytest tests/live/test_live_wg_stress_orchestrator.py \
    -m wg_stress_live \
    -v --tb=short \
    "$@"
  if [[ -f reports/live_wg_stress_last.json ]]; then
    echo "Report: reports/live_wg_stress_last.json"
    "$PYTHON" scripts/load_report.py reports/live_wg_stress_last.json 2>/dev/null || true
  fi
fi
