import os
from pathlib import Path

from dotenv import load_dotenv

from vpn_bot._paths import PROJECT_ROOT

# Load environment variables from repository root (.env next to manage.sh)
load_dotenv(PROJECT_ROOT / ".env")

def _require_encryption_key():
    if os.getenv("ENCRYPTION_KEY", "").strip():
        return
    if os.getenv("DEBUG", "False").lower() == "true":
        return
    raise RuntimeError(
        "ENCRYPTION_KEY is required. Set it in .env or enable DEBUG=true for local dev only."
    )

_require_encryption_key()

class Config:
    # Telegram Bot
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    ADMIN_IDS = [int(id_str) for id_str in os.getenv("ADMIN_IDS", "").split(",") if id_str.strip()]
    PROXY_URL = os.getenv("PROXY_URL", "")
    
    # Database
    DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://vpnbot:vpnbot@localhost:5432/vpnbot")
    
    # MikroTik Defaults (Optional, specific servers will be in DB)
    MIKROTIK_HOST = os.getenv("MIKROTIK_HOST", "")
    MIKROTIK_USERNAME = os.getenv("MIKROTIK_USERNAME", "")
    MIKROTIK_PASSWORD = os.getenv("MIKROTIK_PASSWORD", "")
    MIKROTIK_PORT = int(os.getenv("MIKROTIK_PORT", 8728))
    # TLS certificate verification for MikroTik API (ports 443/8729). Default true in production.
    MIKROTIK_SSL_VERIFY = os.getenv("MIKROTIK_SSL_VERIFY", "true").lower() == "true"
    
    # App Settings
    DEBUG = os.getenv("DEBUG", "False").lower() == "true"

    # Optional error monitoring (Sentry)
    SENTRY_DSN = os.getenv("SENTRY_DSN", "").strip()
    
    # Backup Settings
    BACKUP_GROUP_ID = os.getenv("BACKUP_GROUP_ID", "")
    
config = Config()
