
import asyncio
from sqlalchemy import select
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Server
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.utils import logger, LanguageManager
from telegram.helpers import escape_markdown

async def get_all_servers():
    """Fetch all servers from database."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Server))
        return result.scalars().all()


def is_mock_or_test_server(server: Server) -> bool:
    """True for pytest / placeholder servers — excluded from admin lists."""
    from vpn_bot.test_data import is_test_server_row

    return is_test_server_row(server)


async def get_servers_for_admin_list():
    """Production-like servers for admin list (active + inactive; excludes DBTEST / mock hosts)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Server).order_by(Server.id))
        servers = result.scalars().all()
    return [s for s in servers if not is_mock_or_test_server(s)]


def build_server_list_keyboard(servers, *, max_manage: int = 25) -> "InlineKeyboardMarkup":
    """Inline keyboard for server list: manage rows, add, back."""
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    from vpn_bot.utils import LanguageManager

    keyboard: list[list[InlineKeyboardButton]] = []
    for s in servers[:max_manage]:
        keyboard.append(
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.server.btn_manage", name=s.name),
                    callback_data=f"server_edit_{s.id}",
                )
            ]
        )
    keyboard.append(
        [InlineKeyboardButton(LanguageManager.get("admin.server.btn_add"), callback_data="server_add")]
    )
    keyboard.append(
        [InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="admin_start")]
    )
    return InlineKeyboardMarkup(keyboard)


async def get_active_servers():
    """Fetch active servers from database."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Server).where(Server.is_active))
        return result.scalars().all()

async def get_server_health_status(server: Server):
    """Check if a MikroTik server is reachable."""
    if is_mock_or_test_server(server):
        return False
    try:
        mgr = get_mikrotik_manager(server)
        # 3s timeout for quick check
        await asyncio.wait_for(asyncio.to_thread(mgr.connect), timeout=3.0)
        return True
    except Exception as e:
        logger.debug(f"Health check failed for {server.name}: {e}")
        return False

async def get_multi_server_health(servers):
    """Check health for multiple servers in parallel."""
    results = await asyncio.gather(*(get_server_health_status(s) for s in servers))
    return {s.id: status for s, status in zip(servers, results)}

async def format_server_list_text(servers, health_map, *, max_lines: int = 25):
    """Format the server list for display (truncates to stay under Telegram 4096 limit)."""
    text = LanguageManager.get("admin.server.list_title")
    online = sum(1 for s in servers if health_map.get(s.id))
    if len(servers) > max_lines:
        text += LanguageManager.get(
            "admin.server.list_summary",
            shown=max_lines,
            total=len(servers),
            online=online,
        )
    shown = servers[:max_lines]
    for s in shown:
        is_online = health_map.get(s.id, False)
        status_icon = "🔵" if is_online else "🔴"
        status_label = LanguageManager.get("status.active") if s.is_active else LanguageManager.get("status.disabled")
        health_label = "Online" if is_online else "Offline"

        safe_server_name = escape_markdown(s.name, version=1)
        text += (
            f"{status_icon} **{safe_server_name}** ({health_label})\n"
            f"Config: {status_label} | `{s.host}:{s.port}`\n"
            f"-------------------\n"
        )
    if len(servers) > max_lines:
        text += LanguageManager.get("admin.server.list_truncated", extra=len(servers) - max_lines)
    return text

async def get_server_by_id(server_id: int):
    """Fetch a server by ID."""
    async with AsyncSessionLocal() as session:
        return await session.get(Server, server_id)

def _apply_server_fields(server: Server, data: dict) -> None:
    """Apply dict to Server; route plaintext password through encrypting setter."""
    payload = dict(data)
    plain_password = payload.pop("password", None)
    for key, value in payload.items():
        if key == "_password":
            continue
        setattr(server, key, value)
    if plain_password is not None:
        server.password = plain_password


async def create_server(data: dict):
    """Create a new server entry."""
    async with AsyncSessionLocal() as session:
        server = Server()
        _apply_server_fields(server, data)
        session.add(server)
        await session.commit()
        await session.refresh(server)
        return server

async def update_server(server_id: int, data: dict):
    """Update server details."""
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            return False
        _apply_server_fields(server, data)
        await session.commit()
        return True

async def toggle_server_status(server_id: int):
    """Toggle is_active for a server."""
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server: return False, None
        server.is_active = not server.is_active
        await session.commit()
        return True, server.is_active

async def delete_server(server_id: int):
    """
    Delete server when no dependent rows exist.
    Returns (success, server_name_or_error_code).
    """
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            return False, "not_found"

        from vpn_bot.models import (
            Profile,
            Subscription,
            WireGuardInterface,
            WireGuardProfile,
        )

        if (
            await session.execute(
                select(Subscription.id).where(Subscription.server_id == server_id).limit(1)
            )
        ).scalar_one_or_none():
            return False, "blocked_dependencies"
        if (
            await session.execute(
                select(WireGuardInterface.id)
                .where(WireGuardInterface.server_id == server_id)
                .limit(1)
            )
        ).scalar_one_or_none():
            return False, "blocked_dependencies"
        if (
            await session.execute(
                select(Profile.id).where(Profile.server_id == server_id).limit(1)
            )
        ).scalar_one_or_none():
            return False, "blocked_dependencies"
        if (
            await session.execute(
                select(WireGuardProfile.id)
                .where(WireGuardProfile.server_id == server_id)
                .limit(1)
            )
        ).scalar_one_or_none():
            return False, "blocked_dependencies"

        name = server.name
        host_key = f"{server.host}:{server.port}:{server.username}"
        await session.delete(server)
        await session.commit()

    try:
        from vpn_bot.mt_cache import evict_manager_for_key

        evict_manager_for_key(host_key)
    except Exception:
        pass

    return True, name

async def get_server_upstream_interfaces(server_id: int):
    """Fetch available upstream interfaces from MikroTik for a server."""
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            return None
        
        mgr = get_mikrotik_manager(server)
        return await asyncio.to_thread(mgr.get_upstream_interfaces)

async def set_server_upstream_interface(server_id: int, iface_name: str):
    """Set the WireGuard upstream interface on MikroTik for a server."""
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            return False
        
        mgr = get_mikrotik_manager(server)
        return await asyncio.to_thread(mgr.set_wg_upstream_interface, iface_name)
