# Production deployment (Linux)

## Required files for FTP upload

Upload the **repository root** (same layout as GitHub), including at minimum:

| Path | Required |
|------|----------|
| `vpn_bot/` | Yes — application package |
| `locales/` | Yes — `en.json`, `fa.json` |
| `manage.sh`, `install.sh` | Yes |
| `requirements.txt`, `pyproject.toml` | Yes |
| `.env.example` | Recommended (copied on bootstrap if no `.env`) |

Optional on server: `tests/`, `docs/`, `scripts/` (not needed to run the bot).

Do **not** rely on `.env` in git — create it on the server via `sudo vpnbot` or copy from `.env.example`.

## Install from uploaded folder (FTP)

```bash
cd /home/youruser/vpn_bot    # your upload path
sudo bash install.sh
sudo vpnbot                   # configure BOT_TOKEN, admins, DATABASE_URL
```

`install.sh` detects a local project (no git clone) and writes `/etc/vpnbot/install.conf`.

## Install from GitHub

```bash
sudo bash install.sh
# or custom path:
sudo bash install.sh /srv/vpn_bot
```

## Service (auto-start on reboot)

```bash
systemctl status vpn_bot
systemctl restart vpn_bot
journalctl -u vpn_bot -f
```

The unit uses `python3 -m vpn_bot` with `Restart=always` and `systemctl enable`.

## Environment

See `.env.example` and `docs/PERFORMANCE.md` for pool tuning (`DB_POOL_SIZE`, `MIKROTIK_MAX_CONCURRENT`).

**Production requirements:**

- `REDIS_URL` — **required** when running more than one bot instance (distributed rate limiting)
- `MIKROTIK_SSL_VERIFY=true` — verify TLS when using MikroTik API over ports 443/8729
- `ENCRYPTION_KEY` — stable value; never rotate without `scripts/fix_server_encryption.py`

See [COMMERCIAL_CHECKLIST.md](COMMERCIAL_CHECKLIST.md) before going live.

```bash
python3 scripts/health_check.py   # exit 0 = healthy (use in cron)
```
