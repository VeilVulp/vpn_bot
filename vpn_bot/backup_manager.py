import asyncio
import os
import shutil
import logging
from datetime import datetime
from telegram import Bot
from vpn_bot.config import config
from urllib.parse import urlparse
from vpn_bot.utils import db_maintenance_lock

logger = logging.getLogger("vpn_bot.backup")


def _find_pg_tool(name: str) -> str:
    """Locate a PostgreSQL CLI tool (pg_dump / pg_restore) on disk.

    1. Try ``shutil.which`` (honours $PATH).
    2. Fall back to well-known Homebrew & Linux locations.
    3. Return the bare name as last resort (will raise at exec time if missing).
    """
    found = shutil.which(name)
    if found:
        return found

    # Common installation paths (Homebrew on macOS + Linux package managers)
    search_dirs = [
        # Homebrew opt symlinks (version-agnostic)
        "/opt/homebrew/opt/libpq/bin",
        "/opt/homebrew/opt/postgresql@14/bin",
        "/opt/homebrew/opt/postgresql@15/bin",
        "/opt/homebrew/opt/postgresql@16/bin",
        "/opt/homebrew/opt/postgresql@17/bin",
        "/opt/homebrew/bin",
        "/usr/local/opt/libpq/bin",
        "/usr/local/opt/postgresql@14/bin",
        "/usr/local/bin",
        "/usr/bin",
        # Linux (apt/yum installed)
        "/usr/lib/postgresql/14/bin",
        "/usr/lib/postgresql/15/bin",
        "/usr/lib/postgresql/16/bin",
    ]

    # Also scan Homebrew Cellar for libpq (version-specific)
    import glob
    for pattern in [
        "/opt/homebrew/Cellar/libpq/*/bin",
        "/opt/homebrew/Cellar/postgresql@*/*/bin",
        "/usr/local/Cellar/libpq/*/bin",
    ]:
        search_dirs.extend(glob.glob(pattern))
    for d in search_dirs:
        candidate = os.path.join(d, name)
        if os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            logger.info(f"Found {name} at {candidate}")
            return candidate

    logger.warning(f"{name} not found in PATH or common locations; using bare name")
    return name


def _parse_pg_url(url: str) -> dict:
    """Extract PostgreSQL connection params from DATABASE_URL."""
    # Handle both asyncpg and psycopg2 URL schemes
    clean_url = url.replace("postgresql+asyncpg://", "postgresql://") \
                    .replace("postgresql+psycopg2://", "postgresql://")
    parsed = urlparse(clean_url)
    return {
        "host": parsed.hostname or "localhost",
        "port": parsed.port or 5432,
        "user": parsed.username or "vpnbot",
        "password": parsed.password or "",
        "dbname": parsed.path.lstrip("/") or "vpnbot",
    }


class BackupManager:
    """Handles automated and manual PostgreSQL database backups."""
    
    def __init__(self, bot: Bot = None):
        self.bot = bot
        self.db_params = _parse_pg_url(config.DATABASE_URL)
        
    async def create_backup_file(self) -> str:
        """Creates a timestamped pg_dump of the database."""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_filename = f"backup_{timestamp}.sql"
        
        env = os.environ.copy()
        env["PGPASSWORD"] = self.db_params["password"]
        
        cmd = [
            _find_pg_tool("pg_dump"),
            "-h", self.db_params["host"],
            "-p", str(self.db_params["port"]),
            "-U", self.db_params["user"],
            "-d", self.db_params["dbname"],
            "-F", "c",  # Custom format (compressed)
            "-f", backup_filename,
        ]
        
        async with db_maintenance_lock:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                raise RuntimeError(f"pg_dump failed: {stderr.decode()}")
            
        return backup_filename

    async def send_backup_to_telegram(self, chat_id: str, is_auto: bool = False):
        """Sends the database dump to a specified Telegram chat."""
        if not chat_id:
            logger.warning("No BACKUP_GROUP_ID configured. Skipping backup.")
            return

        backup_file = None
        try:
            backup_file = await self.create_backup_file()
            now = datetime.now()
            date_str = now.strftime('%Y-%m-%d')
            time_str = now.strftime('%H:%M:%S')
            
            caption = (
                f"🛰️ **{'Automated' if is_auto else 'Manual'} Database Backup**\n\n"
                f"📅 **Date:** `{date_str}`\n"
                f"⏰ **Time:** `{time_str}`"
            )
            
            with open(backup_file, 'rb') as db:
                await self.bot.send_document(
                    chat_id=chat_id,
                    document=db,
                    filename=backup_file,
                    caption=caption,
                    parse_mode='Markdown'
                )
            logger.info(f"Backup sent successfully to {chat_id}")
        except Exception as e:
            logger.error(f"Failed to send backup: {e}")
        finally:
            if backup_file and os.path.exists(backup_file):
                os.remove(backup_file)

    async def run_periodic_backup(self):
        """Task that runs periodically based on admin settings to send backups."""
        from vpn_bot.settings_utils import get_admin_setting
        from vpn_bot.utils import parse_duration_to_seconds
        
        logger.info("Starting periodic backup service...")
        while True:
            try:
                # Fetch interval from DB, default to 6h
                interval_str = await get_admin_setting('backup_interval_hours', '6h')
                interval_seconds = parse_duration_to_seconds(interval_str)
                
                # Failsafe: if less than 1 minute or error parsing, enforce 6 hours
                if interval_seconds < 60:
                    interval_seconds = 6 * 3600
                
                await asyncio.sleep(interval_seconds)
                
                if config.BACKUP_GROUP_ID:
                    await self.send_backup_to_telegram(config.BACKUP_GROUP_ID, is_auto=True)
                else:
                    logger.warning("Continuous backup running but no BACKUP_GROUP_ID set.")
            except Exception as e:
                logger.error(f"Error in periodic backup loop: {e}")
                await asyncio.sleep(3600)  # Sleep an hour before retrying on failure

    @staticmethod
    async def restore_database(file_path: str) -> bool:
        """Restores the database from a pg_dump file."""
        try:
            db_params = _parse_pg_url(config.DATABASE_URL)
            
            env = os.environ.copy()
            env["PGPASSWORD"] = db_params["password"]
            
            cmd = [
                _find_pg_tool("pg_restore"),
                "-h", db_params["host"],
                "-p", str(db_params["port"]),
                "-U", db_params["user"],
                "-d", db_params["dbname"],
                "--clean",        # Drop existing objects before restoring
                "--if-exists",    # Don't error if objects don't exist
                file_path,
            ]
            
            async with db_maintenance_lock:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                _, stderr = await proc.communicate()
            
            if proc.returncode != 0:
                err_msg = stderr.decode()
                # pg_restore may return warnings that aren't fatal
                if "ERROR" in err_msg:
                    raise RuntimeError(f"pg_restore failed: {err_msg}")
                logger.warning(f"pg_restore completed with warnings: {err_msg}")
            
            logger.info("Database restored from uploaded file.")
            return True
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            return False
