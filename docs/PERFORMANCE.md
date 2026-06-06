# Performance and load

## Goals

Under concurrent Telegram traffic the bot should:

- Avoid holding PostgreSQL pool connections during MikroTik API calls
- Limit parallel router API usage
- Not query `system_language` on every update

Regression targets (mock DB + mock MikroTik):

- `tests/load/test_concurrent_100.py`: QueuePool errors ≤ 15%, p95 latency &lt; 60s (10 concurrent ops)
- `tests/load/test_pool_saturation.py`: pool survives 40 parallel pings

## Environment variables

| Variable | Default | Purpose |
|----------|---------|---------|
| `DB_POOL_SIZE` | 10 | SQLAlchemy pool size |
| `DB_MAX_OVERFLOW` | 20 | Extra connections under burst |
| `DB_POOL_TIMEOUT` | 30 | Seconds to wait for a connection |
| `MIKROTIK_MAX_CONCURRENT` | 10 | Semaphore for `run_mikrotik()` |

Production suggestion for ~100 mixed concurrent operations: `DB_POOL_SIZE=15`, `DB_MAX_OVERFLOW=30`, `MIKROTIK_MAX_CONCURRENT=10`.

## Architecture notes

- **Language**: `LanguageManager.refresh_language()` uses a 60s TTL; admin language change calls `invalidate_language_cache()` + `force=True`.
- **Settings**: `get_admin_setting` / `get_currency_unit` use TTL cache (`settings_utils`).
- **Purchases**: OVPN checkout commits DB state, releases the session, then calls MikroTik; rollback refunds on failure.
- **Sync**: OVPN disables are queued during DB reconcile, applied via `run_mikrotik` after in-memory updates; full sync no longer holds `db_maintenance_lock` for the entire run (only last-sync timestamp write).
- **MikroTik**: `run_mikrotik()` wraps `asyncio.to_thread` with a global semaphore.

## Running load tests

```bash
pytest tests/load -m load -q
pytest tests/admin/test_admin_conversation_reset.py -q
./scripts/run_traffic_policy_tests.sh
```

Live MikroTik subset (optional):

```bash
pytest tests/live/test_load_live_subset.py -m live
```

## Ops

- Restart the bot after changing pool or concurrency env vars.
- Purge pytest mock servers once: `python3 scripts/purge_dbtest_servers.py` (requires `ENCRYPTION_KEY` in `.env`).
- Manual admin QA: [ADMIN_PANEL_QA.md](ADMIN_PANEL_QA.md)
