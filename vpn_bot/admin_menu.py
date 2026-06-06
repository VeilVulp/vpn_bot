"""
Admin panel menu layout — single source of truth for keyboards and callback tree.

Callback data values are stable; only row grouping and labels change for UX.
"""

from __future__ import annotations

import time
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup

# Max visible label length per button when two share one row (Persian/mobile).
_INLINE_PAIR_MAX_LEN = 16

from vpn_bot.admin_management import has_admin_perm, is_super_admin
from vpn_bot.admin_permissions import (
    PERM_BACKUP,
    PERM_CLEANUP,
    PERM_CONFIG_CARDS,
    PERM_CONFIG_CURRENCY,
    PERM_CONFIG_LANG,
    PERM_CONFIG_MAINTENANCE,
    PERM_CONFIG_MESSAGES,
    PERM_CONFIG_PRESETS,
    PERM_CONFIG_SUBJECTS,
    PERM_CONFIG_SYNC,
    PERM_NOTIFY,
    PERM_OVPN,
    PERM_OVPN_CONNECTION,
    PERM_OVPN_MANAGE,
    PERM_OVPN_PROFILES,
    PERM_RECEIPTS,
    PERM_REPORTS,
    PERM_SALES,
    PERM_SERVERS,
    PERM_SHARED_USERS,
    PERM_TICKETS,
    PERM_USER_MGMT,
    PERM_WG_ADD_INTERFACE,
    PERM_WG_INTERFACES,
    PERM_WG_PLANS,
    PERM_WG_USERS,
    permission_for_callback,
)
from vpn_bot.admin_receipt_service import count_pending_receipts
from vpn_bot.admin_ticket_service import count_tickets_needing_admin
from vpn_bot.utils import LanguageManager

# Documented navigation tree (parent -> children callbacks).
MENU_TREE: dict[str, list[str]] = {
    "admin_start": [
        "search_user",
        "pending_receipts",
        "admin_tickets",
        "ovpn_l2tp_mgmt_menu",
        "wg_mgmt_menu",
        "sales_mgmt_menu",
        "admin_reports",
        "list_servers",
        "bot_config_menu",
        "shared_users_menu",
        "notification_menu",
        "backup_menu",
        "clean_db_menu",
        "admin_mgmt_menu",  # super admin only
    ],
    "ovpn_l2tp_mgmt_menu": ["list_profiles", "settings_connection", "manage_ovpn"],
    "wg_mgmt_menu": [
        "list_wg_profiles",
        "list_wg_interfaces",
        "add_wg_interface_start",
        "list_wg_users",
    ],
    "sales_mgmt_menu": [
        "sales_toggle_global",
        "sales_capacity_menu",
        "renew_mgmt_menu",
        "discount_codes_menu",
    ],
    "bot_config_menu": [
        "settings_cards",
        "settings_messages",
        "settings_presets",
        "settings_subjects",
        "settings_lang",
        "settings_currency",
        "settings_sync",
        "admin_maintenance_menu",
    ],
    "admin_tickets": [
        "admin_tickets_active",
        "admin_tickets_closed",
        "admin_tickets_search",
        "admin_tickets_create",
        "admin_tickets_notif_mode",
    ],
    "backup_menu": ["backup_set_interval", "backup_export", "backup_import"],
    "notification_menu": ["notify_broadcast", "notify_targeted"],
    "clean_db_menu": [
        "clean_expired_subs",
        "clean_expired_wg_subs",
        "clean_pending_receipts",
        "clean_old_transactions",
        "clean_closed_tickets",
        "clean_inactive_users",
        "clean_mt_orphans",
        "clear_mt_sessions",
        "clean_settings_menu",
    ],
    "shared_users_menu": ["set_default_shared", "edit_shared_user"],
}

# Expected parent menu for back buttons in multi-step flows.
NAV_PARENT: dict[str, str] = {
    "search_user": "admin_start",
    "backup_set_interval": "backup_menu",
    "backup_import": "backup_menu",
    "admin_mgmt_add": "admin_mgmt_menu",
    "shared_users_menu": "admin_start",
    "admin_tickets_create": "admin_tickets",
    "admin_tickets_reply": "admin_tickets",
}

_INBOX_CACHE_TTL_SEC = 30
_inbox_cache: dict[str, Any] = {"ts": 0.0, "receipts": 0, "tickets": 0}


async def get_admin_inbox_counts(*, force_refresh: bool = False) -> dict[str, int]:
    """Pending receipts and tickets needing admin; cached briefly for menu builds."""
    now = time.monotonic()
    if (
        not force_refresh
        and now - _inbox_cache["ts"] < _INBOX_CACHE_TTL_SEC
    ):
        return {"receipts": _inbox_cache["receipts"], "tickets": _inbox_cache["tickets"]}

    receipts = await count_pending_receipts()
    ticket_count = await count_tickets_needing_admin()
    _inbox_cache.update(ts=now, receipts=receipts, tickets=ticket_count)
    return {"receipts": receipts, "tickets": ticket_count}


def invalidate_inbox_cache() -> None:
    """Force admin menu badge counts to refresh after receipt/ticket changes."""
    _inbox_cache["ts"] = 0.0


def admin_button_label(base_key: str, count: int = 0, *, short: bool = False) -> str:
    """Resolve label; use ``{base_key}_short`` when requested and defined."""
    key = f"{base_key}_short" if short else base_key
    label = LanguageManager.get(key)
    if short and label.startswith("[") and label.endswith("]"):
        label = LanguageManager.get(base_key)
    if count > 0:
        return LanguageManager.get("admin.btn_with_count", label=label, count=count)
    return label


def _btn_label(base_key: str, count: int = 0) -> str:
    return admin_button_label(base_key, count, short=False)


def _fits_inline_pair(label: str) -> bool:
    return len(label) <= _INLINE_PAIR_MAX_LEN


def inline_button(
    label_key: str,
    callback_data: str,
    *,
    count: int = 0,
    short: bool = True,
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        admin_button_label(label_key, count, short=short),
        callback_data=callback_data,
    )


def keyboard_rows_single(
    items: list[tuple[str, str]],
    *,
    short: bool = True,
) -> list[list[InlineKeyboardButton]]:
    """One full-width button per row (best for Persian mobile)."""
    return [[inline_button(label_key, callback_data, short=short)] for label_key, callback_data in items]


def keyboard_row_pair(
    key1: str,
    cb1: str,
    key2: str,
    cb2: str,
    *,
    count1: int = 0,
    count2: int = 0,
) -> list[list[InlineKeyboardButton]]:
    """One row with two buttons, or two full-width rows if labels are too long."""
    l1 = admin_button_label(key1, count1, short=True)
    l2 = admin_button_label(key2, count2, short=True)
    b1 = InlineKeyboardButton(l1, callback_data=cb1)
    b2 = InlineKeyboardButton(l2, callback_data=cb2)
    if _fits_inline_pair(l1) and _fits_inline_pair(l2):
        return [[b1, b2]]
    return [[b1], [b2]]


def format_admin_menu_title() -> str:
    return LanguageManager.get("admin.menu_title") + LanguageManager.get("admin.menu_sections_hint")


async def _can_show_callback(user_id: int, callback_data: str) -> bool:
    if await is_super_admin(user_id):
        if callback_data == "clean_db_menu":
            return True
        return True
    perm = permission_for_callback(callback_data)
    if callback_data == "clean_db_menu":
        return False
    if not perm:
        return True
    return await has_admin_perm(user_id, perm)


async def _filter_pair_row(
    user_id: int,
    key1: str,
    cb1: str,
    key2: str,
    cb2: str,
    *,
    count1: int = 0,
    count2: int = 0,
) -> list[list[InlineKeyboardButton]]:
    show1 = await _can_show_callback(user_id, cb1)
    show2 = await _can_show_callback(user_id, cb2)
    if show1 and show2:
        return keyboard_row_pair(key1, cb1, key2, cb2, count1=count1, count2=count2)
    rows: list[list[InlineKeyboardButton]] = []
    if show1:
        rows.append([inline_button(key1, cb1, count=count1)])
    if show2:
        rows.append([inline_button(key2, cb2, count=count2)])
    return rows


async def build_admin_main_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """Priority-ordered main admin menu filtered by RBAC."""
    counts = await get_admin_inbox_counts()
    keyboard: list[list[InlineKeyboardButton]] = []

    if await _can_show_callback(user_id, "search_user"):
        keyboard.append([inline_button("admin.btn_user_mgmt", "search_user", short=False)])

    keyboard.extend(
        await _filter_pair_row(
            user_id,
            "admin.btn_receipts",
            "pending_receipts",
            "admin.btn_tickets",
            "admin_tickets",
            count1=counts["receipts"],
            count2=counts["tickets"],
        )
    )
    keyboard.extend(
        await _filter_pair_row(
            user_id,
            "admin.btn_ovpn_l2tp_mgmt",
            "ovpn_l2tp_mgmt_menu",
            "admin.btn_wg_mgmt",
            "wg_mgmt_menu",
        )
    )
    keyboard.extend(
        await _filter_pair_row(
            user_id,
            "admin.btn_sales_mgmt",
            "sales_mgmt_menu",
            "admin.btn_reports",
            "admin_reports",
        )
    )
    keyboard.extend(
        await _filter_pair_row(
            user_id,
            "admin.btn_servers",
            "list_servers",
            "admin.btn_bot_config",
            "bot_config_menu",
        )
    )
    keyboard.extend(
        await _filter_pair_row(
            user_id,
            "admin.btn_shared_users",
            "shared_users_menu",
            "admin.btn_notify",
            "notification_menu",
        )
    )
    backup_clean = await _filter_pair_row(
        user_id,
        "admin.btn_backup",
        "backup_menu",
        "admin.btn_clean",
        "clean_db_menu",
    )
    keyboard.extend(backup_clean)

    if await is_super_admin(user_id):
        keyboard.append([inline_button("admin.btn_admin_mgmt", "admin_mgmt_menu", short=True)])

    if not keyboard:
        keyboard.append(
            [InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="admin_start")]
        )

    return InlineKeyboardMarkup(keyboard)


async def build_wg_mgmt_keyboard(user_id: int) -> InlineKeyboardMarkup:
    """WireGuard admin submenu filtered by RBAC."""
    items: list[tuple[str, str]] = []
    if await has_admin_perm(user_id, PERM_WG_PLANS):
        items.append(("admin.wg.btn_plans", "list_wg_profiles"))
    if await has_admin_perm(user_id, PERM_WG_USERS):
        items.append(("admin.wg.btn_users", "list_wg_users"))
    if await has_admin_perm(user_id, PERM_WG_INTERFACES):
        items.append(("admin.wg.btn_interfaces", "list_wg_interfaces"))
    if await has_admin_perm(user_id, PERM_WG_ADD_INTERFACE):
        items.append(("admin.wg.btn_add_interface", "add_wg_interface_start"))
    keyboard = keyboard_rows_single(items) if items else []
    keyboard.append(
        [InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="admin_start")]
    )
    return InlineKeyboardMarkup(keyboard)


async def build_bot_config_keyboard(user_id: int) -> InlineKeyboardMarkup:
    keyboard: list[list[InlineKeyboardButton]] = []
    keyboard.extend(
        await _filter_pair_row(
            user_id,
            "admin.settings.btn_cards",
            "settings_cards",
            "admin.settings.btn_presets",
            "settings_presets",
        )
    )
    keyboard.extend(
        await _filter_pair_row(
            user_id,
            "admin.settings.btn_msgs",
            "settings_messages",
            "admin.settings.btn_subjects",
            "settings_subjects",
        )
    )
    if await has_admin_perm(user_id, PERM_CONFIG_MESSAGES):
        keyboard.append(
            [InlineKeyboardButton(LanguageManager.get("admin.settings.btn_terms"), callback_data="settings_purchase_terms")]
        )
    keyboard.extend(
        await _filter_pair_row(
            user_id,
            "admin.settings.btn_lang",
            "settings_lang",
            "admin.settings.btn_currency",
            "settings_currency",
        )
    )
    if await has_admin_perm(user_id, PERM_CONFIG_SYNC):
        keyboard.append([inline_button("admin.settings.btn_sync", "settings_sync", short=True)])
    if await has_admin_perm(user_id, PERM_CONFIG_MAINTENANCE):
        keyboard.append(
            [inline_button("admin.settings.btn_maintenance", "admin_maintenance_menu", short=True)]
        )
    keyboard.append(
        [InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="admin_start")]
    )
    return InlineKeyboardMarkup(keyboard)


async def build_clean_db_keyboard(user_id: int) -> InlineKeyboardMarkup:
    if not await is_super_admin(user_id):
        return InlineKeyboardMarkup(
            [[InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="admin_start")]]
        )
    keyboard = [
        *keyboard_row_pair(
            "admin.clean.btn_subs", "clean_expired_subs",
            "admin.clean.btn_wg_subs", "clean_expired_wg_subs",
        ),
        *keyboard_row_pair(
            "admin.clean.btn_rcpt", "clean_pending_receipts",
            "admin.clean.btn_tx", "clean_old_transactions",
        ),
        *keyboard_row_pair(
            "admin.clean.btn_tickets", "clean_closed_tickets",
            "admin.clean.btn_users", "clean_inactive_users",
        ),
        *keyboard_row_pair(
            "admin.clean.btn_orphans", "clean_mt_orphans",
            "admin.clean.btn_sessions", "clear_mt_sessions",
        ),
        [inline_button("admin.clean.btn_settings", "clean_settings_menu", short=True)],
        [
            InlineKeyboardButton(LanguageManager.get("common.refresh"), callback_data="clean_db_menu"),
            InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="admin_start"),
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def build_admin_back_markup(back_callback_data: str = "admin_start") -> InlineKeyboardMarkup:
    """Single-row inline back button for admin text-input steps."""
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(LanguageManager.get("common.back"), callback_data=back_callback_data)]]
    )


def build_notification_back_markup() -> InlineKeyboardMarkup:
    return build_admin_back_markup("notification_menu")
