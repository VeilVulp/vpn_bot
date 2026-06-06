# Live MikroTik Testing

## Prerequisites

- RouterOS with User Manager v7 enabled
- API service on port **8728** (or API-SSL on **443**)
- Test machine IP allowed in firewall
- PostgreSQL database reachable (`DATABASE_URL` in `.env.test`)
- `ENCRYPTION_KEY` set in `.env.test` (or `DEBUG=true` for ephemeral dev key)

**Do not use Winbox port 8297 for API tests.**

## Setup

```bash
cp .env.test.example .env.test
# Edit: MIKROTIK_TEST_*, DATABASE_URL, ENCRYPTION_KEY
```

## Run tests

```bash
source .venv/bin/activate
pip install -r requirements.txt
export $(grep -v '^#' .env.test | xargs)

# Full suite (~5–8 min on live router)
pytest -m live_mt -v

# WG workflow only (~3–5 min)
pytest tests/live/test_live_wg_workflow.py -m live_mt -v

# WG Telegram renewal E2E (requires LIVE_ADMIN_ACK_PRODUCTION=1)
pytest tests/live/test_live_wg_renewal_e2e.py -m live_admin_e2e -v

# WG concurrent capacity smoke (real MikroTik, ~1.5 min)
pytest tests/live/test_live_wg_capacity_concurrent.py -m live_mt -v

# WG full stress orchestrator (wallet E2E + multi-iface + concurrent load, ~8–15 min)
export LIVE_ADMIN_ACK_PRODUCTION=1
pytest tests/live/test_live_wg_stress_orchestrator.py -m wg_stress_live -v
python scripts/load_report.py reports/live_wg_stress_last.json

# Unit tests only (no router)
pytest tests/test_wallet_receipt_atomic.py -v

# Via manage.sh (sudo)
./manage.sh  # Advanced Options → 7) Run Live Tests
```

## Test modules

| File | Coverage |
|------|----------|
| `test_00_connectivity.py` | API 8728 / SSL 443 |
| `test_live_ovpn_purchase.py` | OVPN checkout |
| `test_live_wg_purchase.py` | WG purchase (smoke) |
| `test_live_wg_workflow.py` | WG full workflow: purchase, config, quota/time expiry, renewal, cleanup |
| `test_live_wg_renewal_e2e.py` | WG renewal via Telegram callbacks (`live_admin_e2e`) |
| `test_live_wg_capacity_concurrent.py` | WG concurrent capacity smoke: purchase, renew, expiry, cleanup |
| `test_live_wg_stress_orchestrator.py` | WG stress orchestrator: wallet/receipt Telegram E2E, burst purchase/renew, multi-iface capacity, slot reuse, cleanup, spillover, finale load (`wg_stress_live`) |
| `test_admin_subscription_ops.py` | extend, reset, data, toggle |
| `test_wallet_receipt_live.py` | deposit, deduct, approve/reject |
| `test_live_sync_manager.py` | OVPN reconcile |
| `test_live_cleanup.py` | cleanup services |
| `test_live_renewal.py` | OVPN renewal |
| `test_live_profile_sync.py` | UM profile on router |
| `test_real_integration.py` | 25 workflow smoke tests |
| `test_smoke_simulates.py` | legacy simulate_* parity |
| `test_wg_interface_pytest.py` | WG interface params |
| `test_wg_firewall_live.py` | WG NAT/Mangle rules on router after `sync_wg_interface_automation` |
| `test_security_banned.py` | banned user blocked |
| `test_load_live_subset.py` | parallel load subset (Semaphore 5) |
| `test_live_chaos_concurrent.py` | production-like chaos (admin+user+sync+cleanup) |
| `test_admin_menu_crawl_live.py` | BFS crawl of admin menus via `process_update` |
| `test_admin_panel_e2e_live.py` | Domain E2E: user hub, OVPN/WG MT, receipts, tickets |
| `test_admin_panel_orchestrator.py` | Combined matrix + `reports/admin_panel_e2e_last.json` |

## Admin panel E2E (Telegram handlers + real MikroTik)

Uses the same handler registration as production ([`vpn_bot/handler_registry.py`](../vpn_bot/handler_registry.py)) with a recording bot (no real Telegram HTTP).

**Required:**

```bash
export LIVE_ADMIN_ACK_PRODUCTION=1   # explicit ack for production router
export ADMIN_IDS=<your_telegram_id>  # must be admin in .env
```

**Optional:**

```bash
export LIVE_ADMIN_FULL_DESTRUCTIVE=1  # allow force_clean / delete_account tests
export ADMIN_E2E_MT_SEM=3             # MikroTik API concurrency cap
export ADMIN_E2E_TEST_TG=880009001    # test user telegram id for user-mgmt flows
```

```bash
# Menu crawl only (~2–5 min)
pytest tests/live/test_admin_menu_crawl_live.py -m live_admin_e2e -v

# Full admin E2E suite (~20–40 min)
pytest -m live_admin_e2e -v --timeout=3600
```

Report: `reports/admin_panel_e2e_last.json` after orchestrator test (includes `rbac_access_matrix` for super / db_full / db_limited profiles).

## Admin RBAC (offline, no router)

Fast matrix tests for permission gates and menu filtering:

```bash
pytest tests/admin/test_admin_access_matrix.py tests/admin/test_admin_menu_crawl_offline.py -v
```

Covers: non-admin callback deny, DB admin without `admin_mgmt_menu`, limited preset menus, wildcard permissions.

## Admin RBAC (optional live)

If a **database admin** with limited permissions exists on production:

```bash
export ADMIN_E2E_DB_ADMIN_TG=<telegram_id>
pytest tests/live/test_admin_rbac_live.py -m live_admin_rbac -v
```

Super admins manage permissions from **Admin Management** in the panel (toggle rows + Full / Limited presets).

## Test server (reference)

| Setting | Value |
|---------|--------|
| Host | 81.30.108.27 |
| API | 8728 |
| API-SSL | 443 |
| Winbox | 8297 (manual only) |

## Load subset (parallel stress)

`tests/live/test_load_live_subset.py` runs ~17 tasks with `asyncio.Semaphore(5)`:

- 5× OVPN checkout (distinct users)
- 5× WG finalize
- 3× wallet deposit
- 1× receipt approve
- 1× admin extend (if a sub was created)

```bash
pytest tests/live/test_load_live_subset.py -m live_mt -v
```

Expect completion under **120 seconds**. See also [DATABASE_TESTING.md](DATABASE_TESTING.md) for mock 100-user load tests.

## Chaos concurrent (production-like)

`tests/live/test_live_chaos_concurrent.py` runs ~28 parallel operations with `CHAOS_MT_SEM=4` (default):

| Lane | Operations |
|------|------------|
| User | 6× OVPN checkout, 5× WG, 4× deposit, 2× race checkout |
| Admin | 3× approve receipt, extend, toggle, settings, balance |
| Maintenance | 2× sync, cleanup warn/delete, health check |

```bash
chmod +x scripts/run_live_chaos_suite.sh
./scripts/run_live_chaos_suite.sh
# or
pytest tests/live/test_live_chaos_concurrent.py -m "live_mt and chaos_live" -v
```

Report written to `reports/live_chaos_last.json`. Full cleanup of `INTG_*` / `u*` / `chaos_*` data after each test via `chaos_cleanup` fixture.

Optional env for heavy load:

```bash
export DB_POOL_SIZE=15 DB_MAX_OVERFLOW=25 CHAOS_MT_SEM=4
```

## Cleanup

Tests use `INTG_*` prefixes and `u*` usernames. Autouse fixtures delete matching User Manager users after the session.

## Production notes

- Set a stable `ENCRYPTION_KEY` in `.env` (required when `DEBUG=false`)
- Rotate MikroTik API password if exposed during testing
