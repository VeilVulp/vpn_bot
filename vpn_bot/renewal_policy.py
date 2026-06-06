"""
Central renewal eligibility rules (user + admin).

Policy (when protocol renewal is enabled in admin):
- User may renew if subscription time expired OR data quota exhausted OR both.
- Renewal window (sales_renew_window_days) applies only when the account still has
  time AND data remaining (early renewal before expiry).
- Optional sales_renew_strict_window=false disables the early-window restriction entirely.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal, Tuple

from vpn_bot.settings_utils import get_admin_setting
from vpn_bot.utils import LanguageManager, parse_duration_to_seconds, utc_now

Protocol = Literal["um", "wg"]


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def ovpn_quota_exhausted(sub: Any) -> bool:
    limit = int(sub.total_limit_bytes or 0)
    if limit <= 0:
        return False
    used = int(sub.used_bytes or 0)
    if used >= limit:
        return True
    return sub.status == "disabled" and used >= limit * 0.95


def wg_quota_exhausted(sub: Any, profile: Any = None) -> bool:
    used = int(sub.total_bytes_rx or 0) + int(sub.total_bytes_tx or 0)
    cap = int(sub.bytes_remaining or 0)
    if cap > 0 and used >= cap:
        return True
    if profile and getattr(profile, "volume_gb", None):
        vol_bytes = int(profile.volume_gb) * 1024**3
        if vol_bytes > 0 and used >= vol_bytes:
            return True
    return sub.status == "disabled" and cap > 0 and used >= cap * 0.95


def subscription_time_expired(sub: Any, now: datetime | None = None) -> bool:
    now_a = _aware(now) or utc_now()
    exp = _aware(sub.expiry_date)
    if not exp:
        return False
    return exp < now_a


async def is_protocol_renewal_enabled(protocol: Protocol) -> bool:
    key = "sales_um_renew_active" if protocol == "um" else "sales_wg_renew_active"
    return bool(await get_admin_setting(key, True))


async def check_renewal_eligibility(
    sub: Any,
    protocol: Protocol,
    *,
    profile: Any = None,
    now: datetime | None = None,
) -> Tuple[bool, str | None]:
    """
    Return (allowed, user_message).
    user_message is ready-to-send Markdown/text when allowed is False.
    """
    if not await is_protocol_renewal_enabled(protocol):
        msg = await get_admin_setting(
            "sales_renew_disabled_msg",
            LanguageManager.get("admin.sales.renew_disabled_msg_default"),
        )
        return False, msg

    now = now or utc_now()
    expired = subscription_time_expired(sub, now)
    quota_full = (
        ovpn_quota_exhausted(sub) if protocol == "um" else wg_quota_exhausted(sub, profile)
    )

    strict_window = await get_admin_setting("sales_renew_strict_window", True)
    if not strict_window or expired or quota_full:
        return True, None

    window_raw = await get_admin_setting("sales_renew_window_days", "3d")
    window_seconds = parse_duration_to_seconds(
        window_raw if not isinstance(window_raw, int) else f"{window_raw}h"
    )
    exp = _aware(sub.expiry_date)
    if not exp:
        return True, None
    now_aware = _aware(now) or now
    if exp > now_aware:
        seconds_left = (exp - now_aware).total_seconds()
        if seconds_left > window_seconds:
            tpl = await get_admin_setting(
                "sales_renew_too_early_msg",
                LanguageManager.get("admin.sales.renew_too_early_msg_default"),
            )
            return False, tpl.format(days=window_raw)

    return True, None


async def should_offer_renewal_button(
    sub: Any,
    protocol: Protocol,
    *,
    profile: Any = None,
) -> bool:
    """Show renew button when renewal is enabled and user is eligible (or already needs renew)."""
    if not await is_protocol_renewal_enabled(protocol):
        return False
    expired = subscription_time_expired(sub)
    quota_full = (
        ovpn_quota_exhausted(sub) if protocol == "um" else wg_quota_exhausted(sub, profile)
    )
    if expired or quota_full:
        return True
    strict_window = await get_admin_setting("sales_renew_strict_window", True)
    if not strict_window:
        return True
    window_raw = await get_admin_setting("sales_renew_window_days", "3d")
    window_seconds = parse_duration_to_seconds(
        window_raw if not isinstance(window_raw, int) else f"{window_raw}h"
    )
    exp = _aware(sub.expiry_date)
    if not exp:
        return True
    now = utc_now()
    if exp > now:
        return (exp - now).total_seconds() <= window_seconds
    return True
