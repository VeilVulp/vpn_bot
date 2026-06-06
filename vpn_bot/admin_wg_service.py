
import asyncio
from datetime import datetime, timedelta

from sqlalchemy import func, select
from telegram.helpers import escape_markdown

import os

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.models import WireGuardProfile, WireGuardInterface, Server, WireGuardSubscription, AdminSetting
from vpn_bot.utils import logger, LanguageManager

_WG_MT_APPLY_TIMEOUT = float(os.getenv("WG_MT_APPLY_TIMEOUT", "60"))
_WG_MT_APPLY_RETRIES = int(os.getenv("MIKROTIK_APPLY_RETRIES", "3"))

# Statuses that occupy a purchase slot on an interface.
WG_OCCUPYING_STATUSES = ("active", "pending")


async def count_wg_interface_active_subs(session, interface_id: int) -> int:
    """Count active/pending subs occupying capacity on an interface."""
    res = await session.execute(
        select(func.count())
        .select_from(WireGuardSubscription)
        .where(
            WireGuardSubscription.interface_id == interface_id,
            WireGuardSubscription.status.in_(WG_OCCUPYING_STATUSES),
        )
    )
    return int(res.scalar() or 0)


async def sync_wg_interface_current_users(session, interface_id: int) -> int:
    """Align stored current_users with live active/pending sub count."""
    iface = await session.get(WireGuardInterface, interface_id)
    if not iface:
        return 0
    count = await count_wg_interface_active_subs(session, interface_id)
    iface.current_users = count
    return count


async def sync_wg_interfaces_current_users(session, interface_ids: list[int]) -> None:
    """Sync capacity counters for multiple interfaces (deduplicated)."""
    for iface_id in dict.fromkeys(interface_ids):
        if iface_id:
            await sync_wg_interface_current_users(session, iface_id)


async def pick_wg_interface_for_purchase(session, server_id: int):
    """Pick least-loaded interface with free active/pending slots (row-locked)."""
    # Lock all active interfaces on the server to serialize concurrent purchases.
    await session.execute(
        select(WireGuardInterface.id)
        .where(
            WireGuardInterface.server_id == server_id,
            WireGuardInterface.is_active,
        )
        .with_for_update()
    )
    int_res = await session.execute(
        select(WireGuardInterface)
        .where(
            WireGuardInterface.server_id == server_id,
            WireGuardInterface.is_active,
        )
        .order_by(WireGuardInterface.id.asc())
    )
    ifaces = int_res.scalars().all()
    best = None
    best_active = None
    for iface in ifaces:
        active = await count_wg_interface_active_subs(session, iface.id)
        if active < iface.max_users and (best_active is None or active < best_active):
            best = iface
            best_active = active
    return best


async def next_wg_client_ip(session, interface: WireGuardInterface) -> str:
    """Next client IP avoiding reuse while expired/disabled peers remain on router."""
    root = interface.address.split("/")[0].rsplit(".", 1)[0]
    res = await session.execute(
        select(WireGuardSubscription.assigned_ip).where(
            WireGuardSubscription.interface_id == interface.id,
            WireGuardSubscription.assigned_ip.isnot(None),
        )
    )
    max_suffix = 1
    prefix = f"{root}."
    for (ip,) in res.all():
        if ip and ip.startswith(prefix):
            try:
                max_suffix = max(max_suffix, int(ip.rsplit(".", 1)[-1]))
            except ValueError:
                pass
    return f"{root}.{max_suffix + 1}"


async def get_all_wg_profiles():
    """Fetch active WireGuard profiles (excludes pytest / DBTEST rows)."""
    from vpn_bot.test_data import is_test_profile_name

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WireGuardProfile).where(WireGuardProfile.is_active)
        )
        rows = result.scalars().all()
    return [p for p in rows if not is_test_profile_name(p.name)]

async def get_wg_profile_by_id(profile_id: int):
    """Fetch a specific WG profile by ID."""
    async with AsyncSessionLocal() as session:
        return await session.get(WireGuardProfile, profile_id)

async def create_wg_profile(data: dict):
    """Create a new WG profile in DB."""
    async with AsyncSessionLocal() as session:
        new_prof = WireGuardProfile(
            name=data['name'],
            volume_gb=data['volume'],
            duration_days=data['days'],
            price_usd=data.get('price_usd', 0),
            price_toman=data.get('price_toman', 0),
            price_rial=data.get('price_toman', 0) * 10,
            rate_limit=data.get('rate_limit'),
            server_id=data.get('server_id'),
            is_active=True
        )
        session.add(new_prof)
        await session.commit()
        await session.refresh(new_prof)
        return new_prof

async def update_wg_profile(profile_id: int, data: dict):
    """Update an existing WG profile."""
    async with AsyncSessionLocal() as session:
        prof = await session.get(WireGuardProfile, profile_id)
        if not prof: return False
        
        old_rate = prof.rate_limit
        if 'name' in data: prof.name = data['name']
        if 'volume' in data: prof.volume_gb = data['volume']
        if 'days' in data: prof.duration_days = data['days']
        if 'price_usd' in data: prof.price_usd = data['price_usd']
        if 'price_toman' in data:
            prof.price_toman = data['price_toman']
            prof.price_rial = prof.price_toman * 10
        if 'rate_limit' in data: prof.rate_limit = data['rate_limit']
        if 'server_id' in data: prof.server_id = data['server_id']
        
        # Propagate speed update if changed
        if prof.rate_limit != old_rate:
            await propagate_wg_speed_update(session, prof.id, prof.rate_limit)
        
        await session.commit()
        return True

async def propagate_wg_speed_update(session, wg_profile_id: int, new_speed: str):
    """Update all active WireGuard user queues."""
    from sqlalchemy.orm import joinedload
    from vpn_bot.mikrotik_manager import get_mikrotik_manager
    import asyncio
    
    res = await session.execute(
        select(WireGuardSubscription)
        .options(joinedload(WireGuardSubscription.interface))
        .where(WireGuardSubscription.profile_id == wg_profile_id, WireGuardSubscription.status == 'active')
    )
    active_subs = res.scalars().all()
    
    for sub in active_subs:
        interface = sub.interface
        if interface:
            server = await session.get(Server, interface.server_id)
            if server:
                mgr = get_mikrotik_manager(server)
                try:
                    if new_speed:
                        await asyncio.to_thread(mgr.add_wg_queue, sub.unique_identifier, f"{sub.assigned_ip}/32", new_speed)
                    else:
                        await asyncio.to_thread(mgr.remove_wg_queue, sub.unique_identifier)
                except: pass

async def delete_wg_profile(profile_id: int):
    """Delete or archive a WG profile."""
    async with AsyncSessionLocal() as session:
        prof = await session.get(WireGuardProfile, profile_id)
        if not prof: return False
        
        # Check for active subs
        res = await session.execute(
            select(WireGuardSubscription)
            .where(WireGuardSubscription.profile_id == profile_id, WireGuardSubscription.status == 'active')
        )
        if res.scalars().first():
            prof.is_active = False
            await session.commit()
            return True, "Archived"
            
        await session.delete(prof)
        await session.commit()
        return True, "Deleted"

async def sync_wg_interfaces_from_router(server_id: int) -> int:
    """Import MikroTik WireGuard interfaces that exist on router but not in DB."""
    from vpn_bot.admin_server_service import get_server_by_id
    from vpn_bot.mikrotik_manager import get_mikrotik_manager
    from vpn_bot.test_data import is_test_wg_interface_name

    server = await get_server_by_id(server_id)
    if not server:
        return 0

    from vpn_bot.mt_session import run_mikrotik_for_server

    mgr = get_mikrotik_manager(server)
    list_timeout = float(__import__("os").getenv("WG_MT_LIST_TIMEOUT", "15"))
    router_ifaces = await run_mikrotik_for_server(
        server, mgr.get_wg_interfaces, timeout=list_timeout, retries=2
    ) or []
    if not router_ifaces:
        return 0

    addr_map = await run_mikrotik_for_server(
        server, mgr.get_wg_interface_address_map, timeout=list_timeout, retries=2
    )
    created = 0

    async with AsyncSessionLocal() as session:
        existing = (
            await session.execute(
                select(WireGuardInterface).where(WireGuardInterface.server_id == server_id)
            )
        ).scalars().all()
        by_name = {i.name: i for i in existing}
        parent = (
            await session.execute(
                select(WireGuardInterface)
                .where(
                    WireGuardInterface.server_id == server_id,
                    WireGuardInterface.is_active,
                )
                .order_by(WireGuardInterface.id.asc())
                .limit(1)
            )
        ).scalars().first()

        for row in router_ifaces:
            name = row.get("name") or ""
            if not name or is_test_wg_interface_name(name) or name in by_name:
                continue

            port_raw = row.get("listen-port") or "51820"
            try:
                listen_port = int(port_raw)
            except (TypeError, ValueError):
                listen_port = 51820

            address = addr_map.get(name) or "10.0.0.1/24"
            pubkey = row.get("public-key") or "imported-from-router"

            iface = WireGuardInterface(
                server_id=server_id,
                name=name,
                public_key=pubkey,
                private_key=row.get("private-key") or "managed-by-router",
                listen_port=listen_port,
                address=address,
                is_active=True,
            )
            if parent:
                iface.dns = parent.dns
                iface.mtu = parent.mtu
                iface.keepalive = parent.keepalive
                iface.max_users = parent.max_users
                iface.upstream_interface = parent.upstream_interface
                iface.routing_mark = parent.routing_mark
                iface.nat_routing_mark = parent.nat_routing_mark
                iface.nat_dst_address = parent.nat_dst_address
                iface.nat_dst_address_list = parent.nat_dst_address_list
                iface.nat_dst_negate = parent.nat_dst_negate
                iface.gateway = parent.gateway
                iface.route_table = parent.route_table
                iface.route_dst_address = parent.route_dst_address
                iface.route_distance = parent.route_distance
                iface.endpoint_host = parent.endpoint_host

            session.add(iface)
            await session.flush()
            by_name[name] = iface

            await run_mikrotik_for_server(
                server,
                mgr.sync_wg_interface_automation,
                name=iface.name,
                address=iface.address,
                upstream=iface.upstream_interface,
                routing_mark=iface.routing_mark,
                nat_routing_mark=iface.nat_routing_mark,
                nat_dst=iface.nat_dst_address or "127.0.0.1",
                nat_dst_list=iface.nat_dst_address_list,
                nat_dst_negate=bool(getattr(iface, "nat_dst_negate", True)),
                gateway=iface.gateway,
                route_table=iface.route_table,
                route_dst=iface.route_dst_address or "0.0.0.0/0",
                route_distance=iface.route_distance if iface.route_distance is not None else 1,
                listen_port=iface.listen_port,
                timeout=_WG_MT_APPLY_TIMEOUT,
            )
            created += 1

        if created:
            await session.commit()
            logger.info(f"Synced {created} WG interface(s) from router into DB for server {server.name}")

    return created


async def get_all_wg_interfaces():
    """Fetch all WG interfaces with server loaded (safe outside session)."""
    from sqlalchemy.orm import joinedload

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WireGuardInterface)
            .options(joinedload(WireGuardInterface.server))
            .order_by(WireGuardInterface.server_id.asc(), WireGuardInterface.id.asc())
        )
        from vpn_bot.test_data import is_test_server_row, is_test_wg_interface_name

        rows = list(result.scalars().unique().all())
        return [
            i
            for i in rows
            if not is_test_wg_interface_name(i.name)
            and not (getattr(i, "server", None) and is_test_server_row(i.server))
        ]

async def get_wg_subscription_for_config(query_text: str):
    """Fetch a WireGuard subscription with its interface and server by ID or Unique Identifier."""
    from sqlalchemy.orm import joinedload
    async with AsyncSessionLocal() as session:
        # Try finding by unique identifier or ID
        wg_sub = None
        if query_text.isdigit():
             wg_sub = await session.get(WireGuardSubscription, int(query_text))
        
        if not wg_sub:
            wg_sub_res = await session.execute(
                select(WireGuardSubscription).where(WireGuardSubscription.unique_identifier == query_text)
            )
            wg_sub = wg_sub_res.scalars().first()
            
        if not wg_sub:
            return None
        
        # Re-fetch with options to ensure loading relations
        stmt = (
            select(WireGuardSubscription)
            .options(
                joinedload(WireGuardSubscription.interface),
                joinedload(WireGuardSubscription.profile),
            )
            .where(WireGuardSubscription.id == wg_sub.id)
        )
        wg_sub = (await session.execute(stmt)).scalars().first()
        
        if not wg_sub or not wg_sub.interface:
            return None
            
        server = await session.get(Server, wg_sub.interface.server_id)
        return wg_sub, server

async def generate_wg_subscription_config(wg_sub_id: int):
    """Generate WireGuard config text, QR, and filename for a subscription."""
    from sqlalchemy.orm import joinedload
    from vpn_bot.wg_delivery import build_wg_delivery_payload

    async with AsyncSessionLocal() as session:
        wg_sub = (
            await session.execute(
                select(WireGuardSubscription)
                .options(
                    joinedload(WireGuardSubscription.interface),
                    joinedload(WireGuardSubscription.profile),
                )
                .where(WireGuardSubscription.id == wg_sub_id)
            )
        ).scalars().first()

        if not wg_sub or not wg_sub.interface:
            return None

        server = await session.get(Server, wg_sub.interface.server_id)
        if not server:
            return None

        return build_wg_delivery_payload(wg_sub, server)

async def update_wg_interface(interface_id: int, data: dict):
    """Update a WireGuard interface in DB and MikroTik."""
    from vpn_bot.mikrotik_manager import get_mikrotik_manager
    import asyncio
    
    async with AsyncSessionLocal() as session:
        interface = await session.get(WireGuardInterface, interface_id)
        if not interface: return False, "Interface not found"
        
        server = await session.get(Server, interface.server_id)
        if not server: return False, "Server not found"
        
        mgr = get_mikrotik_manager(server)
        
        # 1. Router Update if needed
        if 'listen_port' in data:
            success = await asyncio.to_thread(mgr.update_wg_interface_port, interface.name, data['listen_port'])
            if not success: return False, "Failed to update port on router"
            interface.listen_port = data['listen_port']

        # 2. DB updates
        if 'dns' in data: interface.dns = data['dns']
        if 'endpoint_host' in data: interface.endpoint_host = data['endpoint_host']
        if 'mtu' in data: interface.mtu = data['mtu']
        if 'keepalive' in data: interface.keepalive = data['keepalive']
        if 'max_users' in data: interface.max_users = data['max_users']
        if 'address' in data: interface.address = data['address']
        if 'network' in data: interface.network = data['network']
        if 'gateway' in data: interface.gateway = data['gateway']
        if 'route_table' in data:
            interface.route_table = data['route_table']
        if 'route_dst_address' in data:
            interface.route_dst_address = data['route_dst_address']
        if 'route_distance' in data:
            interface.route_distance = int(data['route_distance'])
        if 'upstream_interface' in data:
            interface.upstream_interface = data['upstream_interface']
        if 'routing_mark' in data:
            interface.routing_mark = data['routing_mark']
        if 'nat_routing_mark' in data:
            interface.nat_routing_mark = data['nat_routing_mark']
        if 'nat_dst_address' in data:
            interface.nat_dst_address = data['nat_dst_address']
        if 'nat_dst_address_list' in data:
            interface.nat_dst_address_list = data['nat_dst_address_list']
        if 'nat_dst_negate' in data:
            interface.nat_dst_negate = bool(data['nat_dst_negate'])

        await session.commit()
        return True, None

async def get_wg_subscription_comprehensive_info(query_text: str):
    """Fetch all details for a WG subscription from DB and MikroTik."""
    res = await get_wg_subscription_for_config(query_text)
    if not res: return None, None
    
    wg_sub, server = res
    try:
        from vpn_bot.mikrotik_manager import get_mikrotik_manager
        mgr = get_mikrotik_manager(server)
        # For WG, info might be split across peer and queue
        peer_info = await asyncio.to_thread(mgr.get_wg_peer_info, wg_sub.interface.name, wg_sub.peer_public_key)
        queue_info = await asyncio.to_thread(mgr.get_wg_queue_info, wg_sub.unique_identifier)
        
        return wg_sub, {'peer': peer_info, 'queue': queue_info, 'server': server}
    except Exception as e:
        logger.error(f"Failed to fetch WG MT info for {query_text}: {e}")
        return wg_sub, {'server': server}

async def format_wg_subscription_info_text(wg_sub, mt_data):
    """Format WG subscription details for admin display."""

    from vpn_bot.utils import LanguageManager, format_datetime

    formatted_expiry = await format_datetime(wg_sub.expiry_date, include_time=False)
    db_expiry = formatted_expiry if wg_sub.expiry_date else LanguageManager.get('common.na')

    peer = mt_data.get('peer') or {}
    status_active = LanguageManager.get('status.active')
    status_disabled = LanguageManager.get('status.disabled')
    if peer:
        mt_active = status_disabled if peer.get('disabled') == 'true' else status_active
    else:
        mt_active = status_active if wg_sub.status == 'active' else status_disabled

    total_gb = (wg_sub.bytes_remaining / (1024**3)) if wg_sub.bytes_remaining else 0
    used_gb = ((wg_sub.total_bytes_rx or 0) + (wg_sub.total_bytes_tx or 0)) / (1024**3)
    total_display = f"{total_gb:.0f}" if wg_sub.bytes_remaining else '∞'

    plan_name = LanguageManager.get('common.na')
    if wg_sub.profile_id:
        async with AsyncSessionLocal() as session:
            from vpn_bot.models import WireGuardProfile

            prof = await session.get(WireGuardProfile, wg_sub.profile_id)
            if prof:
                plan_name = prof.name

    server = mt_data.get('server')
    server_label = (
        escape_markdown(server.name, version=1) if server else LanguageManager.get('common.na')
    )
    ip_label = (
        escape_markdown(wg_sub.assigned_ip, version=1)
        if wg_sub.assigned_ip
        else LanguageManager.get('common.na')
    )

    return LanguageManager.get(
        'admin.user.wg_info_title',
        uid=escape_markdown(wg_sub.unique_identifier or str(wg_sub.id), version=1),
        status=mt_active,
        used=f"{used_gb:.2f}",
        total=total_display,
        expiry=db_expiry,
        ip=ip_label,
        server=server_label,
        plan=escape_markdown(plan_name, version=1),
    )


async def _get_wg_sub_by_id(wg_sub_id: int, session=None):
    from sqlalchemy.orm import joinedload

    if session is not None:
        return (
            await session.execute(
                select(WireGuardSubscription)
                .options(joinedload(WireGuardSubscription.interface))
                .where(WireGuardSubscription.id == wg_sub_id)
            )
        ).scalars().first()

    async with AsyncSessionLocal() as session_internal:
        return (
            await session_internal.execute(
                select(WireGuardSubscription)
                .options(joinedload(WireGuardSubscription.interface))
                .where(WireGuardSubscription.id == wg_sub_id)
            )
        ).scalars().first()


async def delete_wg_subscription(wg_sub_id: int) -> bool:
    """Remove a single WireGuard subscription from MikroTik and DB."""
    async with AsyncSessionLocal() as session:
        wg_sub = await _get_wg_sub_by_id(wg_sub_id, session=session)
        if not wg_sub:
            return False

        if wg_sub.interface:
            server = await session.get(Server, wg_sub.interface.server_id)
            if server:
                try:
                    mgr = get_mikrotik_manager(server)
                    await asyncio.to_thread(mgr.remove_wg_peer, wg_sub.interface.name, wg_sub.peer_public_key)
                    await asyncio.to_thread(mgr.remove_wg_queue, wg_sub.unique_identifier)
                except Exception as e:
                    logger.warning(f"delete_wg_subscription MT cleanup failed for {wg_sub_id}: {e}")

        iface_id = wg_sub.interface_id
        await session.delete(wg_sub)
        if iface_id:
            await sync_wg_interface_current_users(session, iface_id)
        await session.commit()
        return True


async def extend_wg_subscription(wg_sub_id: int, days: int):
    """Extend WG subscription expiry in DB."""
    async with AsyncSessionLocal() as session:
        wg_sub = await _get_wg_sub_by_id(wg_sub_id, session=session)
        if not wg_sub:
            return False, None
        base = wg_sub.expiry_date or datetime.utcnow()
        wg_sub.expiry_date = base + timedelta(days=days)
        if wg_sub.status == 'expired':
            wg_sub.status = 'active'
        await session.commit()
        return True, wg_sub.expiry_date


async def add_wg_subscription_data(wg_sub_id: int, gb: float) -> bool:
    """Add remaining traffic quota for a WG subscription."""
    async with AsyncSessionLocal() as session:
        wg_sub = await _get_wg_sub_by_id(wg_sub_id, session=session)
        if not wg_sub:
            return False
        bytes_to_add = int(gb * (1024**3))
        if wg_sub.bytes_remaining is None:
            wg_sub.bytes_remaining = bytes_to_add
        else:
            wg_sub.bytes_remaining += bytes_to_add
        await session.commit()
        return True


async def toggle_wg_subscription_status(wg_sub_id: int):
    """Enable/disable WG peer on MikroTik."""
    async with AsyncSessionLocal() as session:
        wg_sub = await _get_wg_sub_by_id(wg_sub_id, session=session)
        if not wg_sub or not wg_sub.interface:
            return False, None

        server = await session.get(Server, wg_sub.interface.server_id)
        if not server:
            return False, None

        disable_next = wg_sub.status == 'active'
        mgr = get_mikrotik_manager(server)
        success = await asyncio.to_thread(
            mgr.set_wg_peer_status,
            wg_sub.interface.name,
            wg_sub.peer_public_key,
            disable_next,
        )
        if success:
            wg_sub.status = 'disabled' if disable_next else 'active'
            await session.commit()
            label = 'disabled' if disable_next else 'enabled'
            return True, label
        return False, None


async def get_wg_interface_details(interface_id: int):
    """Fetch WG interface and count active users (server eager-loaded)."""
    from sqlalchemy.orm import joinedload

    async with AsyncSessionLocal() as session:
        interface = (
            await session.execute(
                select(WireGuardInterface)
                .options(joinedload(WireGuardInterface.server))
                .where(WireGuardInterface.id == interface_id)
            )
        ).scalars().first()
        if not interface:
            return None, 0

        res = await session.execute(
            select(WireGuardSubscription).where(
                WireGuardSubscription.interface_id == interface_id,
                WireGuardSubscription.status == "active",
            )
        )
        active_count = len(res.scalars().all())
        return interface, active_count

async def broadcast_interface_update(interface_id: int, bot, custom_msg: str = None):
    """Broadcast WG update to all active users on an interface."""
    from sqlalchemy.orm import joinedload
    
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WireGuardSubscription)
            .options(joinedload(WireGuardSubscription.user))
            .where(WireGuardSubscription.interface_id == interface_id, WireGuardSubscription.status == 'active')
        )
        subs = res.scalars().all()
        
        if not custom_msg:
            tpl_res = await session.execute(select(AdminSetting).where(AdminSetting.key == 'wg_update_template'))
            tpl_setting = tpl_res.scalars().first()
            custom_msg = tpl_setting.value if tpl_setting else LanguageManager.get('admin.wg.update_notif_default')
            
        subs_by_user = {}
        for s in subs:
            if s.user_id not in subs_by_user: subs_by_user[s.user_id] = []
            subs_by_user[s.user_id].append(s)
            
        count = 0
        for user_id, user_subs in subs_by_user.items():
            try:
                user = user_subs[0].user
                if not user or not user.telegram_id: continue
                
                sub_list_text = ""
                from telegram import InlineKeyboardButton, InlineKeyboardMarkup
                keyboard = []
                for sub in user_subs:
                    sub_list_text += f"🔹 {escape_markdown(sub.unique_identifier, version=1)}\n"
                    btn_text = LanguageManager.get('admin.wg.btn_get_config', name=sub.unique_identifier)
                    keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"get_wg_conf_{sub.id}")])
                
                msg_text = LanguageManager.get('admin.wg.broadcast_msg_list', text=custom_msg, list=sub_list_text)
                await bot.send_message(user.telegram_id, msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
                count += 1
                await asyncio.sleep(0.05)
            except Exception as e:
                logger.error(f"Broadcast failed for user {user_id}: {e}")
        return count

async def migrate_wg_interface_logic(source_id: int, target_data: dict):
    """Robust migration logic: move peers using batch migration and subnet remapping."""
    from vpn_bot.mikrotik_manager import get_mikrotik_manager
    
    async with AsyncSessionLocal() as session:
        source = await session.get(WireGuardInterface, source_id)
        if not source: return False, "Source not found"
        
        # Lock source
        source.is_active = False
        await session.commit()
        
        server = await session.get(Server, source.server_id)
        mgr = get_mikrotik_manager(server)
        
        # 1. Get or create target
        if target_data['type'] == 'new':
            params = target_data['params']
            keys = await asyncio.to_thread(mgr.create_wg_interface, params['name'], params['listen_port'], params['address'])
            if not keys: return False, "Failed to create interface on router"
            
            target = WireGuardInterface(
                server_id=source.server_id, name=params['name'],
                public_key=keys['public_key'], private_key=keys.get('private_key', 'managed-by-router'),
                address=params['address'], listen_port=params['listen_port'],
                dns=source.dns, endpoint_host=source.endpoint_host,
                mtu=source.mtu, keepalive=source.keepalive, max_users=source.max_users,
                is_active=True
            )
            session.add(target)
            await session.flush()
            await apply_wg_automation(target.id)
        else:
            target = await session.get(WireGuardInterface, target_data['interface_id'])
            
        # 2. Prepare Peers for Batch Migration
        res = await session.execute(
            select(WireGuardSubscription)
            .where(WireGuardSubscription.interface_id == source_id, WireGuardSubscription.status == 'active')
        )
        subs = res.scalars().all()
        
        if not subs:
            return True, (target.id, {'added': 0, 'failed': 0})
            
        # Subnet remapping logic
        target_subnet = target.address.split('/')[0]
        target_prefix = '.'.join(target_subnet.split('.')[:3]) + '.'
        source_subnet = source.address.split('/')[0]
        source_prefix = '.'.join(source_subnet.split('.')[:3]) + '.'
        new_subnet_prefix = target_prefix if target_prefix != source_prefix else None
        
        peers_list = []
        for sub in subs:
            peers_list.append({
                'public_key': sub.peer_public_key,
                'allowed_address': f"{sub.assigned_ip}/32",
                'comment': sub.unique_identifier or ''
            })
            
        # 3. Execute Migration on Router
        result = await asyncio.to_thread(
            mgr.migrate_wg_peers,
            source.name, target.name, peers_list,
            new_subnet_prefix=new_subnet_prefix,
            remove_from_old=True
        )
        
        # 4. Update DB
        ip_map = result.get('new_ips', {})
        for sub in subs:
            sub.interface_id = target.id
            if ip_map and sub.assigned_ip in ip_map:
                sub.assigned_ip = ip_map[sub.assigned_ip]
        
        source.current_users = 0
        await session.commit()
        
        # 5. Reconciliation Sync
        try:
            from vpn_bot.sync_manager import SyncManager
            await SyncManager.reconcile_wg_subscriptions(session, server)
        except Exception as e:
            logger.error(f"Migration reconciliation error: {e}")
            
        return True, (target.id, result)

def wg_route_effective_table(interface) -> str | None:
    """Routing table name used for /ip/route (route_table or mangle routing_mark)."""
    return interface.route_table or interface.routing_mark or None


def wg_route_configured_on_interface(interface) -> bool:
    """True when admin set route-list fields that expect a router /ip/route entry."""
    if interface.gateway:
        return True
    if interface.route_table:
        return True
    dst = getattr(interface, "route_dst_address", None) or "0.0.0.0/0"
    if dst and dst != "0.0.0.0/0":
        return True
    dist = getattr(interface, "route_distance", None)
    return dist is not None and dist != 1


def validate_wg_route_before_apply(interface) -> str | None:
    """
    Return a locale key if route list is configured but cannot be pushed to MikroTik.
    None means apply may proceed (route may still be skipped if gateway/table missing).
    """
    if not wg_route_configured_on_interface(interface):
        return None
    if not (interface.gateway or "").strip():
        return "admin.wg.route_requires_gateway"
    if not wg_route_effective_table(interface):
        return "admin.wg.route_requires_table"
    return None


async def apply_wg_automation(interface_id: int) -> tuple[bool, bool]:
    """Sync WG rules to MikroTik. Returns (success, route_applied_on_router)."""
    from vpn_bot.mikrotik_manager import get_mikrotik_manager
    async with AsyncSessionLocal() as session:
        interface = await session.get(WireGuardInterface, interface_id)
        if not interface:
            return False, False

        server = await session.get(Server, interface.server_id)
        if not server:
            return False, False

        from vpn_bot.mt_session import run_mikrotik_for_server

        mgr = get_mikrotik_manager(server)
        logger.info(
            "route_apply start interface_id=%s name=%s server=%s:%s",
            interface_id,
            interface.name,
            server.host,
            server.port,
        )
        result = await run_mikrotik_for_server(
            server,
            mgr.sync_wg_interface_automation,
            name=interface.name,
            address=interface.address,
            upstream=interface.upstream_interface,
            routing_mark=interface.routing_mark,
            nat_routing_mark=interface.nat_routing_mark,
            nat_dst=interface.nat_dst_address or "127.0.0.1",
            nat_dst_list=interface.nat_dst_address_list,
            nat_dst_negate=bool(getattr(interface, "nat_dst_negate", True)),
            gateway=interface.gateway,
            route_table=interface.route_table,
            route_dst=interface.route_dst_address or "0.0.0.0/0",
            route_distance=interface.route_distance if interface.route_distance is not None else 1,
            listen_port=interface.listen_port,
            timeout=_WG_MT_APPLY_TIMEOUT,
            retries=_WG_MT_APPLY_RETRIES,
        )
        if isinstance(result, tuple):
            ok, route_applied = result[0], result[1]
        else:
            ok, route_applied = bool(result), False
        logger.info(
            "route_apply done interface_id=%s server=%s:%s ok=%s route_applied=%s",
            interface_id,
            server.host,
            server.port,
            ok,
            route_applied,
        )
        return ok, route_applied


async def apply_wg_automation_timed(
    interface_id: int,
) -> tuple[bool, str | None, bool]:
    """Apply with timeout and retries. Returns (ok, user_error_or_none, route_applied_on_router)."""
    from vpn_bot.mt_session import MikroTikCircuitOpenError
    from vpn_bot.utils import LanguageManager, MikroTikBusyError

    async with AsyncSessionLocal() as session:
        interface = await session.get(WireGuardInterface, interface_id)
        if not interface:
            return False, LanguageManager.get("common.error"), False
        pre_err_key = validate_wg_route_before_apply(interface)
        if pre_err_key:
            return False, LanguageManager.get(pre_err_key), False

    try:
        ok, route_applied = await apply_wg_automation(interface_id)
    except MikroTikCircuitOpenError as exc:
        secs = max(1, int(exc.seconds_remaining))
        return (
            False,
            LanguageManager.get("admin.wg.mt_router_cooldown", seconds=secs),
            False,
        )
    except asyncio.TimeoutError:
        logger.error("apply_wg_automation timed out for interface_id=%s", interface_id)
        return False, LanguageManager.get("admin.wg.mt_apply_timeout"), False
    except MikroTikBusyError:
        logger.error("apply_wg_automation semaphore busy for interface_id=%s", interface_id)
        return False, LanguageManager.get("admin.wg.mt_list_busy"), False
    except Exception as exc:
        logger.error("apply_wg_automation failed for interface_id=%s: %s", interface_id, exc)
        err_text = str(exc).lower()
        if any(tok in err_text for tok in ("login", "authentication", "denied", "password")):
            return False, LanguageManager.get("admin.wg.mt_apply_failed"), False
        return False, LanguageManager.get("admin.wg.mt_login_failed_transient"), False

    if not ok:
        return False, LanguageManager.get("admin.wg.mt_apply_failed"), False

    warn_key = None
    async with AsyncSessionLocal() as session:
        interface = await session.get(WireGuardInterface, interface_id)
        if interface and wg_route_configured_on_interface(interface) and not route_applied:
            warn_key = "admin.wg.route_not_applied_on_router"

    if warn_key:
        return True, LanguageManager.get(warn_key), False
    return True, None, route_applied


async def delete_wg_interface_on_router_timed(server, iface_name: str) -> tuple[bool, str | None]:
    """Remove WG interface from MikroTik in one API session (cleanup + delete)."""
    from vpn_bot.mikrotik_manager import get_mikrotik_manager
    from vpn_bot.mt_session import MikroTikCircuitOpenError, run_mikrotik_for_server
    from vpn_bot.utils import LanguageManager

    mgr = get_mikrotik_manager(server)
    try:
        ok = await run_mikrotik_for_server(
            server,
            mgr.remove_wg_interface_complete,
            iface_name,
            timeout=_WG_MT_APPLY_TIMEOUT,
            retries=_WG_MT_APPLY_RETRIES,
        )
    except MikroTikCircuitOpenError as exc:
        secs = max(1, int(exc.seconds_remaining))
        return False, LanguageManager.get("admin.wg.mt_router_cooldown", seconds=secs)
    except asyncio.TimeoutError:
        logger.error("remove_wg_interface_complete timed out for %s on %s", iface_name, server.host)
        return False, LanguageManager.get("admin.wg.mt_apply_timeout")
    except Exception as exc:
        logger.error("remove_wg_interface_complete failed for %s: %s", iface_name, exc)
        return False, LanguageManager.get("admin.wg.router_delete_failed")

    if not ok:
        return False, LanguageManager.get("admin.wg.router_delete_failed")
    return True, None


async def fetch_upstream_interfaces_timed(server) -> tuple[list | None, str | None]:
    """Fetch upstream list; None list + error on failure/timeout."""
    from vpn_bot.mikrotik_manager import get_mikrotik_list_reader
    from vpn_bot.mt_session import MikroTikCircuitOpenError, run_mikrotik_for_server
    from vpn_bot.utils import LanguageManager

    mgr = get_mikrotik_list_reader(server)
    timeout = float(__import__("os").getenv("WG_MT_LIST_TIMEOUT", "15"))
    try:
        result = await run_mikrotik_for_server(
            server,
            mgr.get_upstream_interfaces,
            timeout=timeout,
            retries=2,
        )
        if result is None:
            return None, LanguageManager.get("admin.wg.mt_list_failed")
        return result, None
    except MikroTikCircuitOpenError as exc:
        secs = max(1, int(exc.seconds_remaining))
        return None, LanguageManager.get("admin.wg.mt_router_cooldown", seconds=secs)
    except asyncio.TimeoutError:
        logger.error("get_upstream_interfaces timed out for %s", server.host)
        return None, LanguageManager.get("admin.wg.mt_list_timeout")
    except Exception as exc:
        logger.error("get_upstream_interfaces failed for %s: %s", server.host, exc)
        return None, LanguageManager.get("admin.wg.mt_list_failed")


async def fetch_address_list_names_timed(server) -> tuple[list | None, str | None]:
    from vpn_bot.mikrotik_manager import get_mikrotik_list_reader
    from vpn_bot.mt_session import MikroTikCircuitOpenError, run_mikrotik_for_server
    from vpn_bot.utils import LanguageManager

    mgr = get_mikrotik_list_reader(server)
    timeout = float(__import__("os").getenv("WG_MT_LIST_TIMEOUT", "15"))
    try:
        result = await run_mikrotik_for_server(
            server,
            mgr.get_firewall_address_list_names,
            timeout=timeout,
            retries=2,
        )
        if result is None:
            return None, LanguageManager.get("admin.wg.mt_list_failed")
        return result, None
    except MikroTikCircuitOpenError as exc:
        secs = max(1, int(exc.seconds_remaining))
        return None, LanguageManager.get("admin.wg.mt_router_cooldown", seconds=secs)
    except asyncio.TimeoutError:
        return None, LanguageManager.get("admin.wg.mt_list_timeout")
    except Exception as exc:
        logger.error("get_firewall_address_list_names failed: %s", exc)
        return None, LanguageManager.get("admin.wg.mt_list_failed")


async def fetch_routing_tables_timed(server) -> tuple[list | None, str | None]:
    from vpn_bot.mikrotik_manager import get_mikrotik_list_reader
    from vpn_bot.mt_session import MikroTikCircuitOpenError, run_mikrotik_for_server
    from vpn_bot.utils import LanguageManager

    mgr = get_mikrotik_list_reader(server)
    timeout = float(__import__("os").getenv("WG_MT_LIST_TIMEOUT", "15"))
    logger.info(
        "route_table_fetch start server=%s:%s timeout=%.0fs",
        server.host,
        server.port,
        timeout,
    )
    try:
        result = await run_mikrotik_for_server(
            server,
            mgr.get_routing_tables,
            timeout=timeout,
            retries=2,
        )
        if result is None:
            logger.error("route_table_fetch failed (null) server=%s:%s", server.host, server.port)
            return None, LanguageManager.get("admin.wg.mt_list_failed")
        logger.info(
            "route_table_fetch ok server=%s:%s count=%d",
            server.host,
            server.port,
            len(result),
        )
        return result, None
    except MikroTikCircuitOpenError as exc:
        secs = max(1, int(exc.seconds_remaining))
        return None, LanguageManager.get("admin.wg.mt_router_cooldown", seconds=secs)
    except asyncio.TimeoutError:
        logger.error("route_table_fetch timeout server=%s:%s", server.host, server.port)
        return None, LanguageManager.get("admin.wg.mt_list_timeout")
    except Exception as exc:
        logger.error("route_table_fetch failed server=%s:%s: %s", server.host, server.port, exc)
        return None, LanguageManager.get("admin.wg.mt_list_failed")


async def check_and_preemptively_create_interfaces():
    """Proactively create new WG interfaces when existing ones are nearly full.
    
    For each server, checks if any active interface has remaining capacity
    below the threshold. If so, creates a new interface with full inheritance.
    Returns a list of newly created interface names.
    """
    from vpn_bot.mikrotik_manager import get_mikrotik_manager
    from vpn_bot.admin_settings import get_admin_setting
    
    threshold = int(await get_admin_setting('wg_preemptive_threshold', 5))
    created = []
    
    async with AsyncSessionLocal() as session:
        # Get all active servers that have WG interfaces
        ifaces_res = await session.execute(
            select(WireGuardInterface).where(WireGuardInterface.is_active)
        )
        all_ifaces = ifaces_res.scalars().all()
        
        if not all_ifaces:
            return created
        
        # Group by server
        servers = {}
        for iface in all_ifaces:
            if iface.server_id not in servers:
                servers[iface.server_id] = []
            servers[iface.server_id].append(iface)
        
        for server_id, ifaces in servers.items():
            # Check if ALL interfaces on this server have low remaining capacity
            # We only need to pre-create if there's NO interface with sufficient capacity
            has_sufficient = False
            for iface in ifaces:
                active = await count_wg_interface_active_subs(session, iface.id)
                remaining = iface.max_users - active
                if remaining > threshold:
                    has_sufficient = True
                    break
            
            if has_sufficient:
                continue  # This server has enough capacity, skip
            
            # All interfaces are nearly full — create a new one
            server = await session.get(Server, server_id)
            if not server:
                continue
                
            try:
                mgr = get_mikrotik_manager(server)
                
                # Find available params
                wg_params = await asyncio.to_thread(mgr.find_available_wg_interface_params)
                if not wg_params:
                    logger.warning(f"No available WG params for server {server.name}")
                    continue
                
                # Create on MikroTik
                keys = await asyncio.to_thread(
                    mgr.create_wg_interface,
                    wg_params['name'], wg_params['listen_port'], wg_params['address']
                )
                if not keys:
                    logger.error(f"Failed to create WG interface on {server.name}")
                    continue
                
                # Inherit from first active interface on server (lowest id, same as purchase path)
                parent = min(ifaces, key=lambda i: i.id)
                new_iface = WireGuardInterface(
                    server_id=server_id,
                    name=wg_params['name'],
                    public_key=keys['public_key'],
                    private_key=keys.get('private_key', 'managed-by-router'),
                    address=wg_params['address'],
                    listen_port=keys.get('listen_port', wg_params['listen_port']),
                    dns=parent.dns,
                    mtu=parent.mtu,
                    keepalive=parent.keepalive,
                    max_users=parent.max_users,
                    upstream_interface=parent.upstream_interface,
                    routing_mark=parent.routing_mark,
                    nat_routing_mark=parent.nat_routing_mark,
                    nat_dst_address=parent.nat_dst_address,
                    nat_dst_address_list=parent.nat_dst_address_list,
                    nat_dst_negate=parent.nat_dst_negate,
                    gateway=parent.gateway,
                    route_table=parent.route_table,
                    route_dst_address=parent.route_dst_address,
                    route_distance=parent.route_distance,
                    endpoint_host=parent.endpoint_host,
                    is_active=True
                )
                session.add(new_iface)
                await session.flush()
                
                from vpn_bot.mt_session import run_mikrotik_for_server

                await run_mikrotik_for_server(
                    server,
                    mgr.sync_wg_interface_automation,
                    name=new_iface.name,
                    address=new_iface.address,
                    upstream=new_iface.upstream_interface,
                    routing_mark=new_iface.routing_mark,
                    nat_routing_mark=new_iface.nat_routing_mark,
                    nat_dst=new_iface.nat_dst_address or "127.0.0.1",
                    nat_dst_list=new_iface.nat_dst_address_list,
                    nat_dst_negate=bool(getattr(new_iface, "nat_dst_negate", True)),
                    gateway=new_iface.gateway,
                    route_table=new_iface.route_table,
                    route_dst=new_iface.route_dst_address or "0.0.0.0/0",
                    route_distance=new_iface.route_distance if new_iface.route_distance is not None else 1,
                    listen_port=new_iface.listen_port,
                    timeout=_WG_MT_APPLY_TIMEOUT,
                    retries=_WG_MT_APPLY_RETRIES,
                )
                
                await session.commit()
                created.append(new_iface.name)
                logger.info(f"Proactively created WG interface {new_iface.name} on {server.name} "
                           f"(threshold={threshold}, inherited from {parent.name})")
                
            except Exception as e:
                logger.error(f"Proactive WG interface creation failed on {server.name}: {e}")
                await session.rollback()
    
    return created

async def delete_wg_interface(interface_id: int):
    """Delete WG interface record and disable associated subscriptions."""
    from sqlalchemy import update as sqlalchemy_update
    async with AsyncSessionLocal() as session:
        interface = await session.get(WireGuardInterface, interface_id)
        if not interface: return False, "Interface not found"
        
        # 1. Disable all associated subs in DB (Soft delete/disable)
        await session.execute(
            sqlalchemy_update(WireGuardSubscription)
            .where(WireGuardSubscription.interface_id == interface_id)
            .values(status='disabled')
        )
        
        # 2. Delete interface record
        await session.delete(interface)
        await session.commit()
        return True, "Deleted"
async def create_wg_interface(data: dict):
    """Create a new WG interface record in DB."""
    async with AsyncSessionLocal() as session:
        new_iface = WireGuardInterface(
            server_id=data['server_id'],
            name=data['name'],
            public_key=data['public_key'],
            private_key=data.get('private_key', 'managed-by-router'),
            address=data['address'],
            listen_port=data['listen_port'],
            dns=data['dns'],
            endpoint_host=data['endpoint_host'],
            mtu=data['mtu'],
            keepalive=data['keepalive'],
            upstream_interface=data.get('upstream_interface'),
            routing_mark=data.get('routing_mark'),
            nat_routing_mark=data.get('nat_routing_mark'),
            nat_dst_address=data.get('nat_dst_address'),
            nat_dst_address_list=data.get('nat_dst_address_list'),
            nat_dst_negate=bool(data.get('nat_dst_negate', True)),
            gateway=data.get('gateway'),
            route_table=data.get('route_table'),
            route_dst_address=data.get('route_dst_address', '0.0.0.0/0'),
            route_distance=int(data.get('route_distance', 1)),
            is_active=True
        )
        session.add(new_iface)
        await session.commit()
        await session.refresh(new_iface)
        return new_iface

async def get_wg_subscription_count():
    """Count all active WG subscriptions."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(func.count(WireGuardSubscription.id)))
        return result.scalar() or 0

async def find_wg_subscription_by_query(query_text: str):
    """Search for a WireGuard subscription by UID or Assigned IP."""
    from sqlalchemy.orm import joinedload
    async with AsyncSessionLocal() as session:
        stmt = select(WireGuardSubscription).options(joinedload(WireGuardSubscription.interface))
        
        # 1. Search by Unique Identifier
        res = await session.execute(stmt.where(WireGuardSubscription.unique_identifier == query_text))
        wg_sub = res.scalars().first()
        
        # 2. Search by IP
        if not wg_sub:
            res = await session.execute(stmt.where(WireGuardSubscription.assigned_ip == query_text))
            wg_sub = res.scalars().first()
            
        if not wg_sub:
            return None, None
            
        server = await session.get(Server, wg_sub.interface.server_id)
        return wg_sub, server
