
from sqlalchemy import select
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Server, Subscription
from vpn_bot.settings_utils import get_admin_setting, set_admin_setting
from vpn_bot.mikrotik_manager import get_mikrotik_manager
import asyncio

async def get_default_shared_users():
    """Get the global default shared users setting."""
    return await get_admin_setting('default_shared_users', 1)

async def set_default_shared_users(value: int):
    """Set the global default shared users setting."""
    await set_admin_setting('default_shared_users', value)

async def get_subscription_by_username(username: str):
    """Fetch subscription by its MikroTik username."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Subscription).where(Subscription.mikrotik_username == username))
        return res.scalars().first()

async def get_user_shared_count_from_mt(username: str, server_id: int):
    """Get the current shared users count from MikroTik for a specific user."""
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            return None
        
        mgr = get_mikrotik_manager(server)
        return await asyncio.to_thread(mgr.get_user_shared_users, username)

async def set_user_shared_count_on_mt(username: str, server_id: int, value: int):
    """Set the shared users count on MikroTik for a specific user."""
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            return False
        
        mgr = get_mikrotik_manager(server)
        return await asyncio.to_thread(mgr.set_user_shared_users, username, value)
