# Operations runbook

Day-2 operations for the VPN Telegram bot in production.

## Health monitoring

```bash
python3 scripts/health_check.py   # exit 0 = DB (+ optional MikroTik) OK
journalctl -u vpn_bot -f
```

Recommended cron (every 10 minutes):

```cron
*/10 * * * * cd /opt/vpn_bot && /opt/vpn_bot/venv/bin/python3 scripts/health_check.py || mail -s "vpn_bot unhealthy" ops@example.com
```

Alert on:

- `health_check.py` non-zero exit
- Log lines: `Cannot decrypt MikroTik password`, `Backup failed`, repeated `MikroTik renewal sync failed`

Optional: set `SENTRY_DSN` in `.env` for automatic exception capture.

## Restore from backup

1. Stop the bot: `systemctl stop vpn_bot`
2. Import the latest `.sql` or `.dump` from the backup group / host backup
3. Verify `ENCRYPTION_KEY` matches the key used when the backup was taken
4. Run `python3 scripts/fix_server_encryption.py` if server passwords fail decrypt
5. `systemctl start vpn_bot` and run `python3 scripts/health_check.py`

## Rotate ENCRYPTION_KEY

1. **Do not** change the key on a live DB without a migration plan
2. Export backup with the **old** key
3. Set new `ENCRYPTION_KEY`, run `scripts/fix_server_encryption.py` to re-encrypt MikroTik passwords in DB
4. Verify all servers connect from admin panel

## Incidents

### MikroTik unreachable

- Enable maintenance mode in admin → Bot config → Maintenance
- Check firewall from bot host: `telnet MIKROTIK_HOST 8728` (or 443 for API-SSL)
- Review `journalctl` for circuit-breaker cooldown messages

### PostgreSQL full / slow

- Run cleanup from admin → Clean DB (super-admin only for destructive actions)
- Increase disk or purge old transactions per retention policy
- Confirm indexes exist: `ix_transactions_user_created`, `ix_subscriptions_user_status`

### Receipt backlog

- Notify receipt admins via receipt group
- Target SLA: approve or reject within **24 hours**
- Use admin → Pending receipts; escalate if count grows for > 48h

## Multi-instance deployment

- Set shared `REDIS_URL` for rate limiting
- Single writer recommended for MikroTik provisioning; or partition servers per instance
- One Telegram bot token per instance only

## Commercial checklist

Before launch, complete [deploy/COMMERCIAL_CHECKLIST.md](../deploy/COMMERCIAL_CHECKLIST.md).
