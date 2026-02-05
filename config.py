import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    # Telegram Bot
    BOT_TOKEN = os.getenv("BOT_TOKEN", "")
    ADMIN_IDS = [int(id_str) for id_str in os.getenv("ADMIN_IDS", "").split(",") if id_str.strip()]
    
    # Database
    DATABASE_URL = os.getenv("DATABASE_URL", "sqlite+aiosqlite:///vpn_bot.db")
    
    # MikroTik Defaults (Optional, specific servers will be in DB)
    MIKROTIK_HOST = os.getenv("MIKROTIK_HOST", "")
    MIKROTIK_USERNAME = os.getenv("MIKROTIK_USERNAME", "")
    MIKROTIK_PASSWORD = os.getenv("MIKROTIK_PASSWORD", "")
    MIKROTIK_PORT = int(os.getenv("MIKROTIK_PORT", 8728))
    
    # App Settings
    DEBUG = os.getenv("DEBUG", "False").lower() == "true"
    
    # Backup Settings
    BACKUP_GROUP_ID = os.getenv("BACKUP_GROUP_ID", "")
    
config = Config()
