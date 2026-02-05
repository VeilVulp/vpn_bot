import asyncio
import shutil
import os
import logging
from datetime import datetime
from telegram import Bot
from config import config

logger = logging.getLogger("vpn_bot.backup")

class BackupManager:
    """Handles automated and manual database backups."""
    
    def __init__(self, bot: Bot = None):
        self.bot = bot
        self.db_path = "vpn_bot.db" # Standard path from database.py
        
    async def create_backup_file(self) -> str:
        """Creates a timestamped copy of the database."""
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        backup_filename = f"backup_{timestamp}.db"
        shutil.copy2(self.db_path, backup_filename)
        return backup_filename

    async def send_backup_to_telegram(self, chat_id: str, is_auto: bool = False):
        """Sends the database file to a specified Telegram chat."""
        if not chat_id:
            logger.warning("No BACKUP_GROUP_ID configured. Skipping backup.")
            return

        backup_file = None
        try:
            backup_file = await self.create_backup_file()
            caption = f"🛰️ **{'Automated' if is_auto else 'Manual'} Database Backup**\n📅 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
            
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
        """Task that runs every 6 hours to send backups."""
        logger.info("Starting periodic backup service (6h interval)...")
        while True:
            # Wait 6 hours
            await asyncio.sleep(6 * 3600)
            
            if config.BACKUP_GROUP_ID:
                await self.send_backup_to_telegram(config.BACKUP_GROUP_ID, is_auto=True)
            else:
                logger.warning("Continuous backup running but no BACKUP_GROUP_ID set.")

    @staticmethod
    async def restore_database(file_path: str) -> bool:
        """Replaces the current database with a new file."""
        try:
            # 1. Verification: Could check if it's a valid sqlite file
            # 2. Backup current before overwrite
            if os.path.exists("vpn_bot.db"):
                shutil.copy2("vpn_bot.db", "vpn_bot.db.bak")
                
            # 3. Overwrite
            shutil.copy2(file_path, "vpn_bot.db")
            logger.info("Database restored from uploaded file.")
            return True
        except Exception as e:
            logger.error(f"Restore failed: {e}")
            if os.path.exists("vpn_bot.db.bak"):
                shutil.copy2("vpn_bot.db.bak", "vpn_bot.db")
            return False
