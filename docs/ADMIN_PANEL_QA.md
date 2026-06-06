# Admin Panel QA Checklist

Manual checks after conversation-handler or server-list changes.

## Conversation reset

For each admin flow below: start the flow → send `/admin` or tap **Back** (`admin_start`) or `/cancel` → you must land on the **main admin menu**, and the next plain-text message must **not** be handled by the previous step (e.g. not treated as a user search).

| Entry point | Callback / command |
|-------------|-------------------|
| User search | `search_user` |
| Server add | `server_add` |
| Server list → manage | `list_servers` |
| Sales management | `sales_mgmt_menu` |
| Sales capacity / renew submenus | `sales_capacity_menu`, `renew_mgmt_menu` |
| Bot settings | `bot_config_menu` |
| Support tickets (admin) | `admin_tickets` |
| Notifications | `notify_broadcast`, `notify_targeted` |
| WireGuard search | `search_wg_start` |

## User search

1. **Empty input** → warning + back button; still in search state until back or `/admin`.
2. **Unknown query** → not-found message + back button.
3. **Valid user** → profile + back button.
4. After `/admin`, search again from menu — no stale `target_user` in context.

## Server list

1. Opens in **under ~5 seconds** on production DB (no hundreds of MikroTik connects to `127.0.0.1`).
2. Shows only **active, non-mock** servers (`[DBTEST]` and `127.0.0.1` mocks excluded).
3. Logs should not flood `Health check failed for [DBTEST]`.
4. If many servers exist, list text is **truncated** with a summary line.

## DB cleanup (optional)

```bash
python3 scripts/purge_dbtest_servers.py
```

Requires `ENCRYPTION_KEY` in `.env`.

## Automated tests

```bash
pytest tests/admin/test_admin_conversation_reset.py -q
./scripts/run_traffic_policy_tests.sh
```
