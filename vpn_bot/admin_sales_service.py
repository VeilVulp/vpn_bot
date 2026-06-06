
from sqlalchemy import func, or_, select
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Subscription, WireGuardSubscription
from vpn_bot.settings_utils import get_admin_setting, set_admin_setting
from vpn_bot.utils import LanguageManager, utc_now
import logging

ProtocolKey = str  # 'ovpn' | 'wg'

logger = logging.getLogger("vpn_bot.admin_sales")


def coerce_sales_limit(raw) -> int:
    """Normalize sales_*_limit settings to a non-negative int (0 = unlimited)."""
    try:
        return max(0, int(raw or 0))
    except (TypeError, ValueError):
        return 0


def coerce_sales_bool(raw, default: bool = True) -> bool:
    """Normalize sales_*_active settings from DB strings or JSON."""
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in ("true", "1", "yes")
    return bool(raw)


def sales_capacity_remaining(count: int, limit: int) -> int | str:
    """Slots left for new purchases; unlimited when limit <= 0."""
    if limit <= 0:
        return "∞"
    return max(0, limit - count)


def _active_sales_slot_filter(model, now):
    """Active status plus not past expiry (stale actives excluded from cap)."""
    return (
        model.status == "active",
        or_(model.expiry_date.is_(None), model.expiry_date > now),
    )


async def count_active_sales_slots(protocol: ProtocolKey) -> int:
    """Count active, non-expired subscriptions for sales capacity (global per protocol)."""
    now = utc_now()
    async with AsyncSessionLocal() as session:
        if protocol == "ovpn":
            status_clause, expiry_clause = _active_sales_slot_filter(Subscription, now)
            return (
                await session.execute(
                    select(func.count(Subscription.id)).where(status_clause, expiry_clause)
                )
            ).scalar() or 0
        status_clause, expiry_clause = _active_sales_slot_filter(WireGuardSubscription, now)
        return (
            await session.execute(
                select(func.count(WireGuardSubscription.id)).where(status_clause, expiry_clause)
            )
        ).scalar() or 0


async def get_sales_capacity_limit(protocol: ProtocolKey) -> int:
    """0 = unlimited."""
    key = f"sales_{protocol}_limit"
    return coerce_sales_limit(await get_admin_setting(key, 0))


async def is_new_purchase_capacity_available(protocol: ProtocolKey) -> tuple[bool, int, int]:
    """
    Sales cap applies to NEW purchases only (not renewals).
    Returns (allowed, current_active_count, limit); limit 0 means unlimited.
    """
    limit = await get_sales_capacity_limit(protocol)
    count = await count_active_sales_slots(protocol)
    if limit <= 0:
        return True, count, 0
    allowed = count < limit
    if not allowed:
        logger.info(
            "purchase_blocked protocol=%s reason=capacity count=%s limit=%s",
            protocol,
            count,
            limit,
        )
    return allowed, count, limit


async def get_sales_capacity_block_message() -> str:
    return await get_admin_setting(
        "sales_full_msg",
        LanguageManager.get("admin.sales.capacity_full_default"),
    )


async def assert_new_purchase_capacity(protocol: ProtocolKey) -> tuple[bool, str | None]:
    """Gate for checkout / buy menu. Renewals must NOT call this."""
    global_active = coerce_sales_bool(await get_admin_setting("sales_global_active", True))
    if not global_active:
        msg = await get_admin_setting(
            "sales_global_msg",
            LanguageManager.get("admin.sales.global_disabled_default"),
        )
        return False, msg

    proto_key = f"sales_{protocol}_active"
    proto_active = coerce_sales_bool(await get_admin_setting(proto_key, True))
    if not proto_active:
        msg = await get_admin_setting(
            f"sales_{protocol}_msg",
            LanguageManager.get(f"admin.sales.{protocol}_disabled_default"),
        )
        return False, msg

    allowed, _count, _limit = await is_new_purchase_capacity_available(protocol)
    if allowed:
        return True, None
    return False, await get_sales_capacity_block_message()


async def protocol_key_from_limit_setting(setting_key: str) -> ProtocolKey | None:
    if setting_key == "sales_ovpn_limit":
        return "ovpn"
    if setting_key == "sales_wg_limit":
        return "wg"
    return None


async def apply_sales_capacity_delta(protocol: ProtocolKey, delta: int) -> int:
    """Set limit to current active count + delta; returns new limit."""
    count = await count_active_sales_slots(protocol)
    new_limit = max(0, count + delta)
    key = f"sales_{protocol}_limit"
    await set_admin_setting(key, new_limit)
    return new_limit


async def get_sales_dashboard_data():
    """Fetch all settings and counts for the sales dashboard."""
    global_active = coerce_sales_bool(await get_admin_setting("sales_global_active", True))
    ovpn_active = coerce_sales_bool(await get_admin_setting("sales_ovpn_active", True))
    wg_active = coerce_sales_bool(await get_admin_setting("sales_wg_active", True))

    ovpn_limit = await get_sales_capacity_limit("ovpn")
    wg_limit = await get_sales_capacity_limit("wg")

    ovpn_count = await count_active_sales_slots("ovpn")
    wg_count = await count_active_sales_slots("wg")

    return {
        "global_active": global_active,
        "ovpn_active": ovpn_active,
        "wg_active": wg_active,
        "ovpn_limit": ovpn_limit,
        "wg_limit": wg_limit,
        "ovpn_count": ovpn_count,
        "wg_count": wg_count,
        "ovpn_remaining": sales_capacity_remaining(ovpn_count, ovpn_limit),
        "wg_remaining": sales_capacity_remaining(wg_count, wg_limit),
    }


async def toggle_sales_status(key: str):
    """Toggle a specific sales setting and handle synchronization logic."""
    setting_key = key.replace("sales_toggle_", "sales_") + "_active"

    current = coerce_sales_bool(await get_admin_setting(setting_key, True))
    new_state = not current
    await set_admin_setting(setting_key, new_state)

    if setting_key == "sales_global_active":
        await set_admin_setting("sales_ovpn_active", new_state)
        await set_admin_setting("sales_wg_active", new_state)
    else:
        ovpn_s = coerce_sales_bool(await get_admin_setting("sales_ovpn_active", True))
        wg_s = coerce_sales_bool(await get_admin_setting("sales_wg_active", True))
        await set_admin_setting("sales_global_active", ovpn_s or wg_s)

    return new_state


async def get_renewal_dashboard_data():
    """Fetch settings for the renewal management menu."""
    um_renew = await get_admin_setting("sales_um_renew_active", True)
    wg_renew = await get_admin_setting("sales_wg_renew_active", True)
    window = await get_admin_setting("sales_renew_window_days", "3d")
    strict_window = await get_admin_setting("sales_renew_strict_window", True)

    if isinstance(window, int):
        window = f"{window}d"

    waiting_um = 0
    waiting_wg = 0
    async with AsyncSessionLocal() as session:
        waiting_um = (
            await session.execute(
                select(func.count(Subscription.id)).where(
                    Subscription.status.in_(("expired", "disabled"))
                )
            )
        ).scalar() or 0
        waiting_wg = (
            await session.execute(
                select(func.count(WireGuardSubscription.id)).where(
                    WireGuardSubscription.status.in_(("expired", "disabled"))
                )
            )
        ).scalar() or 0

    return {
        "um_renew": um_renew,
        "wg_renew": wg_renew,
        "window": window,
        "strict_window": strict_window,
        "waiting_um": waiting_um,
        "waiting_wg": waiting_wg,
    }


async def toggle_renewal_status(proto: str):
    """Toggle renewal status for a protocol."""
    key = f"sales_{proto}_renew_active"
    current = await get_admin_setting(key, True)
    new_state = not current
    await set_admin_setting(key, new_state)
    return new_state


async def broadcast_renewal_notification(bot, proto: str):
    """Notify all eligible users about renewal opening."""
    custom_msg = await get_admin_setting(
        "sales_renew_notification_msg",
        LanguageManager.get("admin.sales.renew_notify_msg_default"),
    )

    count = 0
    async with AsyncSessionLocal() as session:
        if proto == "um":
            res = await session.execute(
                select(Subscription.user_id).where(
                    Subscription.status.in_(["expired", "inconsistent", "disabled"])
                ).distinct()
            )
        else:
            res = await session.execute(
                select(WireGuardSubscription.user_id).where(
                    WireGuardSubscription.status.in_(["expired", "inconsistent", "disabled"])
                ).distinct()
            )

        user_ids = res.scalars().all()
        for uid in user_ids:
            try:
                await bot.send_message(chat_id=uid, text=custom_msg, parse_mode="Markdown")
                count += 1
            except Exception as e:
                logging.debug(f"Failed to notify user {uid}: {e}")

    return count
