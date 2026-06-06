
import asyncio
from datetime import timedelta
from sqlalchemy import select
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Subscription, Server
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.utils import logger, LanguageManager, format_datetime

async def get_subscription_by_username(username: str, session=None):
    """Fetch OVPN subscription by MikroTik username."""
    if session is not None:
        stmt = select(Subscription).where(Subscription.mikrotik_username == username)
        return (await session.execute(stmt)).scalars().first()

    async with AsyncSessionLocal() as session_internal:
        stmt = select(Subscription).where(Subscription.mikrotik_username == username)
        return (await session_internal.execute(stmt)).scalars().first()

async def extend_subscription_validity(username: str, days: int):
    """Extend the expiry date of a subscription in DB and MikroTik."""
    async with AsyncSessionLocal() as session:
        sub = await get_subscription_by_username(username, session=session)
        if not sub:
            return False, "Subscription not found"

        server = await session.get(Server, sub.server_id)
        if server:
            mgr = get_mikrotik_manager(server)
            mt_ok = await asyncio.to_thread(mgr.extend_validity, username, days)
            if not mt_ok:
                return False, "MikroTik extend_validity failed"

        sub.expiry_date += timedelta(days=days)
        await session.commit()
        return True, sub.expiry_date

async def reset_subscription_password(username: str, new_password: str):
    """Update password in DB and MikroTik."""
    async with AsyncSessionLocal() as session:
        sub = await get_subscription_by_username(username, session=session)
        if not sub:
            return False

        server = await session.get(Server, sub.server_id)
        if server:
            mgr = get_mikrotik_manager(server)
            mt_ok = await asyncio.to_thread(mgr.reset_password, username, new_password)
            if not mt_ok:
                return False

        sub.mikrotik_password = new_password
        await session.commit()
        return True

async def add_subscription_data(username: str, gb: float):
    """Increase data limit in DB and MikroTik."""
    async with AsyncSessionLocal() as session:
        sub = await get_subscription_by_username(username, session=session)
        if not sub:
            return False

        additional_gb = int(gb)
        bytes_to_add = int(gb * (1024**3))
        server = await session.get(Server, sub.server_id)
        if server:
            mgr = get_mikrotik_manager(server)
            mt_ok = await asyncio.to_thread(mgr.add_data_to_user, username, additional_gb)
            if not mt_ok:
                return False

        sub.total_limit_bytes += bytes_to_add
        await session.commit()
        return True

async def toggle_subscription_status(username: str):
    """Enable or disable a subscription on MikroTik."""
    async with AsyncSessionLocal() as session:
        sub = await get_subscription_by_username(username, session=session)
        if not sub:
            return False, None

        server = await session.get(Server, sub.server_id)
        if not server:
            return False, None

        mgr = get_mikrotik_manager(server)
        mt_info = await asyncio.to_thread(mgr.get_user_info, username)

        if mt_info and mt_info.get('status') == 'active':
            success = await asyncio.to_thread(mgr.disable_user, username)
            new_status = 'disabled'
        else:
            success = await asyncio.to_thread(mgr.enable_user, username)
            new_status = 'enabled'

        return success, new_status

async def get_subscription_comprehensive_info(username: str):
    """Fetch all details for a subscription from DB and MikroTik."""
    async with AsyncSessionLocal() as session:
        sub = await get_subscription_by_username(username, session=session)
        if not sub:
            return None, None

        server = await session.get(Server, sub.server_id)
        if not server:
            return sub, None

        try:
            mgr = get_mikrotik_manager(server)
            mt_info = await asyncio.to_thread(mgr.get_user_info, username)
            return sub, mt_info
        except Exception as e:
            logger.error(f"Failed to fetch MT info for {username}: {e}")
            return sub, None

async def delete_ovpn_subscription(username: str) -> bool:
    """Remove a single OVPN/L2TP subscription from MikroTik and DB."""
    from sqlalchemy import delete as sql_delete

    async with AsyncSessionLocal() as session:
        sub = await get_subscription_by_username(username, session=session)
        if not sub:
            return False

        server = await session.get(Server, sub.server_id)
        if server:
            try:
                mgr = get_mikrotik_manager(server)
                await asyncio.to_thread(mgr.connect)
                await asyncio.to_thread(mgr.delete_user, username)
                await asyncio.to_thread(mgr.close)
            except Exception as e:
                logger.warning(f"delete_ovpn_subscription MT cleanup failed for {username}: {e}")

        await session.execute(
            sql_delete(Subscription).where(Subscription.id == sub.id)
        )
        await session.commit()
        return True


async def format_subscription_info_text(sub, mt_info):
    """Format subscription details for admin display."""
    from vpn_bot.utils import escape_markdown

    formatted_expiry = await format_datetime(sub.expiry_date, include_time=False)
    db_expiry = formatted_expiry if sub.expiry_date else LanguageManager.get('common.na')

    status_active = LanguageManager.get('status.active')
    status_disabled = LanguageManager.get('status.disabled')
    mt_active = status_active if mt_info and mt_info.get('status') == 'active' else status_disabled

    total_gb = sub.total_limit_bytes / (1024**3) if sub.total_limit_bytes else 0
    used_gb = mt_info.get('used_bytes', 0) / (1024**3) if mt_info else 0
    usage_percent = (used_gb / total_gb * 100) if total_gb > 0 else 0
    progress_bar = "█" * int(usage_percent / 10) + "░" * (10 - int(usage_percent / 10))

    text = LanguageManager.get('admin.user.info_title',
        username=escape_markdown(sub.mikrotik_username, version=1),
        password=escape_markdown(sub.mikrotik_password, version=1),
        status=mt_active,
        used=f"{used_gb:.2f}", total=f"{total_gb:.0f}", pct=f"{usage_percent:.1f}",
        bar=progress_bar, expiry=db_expiry,
        active=mt_info.get('connected_devices', 0) if mt_info else '?',
        ip=mt_info.get('current_ip', LanguageManager.get('common.na')) if mt_info else LanguageManager.get('common.na')
    )
    return text
