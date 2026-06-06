# Commercial deployment checklist

Use this before going live with paying customers. See also [README-deploy.md](README-deploy.md) and [../docs/OPERATIONS.md](../docs/OPERATIONS.md).

## Staging validation (run before production)

On a staging server with production-like `.env`:

```bash
# 1. Automated env checks (read-only)
python3 scripts/validate_commercial_env.py

# 2. Health probe (DB + optional MikroTik)
python3 scripts/health_check.py

# 3. Offline test suites (same as CI)
pytest tests/security tests/db tests/admin -q --ignore=tests/live
pytest tests/test_wallet_receipt_atomic.py tests/test_discount_e2e.py -q

# 4. Smoke: start bot, verify /admin, receipt group, backup group, one test purchase
python3 -m vpn_bot   # Ctrl+C after menu loads
```

Record results and sign off each section below before promoting to production.

## Security

- [ ] `ENCRYPTION_KEY` set to a stable random value; stored in a password manager; **never** rotated without running `scripts/fix_server_encryption.py`
- [ ] `ADMIN_IDS` limited to trusted super-admins only
- [ ] `admin_secret_keyword` set in DB (auto-generated on first boot if empty)
- [ ] `MIKROTIK_SSL_VERIFY=true` when using API over TLS (ports 443/8729)
- [ ] Firewall: only the bot server may reach MikroTik API ports
- [ ] `REDIS_URL` configured when running **more than one** bot instance (distributed rate limiting)

## Database & backups

- [ ] PostgreSQL with daily **host-level** backup (pg_dump / managed backup)
- [ ] Bot Telegram backup group configured (`BACKUP_GROUP_ID`) and tested restore
- [ ] Restore procedure documented and tested once on staging

## Runtime

- [ ] `systemd` unit enabled (`Restart=always`) — see `vpn_bot.service.template`
- [ ] Cron or monitoring runs `python3 scripts/health_check.py` every 5–15 minutes; alert on non-zero exit
- [ ] Optional: `SENTRY_DSN` for unhandled exception alerting
- [ ] `DEBUG=false` in production `.env`

## Operations

- [ ] Receipt approval SLA agreed (suggested: < 24h)
- [ ] On-call contact for MikroTik / DB incidents
- [ ] Sales capacity limits reviewed (`sales_*_limit`)

## Validation commands

```bash
python3 scripts/validate_commercial_env.py
pytest tests/security tests/db -q --ignore=tests/live
pytest tests/admin -q --ignore=tests/live
ruff check vpn_bot/
bandit -r vpn_bot/ -ll
python3 scripts/health_check.py
```
