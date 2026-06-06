#!/usr/bin/env bash
# Run comprehensive traffic / quota / expiry policy tests (mock MikroTik).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

export DEBUG=false
if [[ -f .env ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

echo "=== Traffic policy suite (mock) ==="
python3 -m pytest \
  tests/db/test_traffic_policy_comprehensive.py \
  tests/db/test_expiry_matrix.py \
  tests/db/test_renewal_policy.py \
  -v --tb=short "$@"

echo ""
echo "=== Optional live checks ==="
echo "  pytest tests/live/test_live_traffic_policy.py -m live_mt -v"
