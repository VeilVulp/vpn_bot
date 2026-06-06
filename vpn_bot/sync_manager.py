import asyncio
from sqlalchemy import select, update
from sqlalchemy.orm import joinedload
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Server, Subscription, WireGuardSubscription, WireGuardInterface, AdminSetting
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.utils import logger
from datetime import timezone
from vpn_bot.utils import utc_now

class SyncManager:
    @staticmethod
    async def reconcile_ovpn_subscriptions(session, server: Server):
        """Sync User Manager accounts with DB."""
        mgr = get_mikrotik_manager(server)
        router_users = await asyncio.to_thread(mgr.get_all_um_users)
        if router_users is None: # Error case
            return 0
            
        # Map by username for quick lookup
        router_map = {u['name']: u for u in router_users}
        
        # Get all local subscriptions for this server
        res = await session.execute(
            select(Subscription).where(Subscription.server_id == server.id)
        )
        local_subs = res.scalars().all()
        
        updated_count = 0
        now = utc_now()

        def _expiry_utc(dt):
            if dt is None:
                return None
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)

        pending_disables = []

        for sub in local_subs:
            rem = router_map.get(sub.mikrotik_username)
            if not rem:
                if sub.status == 'active':
                    sub.status = 'inconsistent'
                    updated_count += 1
                continue
            
            rem_disabled = rem.get('disabled') in ['true', 'yes']
            
            # --- Expiration Enforcement ---
            if sub.expiry_date and _expiry_utc(sub.expiry_date) < now:
                if not rem_disabled:
                    logger.info(f"Sync: Queue disable expired UM user {sub.mikrotik_username}")
                    pending_disables.append(sub.mikrotik_username)
                    updated_count += 1
                
                if sub.status != 'expired':
                    sub.status = 'expired'
                    updated_count += 1
                continue # Skip further status sync for expired users
            
            # Sync status for non-expired users
            if rem_disabled and sub.status == 'active':
                sub.status = 'disabled'
                updated_count += 1
            elif (
                not rem_disabled
                and sub.status == 'disabled'
                and not (
                    sub.total_limit_bytes
                    and sub.used_bytes >= sub.total_limit_bytes
                )
            ):
                # DB active only if not quota-disabled (manual re-enable on router)
                sub.status = 'active'
                updated_count += 1
                
            # Sync usage (bytes)
            try:
                download = int(rem.get('download-used', 0))
                upload = int(rem.get('upload-used', 0))
                new_used = download + upload
                if new_used != sub.used_bytes:
                    sub.used_bytes = new_used
                    updated_count += 1
            except Exception:
                pass

            # --- Volume quota (backup if UM did not auto-disable) ---
            if (
                sub.total_limit_bytes
                and sub.total_limit_bytes > 0
                and sub.used_bytes >= sub.total_limit_bytes
                and sub.status == 'active'
            ):
                if not rem_disabled:
                    logger.info(
                        f"Sync: Queue disable OVPN user {sub.mikrotik_username} (quota exhausted)"
                    )
                    pending_disables.append(sub.mikrotik_username)
                    updated_count += 1
                sub.status = 'disabled'
                updated_count += 1

        if pending_disables:
            from vpn_bot.utils import run_mikrotik

            for username in pending_disables:
                try:
                    await run_mikrotik(mgr.disable_user, username)
                except Exception as e:
                    logger.error(f"Sync disable failed for {username}: {e}")

        return updated_count

    @staticmethod
    async def reconcile_wg_subscriptions(session, server: Server):
        """Sync WireGuard peers and queues with DB."""
        mgr = get_mikrotik_manager(server)
        
        # 1. Sync Peers across all interfaces
        res = await session.execute(
            select(WireGuardInterface).where(WireGuardInterface.server_id == server.id)
        )
        interfaces = res.scalars().all()
        
        updated_count = 0
        for interface in interfaces:
            router_peers = await asyncio.to_thread(mgr.get_all_wg_peers, interface.name)
            if router_peers is None: continue
            
            # Map by public key
            peer_map = {p['public-key']: p for p in router_peers}
            
            res_subs = await session.execute(
                select(WireGuardSubscription).where(WireGuardSubscription.interface_id == interface.id)
            )
            subs = res_subs.scalars().all()
            
            now = utc_now()

            def _expiry_utc_wg(dt):
                if dt is None:
                    return None
                if dt.tzinfo is None:
                    return dt.replace(tzinfo=timezone.utc)
                return dt.astimezone(timezone.utc)

            for sub in subs:
                rem = peer_map.get(sub.peer_public_key)
                if not rem:
                    if sub.status == 'active':
                        sub.status = 'inconsistent'
                        updated_count += 1
                    continue
                
                rem_disabled = rem.get('disabled') in ['true', 'yes']
                
                # --- Expiration Enforcement ---
                if sub.expiry_date and _expiry_utc_wg(sub.expiry_date) < now:
                    if not rem_disabled:
                        logger.info(f"Sync: Disabling expired WG peer {sub.unique_identifier}")
                        await asyncio.to_thread(mgr.set_wg_peer_status, interface.name, sub.peer_public_key, disabled=True)
                        updated_count += 1
                    
                    if sub.status != 'expired':
                        sub.status = 'expired'
                        updated_count += 1
                    continue
                
                # Sync status for non-expired peers
                if rem_disabled and sub.status == 'active':
                    sub.status = 'disabled'
                    updated_count += 1
                elif (
                    not rem_disabled
                    and sub.status == 'disabled'
                    and not (
                        sub.bytes_remaining
                        and (sub.total_bytes_rx or 0) + (sub.total_bytes_tx or 0)
                        >= sub.bytes_remaining
                    )
                ):
                    # Re-enable in DB only if disabled for non-quota reasons (e.g. manual)
                    sub.status = 'active'
                    updated_count += 1
                    
                # Sync usage (bytes) - Cumulative Logic
                try:
                    curr_rx = int(rem.get('rx', 0))
                    curr_tx = int(rem.get('tx', 0))
                    
                    # RX Delta
                    rx_delta = curr_rx - (sub.last_router_rx or 0)
                    if rx_delta < 0: # Reset occurred
                        rx_delta = curr_rx
                    
                    # TX Delta
                    tx_delta = curr_tx - (sub.last_router_tx or 0)
                    if tx_delta < 0: # Reset occurred
                        tx_delta = curr_tx
                        
                    if rx_delta > 0 or tx_delta > 0:
                        sub.total_bytes_rx = (sub.total_bytes_rx or 0) + rx_delta
                        sub.total_bytes_tx = (sub.total_bytes_tx or 0) + tx_delta
                        updated_count += 1
                        
                    # Update last seen router values
                    sub.last_router_rx = curr_rx
                    sub.last_router_tx = curr_tx

                    # Remaining quota (bytes_remaining stores purchased quota cap)
                    quota_bytes = sub.bytes_remaining or 0
                    if quota_bytes > 0:
                        used_total = (sub.total_bytes_rx or 0) + (sub.total_bytes_tx or 0)
                        if used_total >= quota_bytes and sub.status == 'active':
                            if not rem_disabled:
                                logger.info(
                                    f"Sync: Disabling WG peer {sub.unique_identifier} (quota exhausted)"
                                )
                                await asyncio.to_thread(
                                    mgr.set_wg_peer_status,
                                    interface.name,
                                    sub.peer_public_key,
                                    True,
                                )
                                updated_count += 1
                            sub.status = 'disabled'
                            updated_count += 1
                except Exception as e:
                    logger.error(f"Sync usage error for {sub.unique_identifier}: {e}")

            from vpn_bot.admin_wg_service import sync_wg_interface_current_users

            await sync_wg_interface_current_users(session, interface.id)

        # 2. Sync Simple Queues (Rate Limits)
        queues = await asyncio.to_thread(mgr.get_all_queues)
        if queues is not None:
            queue_map = {q['name']: q for q in queues}
            
            # We check all active WG subs to ensure their queue matches their profile limit
            res_active = await session.execute(
                select(WireGuardSubscription)
                .options(joinedload(WireGuardSubscription.profile))
                .where(WireGuardSubscription.status == 'active')
            )
            active_subs = res_active.scalars().all()
            
            for sub in active_subs:
                if not sub.profile or not sub.profile.rate_limit: continue
                
                q_name = sub.unique_identifier
                rem_q = queue_map.get(q_name)
                
                if not rem_q:
                    # Re-create missing queue
                    logger.warning(f"Sync: Missing queue for {sub.unique_identifier}, recreating...")
                    await asyncio.to_thread(mgr.add_wg_queue, q_name, sub.assigned_ip, sub.profile.rate_limit)
                else:
                    # Check if rate limit matches
                    # rem_q['max-limit'] is usually 'upload/download' e.g. '8388608/8388608'
                    # For simplicity, we just force update if not sure, or skip for now
                    pass
                
        return updated_count

    @staticmethod
    async def sync_all_servers():
        """Top-level sync orchestrator."""
        from vpn_bot.utils import db_maintenance_lock

        if db_maintenance_lock.locked():
            logger.warning("Sync skipped: database maintenance in progress")
            return 0

        return await SyncManager._sync_all_servers_impl()

    @staticmethod
    async def _sync_all_servers_impl():
        """Internal sync body (caller holds db_maintenance_lock)."""
        from datetime import datetime

        # 1. Fetch active servers first
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Server).where(Server.is_active == True))
            servers = res.scalars().all()
            server_ids = [s.id for s in servers]
        
        total_updated = 0
        # 2. Sync each server in its own transaction (MikroTik disables run inside reconcile after DB work)
        for s_id in server_ids:
            async with AsyncSessionLocal() as session:
                server = await session.get(Server, s_id)
                if not server:
                    continue
                try:
                    logger.info(f"Syncing server {server.name} ({server.host})...")
                    u1 = await SyncManager.reconcile_ovpn_subscriptions(session, server)
                    u2 = await SyncManager.reconcile_wg_subscriptions(session, server)
                    total_updated += (u1 + u2)
                    await session.commit()
                except Exception as e:
                    logger.error(f"Failed to sync server {server.name}: {e}")
                    await session.rollback()
        
        # 3. Update last sync time (short lock for maintenance coordination)
        from vpn_bot.utils import db_maintenance_lock

        async with db_maintenance_lock:
            async with AsyncSessionLocal() as session:
                res_st = await session.execute(
                    select(AdminSetting).where(AdminSetting.key == "last_sync_time")
                )
                st = res_st.scalars().first()
                now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                if not st:
                    session.add(AdminSetting(key="last_sync_time", value=now_str))
                else:
                    st.value = now_str
                await session.commit()

        logger.info(f"Sync complete. Total DB records updated: {total_updated}")
        return total_updated

    @staticmethod
    async def run_periodic_sync():
        """Background task for automatic sync."""
        logger.info("Starting periodic sync service...")
        while True:
            try:
                # 1. Get interval (we import inside to avoid circular deps if any)
                from vpn_bot.admin_settings import get_admin_setting
                from vpn_bot.utils import parse_duration_to_seconds
                interval_str = await get_admin_setting('sync_interval_hours', "6h")
                
                seconds = parse_duration_to_seconds(interval_str)
                # Safeguard: Minimum 10 minutes to prevent API abuse/CPU spikes
                if seconds < 600:
                    seconds = 600
                    logger.warning(f"Sync: Interval '{interval_str}' is too low. Using 600s (10m) instead.")
                
                # 2. Wait
                logger.info(f"Sync: Sleeping for {seconds} seconds (Interval: {interval_str})...")
                await asyncio.sleep(seconds)
                
                # 3. Exec
                logger.info("Sync: Starting scheduled synchronization...")
                await SyncManager.sync_all_servers()
            except Exception as e:
                logger.error(f"Error in periodic sync task: {e}")
                await asyncio.sleep(300) # Sleep 5m on error before retry
