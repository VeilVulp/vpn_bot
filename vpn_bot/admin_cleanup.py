import logging
import asyncio
from vpn_bot.admin_cleanup_service import (
    get_cleanup_thresholds, get_db_health_stats, 
    clean_pending_receipts_service, clean_old_transactions_service,
    clean_closed_tickets_service, clean_inactive_users_service
)
from sqlalchemy import select

from vpn_bot.settings_utils import get_admin_setting
from vpn_bot.utils import LanguageManager, db_maintenance_lock
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Server, Subscription, WireGuardSubscription, WireGuardInterface
from vpn_bot.mikrotik_manager import get_mikrotik_manager

logger = logging.getLogger(__name__)

class AdminCleanup:
    @staticmethod
    async def get_thresholds():
        return await get_cleanup_thresholds()

    @staticmethod
    async def get_db_health():
        return await get_db_health_stats()

    @staticmethod
    async def clean_pending_receipts(days=5):
        return await clean_pending_receipts_service(days)

    @staticmethod
    async def clean_old_transactions(months=6):
        return await clean_old_transactions_service(months)

    @staticmethod
    async def clean_closed_tickets(days=7):
        return await clean_closed_tickets_service(days)

    @staticmethod
    async def clean_inactive_users(days=30):
        return await clean_inactive_users_service(days)

    @staticmethod
    async def clean_expired_subscriptions(bot=None, mode='delete', seconds=259200):
        """Clean expired OVPN/L2TP subscriptions.
        mode='warn': send warning to users, mark deletion_warning_sent_at
        mode='delete': actually delete from DB + disable on MikroTik (only if warned 24h+ ago)
        """
        from datetime import timedelta
        from vpn_bot.utils import utc_now
        from sqlalchemy import select, and_
        from sqlalchemy.orm import joinedload
        
        async with AsyncSessionLocal() as session:
            now = utc_now()
            threshold = now - timedelta(seconds=seconds)
            
            if mode == 'warn':
                res = await session.execute(
                    select(Subscription).where(
                        and_(Subscription.expiry_date < threshold,
                             Subscription.deletion_warning_sent_at is None)
                    )
                )
                to_warn = res.scalars().all()
                count = 0
                warn_msg = LanguageManager.get('admin.clean.delete_warn_tpl')
                for sub in to_warn:
                    if bot:
                        try:
                            await bot.send_message(chat_id=sub.user_id, text=warn_msg, parse_mode='Markdown')
                            sub.deletion_warning_sent_at = now
                            count += 1
                        except Exception:
                            pass
                await session.commit()
                return count
            else:
                # Delete mode: remove subs that were warned 24h+ ago
                res = await session.execute(
                    select(Subscription)
                    .options(joinedload(Subscription.server))
                    .where(and_(
                        Subscription.expiry_date < threshold,
                        Subscription.deletion_warning_sent_at < now - timedelta(hours=24)
                    ))
                )
                to_delete = res.scalars().all()
                count = 0
                for sub in to_delete:
                    # 1. Disable/delete on MikroTik
                    if sub.server and sub.mikrotik_username:
                        try:
                            mgr = get_mikrotik_manager(sub.server)
                            await asyncio.to_thread(mgr.disable_user, sub.mikrotik_username)
                            await asyncio.to_thread(mgr.delete_user, sub.mikrotik_username)
                        except Exception as e:
                            logger.warning(f"Failed to remove MT user {sub.mikrotik_username}: {e}")
                    # 2. Delete from DB
                    await session.delete(sub)
                    count += 1
                await session.commit()
                return count

    @staticmethod
    async def clean_expired_wg_subscriptions(bot=None, mode='delete', seconds=259200):
        """Clean expired WireGuard subscriptions.
        mode='warn': send warning to users
        mode='delete': disable peer on MikroTik + delete from DB
        """
        from datetime import timedelta
        from vpn_bot.utils import utc_now
        from sqlalchemy import select, and_
        from sqlalchemy.orm import joinedload
        
        async with AsyncSessionLocal() as session:
            now = utc_now()
            threshold = now - timedelta(seconds=seconds)
            
            if mode == 'warn':
                res = await session.execute(
                    select(WireGuardSubscription).where(
                        and_(WireGuardSubscription.expiry_date < threshold,
                             WireGuardSubscription.deletion_warning_sent_at is None)
                    )
                )
                to_warn = res.scalars().all()
                count = 0
                warn_msg = LanguageManager.get('admin.clean.delete_warn_tpl')
                for sub in to_warn:
                    if bot:
                        try:
                            await bot.send_message(chat_id=sub.user_id, text=warn_msg, parse_mode='Markdown')
                            sub.deletion_warning_sent_at = now
                            count += 1
                        except Exception:
                            pass
                await session.commit()
                return count
            else:
                # Delete mode
                res = await session.execute(
                    select(WireGuardSubscription)
                    .options(joinedload(WireGuardSubscription.interface).joinedload(WireGuardInterface.server))
                    .where(and_(
                        WireGuardSubscription.expiry_date < threshold,
                        WireGuardSubscription.deletion_warning_sent_at < now - timedelta(hours=24)
                    ))
                )
                to_delete = res.scalars().all()
                count = 0
                iface_ids: list[int] = []
                for sub in to_delete:
                    # 1. Disable peer + remove queue on MikroTik
                    if sub.interface and sub.interface.server:
                        try:
                            mgr = get_mikrotik_manager(sub.interface.server)
                            await asyncio.to_thread(mgr.set_wg_peer_status, sub.interface.name, sub.peer_public_key, True)
                            await asyncio.to_thread(mgr.remove_wg_peer, sub.interface.name, sub.peer_public_key)
                            await asyncio.to_thread(mgr.remove_wg_queue, sub.unique_identifier)
                        except Exception as e:
                            logger.warning(f"Failed to remove WG peer {sub.unique_identifier}: {e}")
                    if sub.interface_id:
                        iface_ids.append(sub.interface_id)
                    # 2. Delete from DB
                    await session.delete(sub)
                    count += 1
                if iface_ids:
                    from vpn_bot.admin_wg_service import sync_wg_interfaces_current_users

                    await sync_wg_interfaces_current_users(session, iface_ids)
                await session.commit()
                return count

    @staticmethod
    async def sync_mikrotik_orphans():
        """Find and remove MikroTik User Manager users that have no matching DB subscription."""
        from sqlalchemy import select
        
        count = 0
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Server).where(Server.is_active))
            servers = res.scalars().all()
            
            for server in servers:
                try:
                    mgr = get_mikrotik_manager(server)
                    
                    # 1. Check OVPN/L2TP orphans
                    router_users = await asyncio.to_thread(mgr.get_all_um_users)
                    if router_users:
                        db_res = await session.execute(
                            select(Subscription.mikrotik_username).where(Subscription.server_id == server.id)
                        )
                        db_usernames = set(db_res.scalars().all())
                        
                        for ru in router_users:
                            ru_name = ru.get('name', '')
                            if ru_name and ru_name not in db_usernames:
                                try:
                                    await asyncio.to_thread(mgr.delete_user, ru_name)
                                    logger.info(f"Removed orphan UM user: {ru_name} from {server.name}")
                                    count += 1
                                except Exception as e:
                                    logger.warning(f"Failed to remove orphan {ru_name}: {e}")
                    
                    # 2. Check WG orphans
                    res_ifaces = await session.execute(
                        select(WireGuardInterface).where(WireGuardInterface.server_id == server.id)
                    )
                    interfaces = res_ifaces.scalars().all()
                    
                    for iface in interfaces:
                        router_peers = await asyncio.to_thread(mgr.get_all_wg_peers, iface.name)
                        if not router_peers:
                            continue
                        db_res = await session.execute(
                            select(WireGuardSubscription.peer_public_key).where(
                                WireGuardSubscription.interface_id == iface.id
                            )
                        )
                        db_keys = set(db_res.scalars().all())
                        
                        for peer in router_peers:
                            pk = peer.get('public-key', '')
                            if pk and pk not in db_keys:
                                try:
                                    await asyncio.to_thread(mgr.remove_wg_peer, iface.name, pk)
                                    logger.info(f"Removed orphan WG peer from {iface.name}")
                                    count += 1
                                except Exception as e:
                                    logger.warning(f"Failed to remove orphan WG peer: {e}")
                                    
                except Exception as e:
                    logger.error(f"Orphan sync failed for server {server.name}: {e}")
        
        return count

    @staticmethod
    async def clear_phantom_sessions():
        """Clear stale/phantom sessions from User Manager on all servers."""
        count = 0
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Server).where(Server.is_active))
            servers = res.scalars().all()
            
            for server in servers:
                try:
                    mgr = get_mikrotik_manager(server)
                    # Get all active sessions
                    sessions_api = await asyncio.to_thread(
                        lambda: mgr._get_resource('/user-manager/session').get(active='true')
                    )
                    if not sessions_api:
                        continue
                    
                    # Check each session against DB subscriptions
                    db_res = await session.execute(
                        select(Subscription.mikrotik_username).where(
                            Subscription.server_id == server.id,
                            Subscription.status == 'active'
                        )
                    )
                    active_usernames = set(db_res.scalars().all())
                    
                    for sess in sessions_api:
                        sess_user = sess.get('user', '')
                        if sess_user and sess_user not in active_usernames:
                            try:
                                await asyncio.to_thread(mgr.disconnect_user_session, sess_user)
                                logger.info(f"Cleared phantom session for {sess_user}")
                                count += 1
                            except Exception:
                                pass
                except Exception as e:
                    logger.error(f"Clear sessions failed for {server.name}: {e}")
        
        return count


async def run_periodic_cleanup(bot):
    """Background task for automatic periodic database cleanup."""
    from vpn_bot.utils import parse_duration_to_seconds
    
    logger.info("Starting periodic cleanup service...")
    while True:
        try:
            # Get cleanup interval (default: 24 hours)
            interval_str = await get_admin_setting('cleanup_interval', '24h')
            seconds = parse_duration_to_seconds(interval_str)
            if seconds < 3600:
                seconds = 3600  # Minimum 1 hour
            
            logger.info(f"Cleanup: Sleeping for {seconds}s (interval: {interval_str})...")
            await asyncio.sleep(seconds)
            
            logger.info("Cleanup: Starting scheduled cleanup...")
            thresholds = await get_cleanup_thresholds()
            
            async with db_maintenance_lock:
                # Run all cleanup tasks
                c1 = await AdminCleanup.clean_expired_subscriptions(bot=bot, mode='warn', seconds=thresholds['subs'])
                c2 = await AdminCleanup.clean_expired_wg_subscriptions(bot=bot, mode='warn', seconds=thresholds['subs'])
                c3 = await clean_pending_receipts_service(thresholds['receipts'] // 86400 or 5)
                c4 = await clean_old_transactions_service(thresholds['tx'] // 2592000 or 6)
                c5 = await clean_closed_tickets_service(thresholds['tickets'] // 86400 or 7)
                c6 = await clean_inactive_users_service(thresholds['inactive_users'] // 86400 or 30)
                
            total = c1 + c2 + c3 + c4 + c5 + c6
            logger.info(f"Cleanup complete: warned={c1+c2}, receipts={c3}, tx={c4}, tickets={c5}, users={c6} (total={total})")
            
        except Exception as e:
            logger.error(f"Error in periodic cleanup: {e}")
            await asyncio.sleep(300)
