# Database Testing & Debug Guide

This document describes invariants, known vulnerability findings (V1–V10), expiry test matrix, and how to run the DB/load test suite.

## Quick start

```bash
source .venv/bin/activate
export $(grep -v '^#' .env.test | xargs)  # or .env with test DATABASE_URL

# DB + mock MikroTik load (no router)
./scripts/run_db_debug_suite.sh

# Live load subset (10–20 tasks, real router)
pytest tests/live/test_load_live_subset.py -m live_mt -v

# Live chaos (admin + user + sync/cleanup on real MikroTik)
./scripts/run_live_chaos_suite.sh
```

## Architecture (read/write)

```mermaid
flowchart LR
    handlers[bot_handler admin_panel] --> services[user_features wallet_manager admin_receipt_service]
    services --> pool[AsyncSessionLocal pool 10+20]
    pool --> pg[(PostgreSQL)]
    services --> mt[MikroTik asyncio.to_thread]
```

| Path | Session | Row lock on wallet |
|------|---------|-------------------|
| `checkout_subscription` | Single until commit | `SELECT FOR UPDATE` (after fix) |
| `finalize_wg_purchase` | Single | Via `WalletManager.deduct` + `FOR UPDATE` on WG interface |
| `WalletManager.deposit/deduct` | Own or shared | `FOR UPDATE` on User |
| `approve_payment_receipt` | Delegates to `WalletManager.approve_receipt` | `FOR UPDATE` on receipt + user |
| `SyncManager.reconcile_*` | Per-server session | No |
| `AdminCleanup` | Per operation | No (`db_maintenance_lock` only for auto-warn) |

## Invariants (checked after tests)

- `users.wallet_balance >= 0` unless explicit admin manual_adjustment
- Each `payment_receipt` transitions `pending` → `approved` at most once
- `subscriptions.mikrotik_username` unique; failed purchase rolls back DB row
- After sync with UTC-aware `now`: no `status=active` with `expiry_date` in the past
- `wireguard_interfaces.current_users <= max_users` after concurrent WG purchases
- `count(active+pending WG subs) <= max_users` per interface (live active gate)
- No duplicate `assigned_ip` on the same WG interface

## Vulnerability catalog

| ID | Issue | Severity | Test module | Status |
|----|--------|----------|-------------|--------|
| V1 | `safety_margin` + partial `min(price, balance)` deduct | High | `test_abuse_scenarios.py` | **Fixed** — full `p_price` deduct required |
| V2 | Wallet race without `FOR UPDATE` | High | `test_purchase_races.py`, `test_concurrent_100.py` | **Fixed** — `SELECT FOR UPDATE` on `User` |
| V3 | Dual receipt approve paths / txn types | Medium | `test_wallet_invariants.py` | **Fixed** — `approve_payment_receipt` → `WalletManager` |
| V4 | Concurrent double approve | High | `test_receipt_races.py` | **Fixed** — row locks on receipt + user |
| V5 | Naive vs timezone-aware expiry compare | High | `test_expiry_matrix.py` | **Fixed** — `utc_now()` + normalized compare in sync |
| V6 | Sync vs backup without maintenance lock | Medium | `test_live_chaos_concurrent.py` | **Mitigated** — `sync_all_servers` uses `db_maintenance_lock` |
| V7 | Admin `update_user_balance` overwrite | Medium | `test_input_validation.py` | Auth-bound |
| V8 | No DB CHECK on `wallet_balance >= 0` | Medium | Documented | Open |
| V9 | MT orphan on partial failure | Medium | `test_live_chaos_concurrent.py` | Rollback + `chaos_cleanup` / UM delete |
| V10 | WG interface lock scope | Low | `test_concurrent_100.py` | **Fixed** — server-level `FOR UPDATE` + live active count gate |
| V11 | WG `current_users` never decremented on expiry/delete | Medium | `test_wg_capacity_concurrent.py` | **Fixed** — active/pending count gate + `sync_wg_interface_current_users` |

## Expiry matrix (E1–E8)

| ID | Scenario | Module |
|----|----------|--------|
| E1 | Expired OVPN sub → sync sets `expired`, disables UM user | `test_expiry_matrix.py` |
| E2 | Aware UTC expiry vs naive `now` comparison | `test_expiry_matrix.py` |
| E3 | Cleanup warn sets `deletion_warning_sent_at` (with fake bot) | `test_expiry_matrix.py` |
| E4 | Cleanup delete after 24h warning | `test_expiry_matrix.py` |
| E5 | Admin extend validity | `test_expiry_matrix.py` |
| E6 | User renewal flow (if balance sufficient) | `test_expiry_matrix.py` |
| E7 | Expired WG peer → reconcile disables | `test_expiry_matrix.py` |
| E8 | Expired sub not re-enabled as active on sync | `test_expiry_matrix.py` |

## Load test (100 users, mock MT)

Distribution in `tests/load/test_concurrent_100.py`:

- 40× checkout (distinct users)
- 20× paired race checkout (10 users × 2)
- 15× deposit
- 15× approve receipt
- 10× WG finalize

Acceptance thresholds:

- Zero negative balances (without admin adjust)
- At most one successful approve per receipt
- Pool timeouts ≤ 5% of operations

Metrics JSON: `scripts/load_report.py` (or pytest `--json-report` if installed).

## WG capacity concurrent (V11)

`tests/db/test_wg_capacity_concurrent.py`:

- 5 parallel purchases on `max_users=3` interface
- Parallel renew + `add_wg_subscription_data` + reconcile
- Expiry frees slots; new purchases reuse same interface
- Cleanup delete removes expired rows; no `active > max_users`

```bash
pytest tests/db/test_wg_capacity_concurrent.py -m db -v
```

## Backup roundtrip (B1–B6)

Full `pg_dump` / `pg_restore` on an **isolated** database (`vpnbot_backup_test`). Requires PostgreSQL client tools and explicit ack.

```bash
# Once (superuser): ./scripts/ensure_backup_test_db.sh
# Or: createdb -O vpnbot vpnbot_backup_test
export BACKUP_TEST_DATABASE_URL=postgresql+asyncpg://vpnbot:vpnbot@localhost:5432/vpnbot_backup_test
export ALLOW_DESTRUCTIVE_BACKUP_TEST=1
pytest tests/db/test_backup_restore_roundtrip.py -m db_backup -v
```

| ID | Scenario | Module |
|----|----------|--------|
| B1 | `pg_dump` creates valid custom-format file | `test_backup_restore_roundtrip.py` |
| B2 | Seed all tables → dump → mutate → restore → snapshot match | same |
| B3 | Restore removes junk rows / reverts wallet zeroing | same |
| B4 | Fernet-encrypted server password survives roundtrip | same |
| B5 | Wallet balances + transactions survive | same |
| B6 | `admin_settings.value_json` survives | same |

Admin handler smoke (mocked, no real restore): `tests/admin/test_backup_admin_handlers.py`

Or via suite runner:

```bash
RUN_BACKUP_ROUNDTRIP=1 ./scripts/run_db_debug_suite.sh
```

## Abuse scenarios (A1–A7)

See `tests/db/test_abuse_scenarios.py` and `tests/db/test_input_validation.py`.

## White-box security suite

Offline handler/service security tests (RBAC, IDOR, wallet, purchase gates, banned users):

```bash
pytest tests/security tests/admin/test_group_security.py -m security -v
```

Included in `./scripts/run_db_debug_suite.sh` when using `-m "db or security"`.

## Files

| Path | Purpose |
|------|---------|
| `tests/db/` | Unit/integration DB tests (mock MT) |
| `tests/load/` | Concurrency & pool saturation |
| `tests/helpers/mock_mikrotik.py` | In-memory RouterOS stub |
| `tests/db/helpers/invariant_checker.py` | Post-condition scans |
| `tests/db/helpers/backup_roundtrip.py` | Seed/snapshot/mutate for backup tests |
| `tests/db/test_backup_restore_roundtrip.py` | pg_dump/pg_restore roundtrip (B1–B6) |
| `scripts/run_db_debug_suite.sh` | Runner script |
| `scripts/run_live_chaos_suite.sh` | Live chaos on real MikroTik |
| `tests/live/test_live_chaos_concurrent.py` | Concurrent admin/user/maintenance |
| `tests/live/helpers/chaos_report.py` | Metrics JSON for chaos runs |

## Related

- [TESTING_LIVE_MT.md](TESTING_LIVE_MT.md) — live router tests, load subset, and chaos suite
