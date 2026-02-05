from sqlalchemy import select, delete
from database import AsyncSessionLocal
from models import Admin
from config import config
import logging

logger = logging.getLogger("vpn_bot.admin_mgmt")

async def is_user_admin(telegram_id: int) -> bool:
    """Checks if a user is an admin (either in .env or in DB)."""
    # 1. Check Super Admins from .env
    if telegram_id in config.ADMIN_IDS:
        return True
    
    # 2. Check DB Admins
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Admin).where(Admin.telegram_id == telegram_id))
        return res.scalars().first() is not None

async def is_super_admin(telegram_id: int) -> bool:
    """Only Super Admins (from .env) can manage other admins."""
    return telegram_id in config.ADMIN_IDS

async def add_admin(telegram_id: int, username: str = None, added_by: int = None) -> bool:
    """Adds a new admin to the database."""
    try:
        async with AsyncSessionLocal() as session:
            # Check if already exists
            res = await session.execute(select(Admin).where(Admin.telegram_id == telegram_id))
            if res.scalars().first():
                return False
            
            new_admin = Admin(
                telegram_id=telegram_id,
                username=username,
                added_by=added_by
            )
            session.add(new_admin)
            await session.commit()
            return True
    except Exception as e:
        logger.error(f"Error adding admin: {e}")
        return False

async def remove_admin(telegram_id: int) -> bool:
    """Removes an admin from the database."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(delete(Admin).where(Admin.telegram_id == telegram_id))
            await session.commit()
            return True
    except Exception as e:
        logger.error(f"Error removing admin: {e}")
        return False

async def list_admins():
    """Returns a list of all DB admins."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Admin))
        return res.scalars().all()

async def secret_keyword_listener(update, context):
    """Listens for a secret keyword to open the admin panel."""
    if not update.message or not update.message.text:
        return
    
    from admin_settings import get_admin_setting
    secret_keyword = await get_admin_setting('admin_secret_keyword', 'AdminPanel')
    
    if update.message.text.strip() == secret_keyword:
        if await is_user_admin(update.effective_user.id):
            from admin_panel import admin_start
            return await admin_start(update, context)
