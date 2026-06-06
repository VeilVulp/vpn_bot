from sqlalchemy import select, delete
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Admin
from vpn_bot.config import config
import logging
import json
import secrets
from telegram.ext import filters

from vpn_bot.admin_permissions import (
    ALL_PERMISSION_KEYS,
    LIMITED_PERMISSION_PRESET,
    set_admin_permissions as _persist_permissions,
)
from vpn_bot.utils import rate_limit

logger = logging.getLogger("vpn_bot.admin_mgmt")

class AdminKeywordFilter(filters.MessageFilter):
    """Filter that matches text messages for the secret keyword listener."""
    def filter(self, message):
        return bool(message.text and not message.text.startswith('/'))


async def is_user_admin(telegram_id: int) -> bool:
    """Checks if a user is an admin (either in .env or in DB)."""
    if telegram_id in config.ADMIN_IDS:
        return True
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Admin).where(Admin.telegram_id == telegram_id))
        return res.scalars().first() is not None

async def is_super_admin(telegram_id: int) -> bool:
    """Only Super Admins (from .env) can manage other admins."""
    return telegram_id in config.ADMIN_IDS

async def get_all_admin_telegram_ids() -> list[int]:
    from vpn_bot.admin_permissions import get_all_admin_telegram_ids as _all
    return await _all()

async def get_admins_for_permission(perm: str) -> list[int]:
    from vpn_bot.admin_permissions import get_admins_for_permission as _for_perm
    return await _for_perm(perm)

async def has_admin_perm(telegram_id: int, perm: str) -> bool:
    from vpn_bot.admin_permissions import has_admin_perm as _has
    return await _has(telegram_id, perm)

async def get_admin_permissions(telegram_id: int) -> set[str]:
    from vpn_bot.admin_permissions import get_admin_permissions as _get
    return await _get(telegram_id)

async def set_admin_permissions(telegram_id: int, perms: set[str]) -> bool:
    return await _persist_permissions(telegram_id, perms)

async def add_admin(
    telegram_id: int,
    username: str = None,
    added_by: int = None,
    *,
    permissions: set[str] | None = None,
) -> bool:
    """Adds a new admin to the database."""
    try:
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Admin).where(Admin.telegram_id == telegram_id))
            if res.scalars().first():
                return False

            perms = permissions if permissions is not None else set(ALL_PERMISSION_KEYS)
            new_admin = Admin(
                telegram_id=telegram_id,
                username=username,
                added_by=added_by,
                permissions_json=json.dumps(sorted(p for p in perms if p in ALL_PERMISSION_KEYS)),
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

def limited_permission_preset() -> set[str]:
    return set(LIMITED_PERMISSION_PRESET)

def full_permission_preset() -> set[str]:
    return set(ALL_PERMISSION_KEYS)

async def ensure_admin_secret_keyword() -> str | None:
    """Generate and persist a random admin panel keyword when unset."""
    from vpn_bot.admin_settings import get_admin_setting, set_admin_setting

    current = await get_admin_setting('admin_secret_keyword', '')
    if current and str(current).strip():
        return None
    keyword = secrets.token_urlsafe(12)[:16]
    await set_admin_setting('admin_secret_keyword', keyword)
    logger.info("Generated admin_secret_keyword on first boot (retrieve from admin settings in DB)")
    return keyword


@rate_limit(seconds=5)
async def secret_keyword_listener(update, context):
    """Listens for a secret keyword to open the admin panel."""
    if not update.message or not update.message.text:
        return

    from vpn_bot.admin_settings import get_admin_setting
    secret_keyword = await get_admin_setting('admin_secret_keyword', '')

    if update.message.text.strip() == secret_keyword:
        if await is_user_admin(update.effective_user.id):
            from vpn_bot.admin_panel import admin_start
            return await admin_start(update, context)
