"""
Admin RBAC: permission keys, callback mapping, and access checks.
"""

from __future__ import annotations

import json
import logging
from functools import wraps
from typing import Callable, FrozenSet, Iterable, Literal

from sqlalchemy import select
from telegram import Update
from telegram.ext import ApplicationHandlerStop, ContextTypes

from vpn_bot.config import config
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Admin
from vpn_bot.utils import LanguageManager

logger = logging.getLogger("vpn_bot.admin_permissions")

# --- Permission keys (module + submenu) ---

PERM_USER_MGMT = "user_mgmt"
PERM_RECEIPTS = "receipts"
PERM_TICKETS = "tickets"
PERM_TICKETS_ACTIVE = "tickets.active"
PERM_TICKETS_CLOSED = "tickets.closed"
PERM_TICKETS_SEARCH = "tickets.search"
PERM_TICKETS_CREATE = "tickets.create"
PERM_TICKETS_NOTIF = "tickets.notif"
PERM_TICKETS_REPLY = "tickets.reply"
PERM_OVPN = "ovpn"
PERM_OVPN_PROFILES = "ovpn.profiles"
PERM_OVPN_CONNECTION = "ovpn.connection"
PERM_OVPN_MANAGE = "ovpn.manage"
PERM_WG_PLANS = "wg.plans"
PERM_WG_USERS = "wg.users"
PERM_WG_INTERFACES = "wg.interfaces"
PERM_WG_ADD_INTERFACE = "wg.add_interface"
PERM_SALES = "sales"
PERM_SALES_DISCOUNTS = "sales.discounts"
PERM_REPORTS = "reports"
PERM_SERVERS = "servers"
PERM_CONFIG_CARDS = "config.cards"
PERM_CONFIG_MESSAGES = "config.messages"
PERM_CONFIG_PRESETS = "config.presets"
PERM_CONFIG_SUBJECTS = "config.subjects"
PERM_CONFIG_LANG = "config.lang"
PERM_CONFIG_CURRENCY = "config.currency"
PERM_CONFIG_SYNC = "config.sync"
PERM_CONFIG_MAINTENANCE = "config.maintenance"
PERM_NOTIFY = "notify"
PERM_BACKUP = "backup"
PERM_CLEANUP = "cleanup"
PERM_SHARED_USERS = "shared_users"

ALL_PERMISSION_KEYS: FrozenSet[str] = frozenset({
    PERM_USER_MGMT,
    PERM_RECEIPTS,
    PERM_TICKETS,
    PERM_TICKETS_ACTIVE,
    PERM_TICKETS_CLOSED,
    PERM_TICKETS_SEARCH,
    PERM_TICKETS_CREATE,
    PERM_TICKETS_NOTIF,
    PERM_TICKETS_REPLY,
    PERM_OVPN,
    PERM_OVPN_PROFILES,
    PERM_OVPN_CONNECTION,
    PERM_OVPN_MANAGE,
    PERM_WG_PLANS,
    PERM_WG_USERS,
    PERM_WG_INTERFACES,
    PERM_WG_ADD_INTERFACE,
    PERM_SALES,
    PERM_SALES_DISCOUNTS,
    PERM_REPORTS,
    PERM_SERVERS,
    PERM_CONFIG_CARDS,
    PERM_CONFIG_MESSAGES,
    PERM_CONFIG_PRESETS,
    PERM_CONFIG_SUBJECTS,
    PERM_CONFIG_LANG,
    PERM_CONFIG_CURRENCY,
    PERM_CONFIG_SYNC,
    PERM_CONFIG_MAINTENANCE,
    PERM_NOTIFY,
    PERM_BACKUP,
    PERM_CLEANUP,
    PERM_SHARED_USERS,
})

# Limited preset: support + receipts + basic user lookup
LIMITED_PERMISSION_PRESET: FrozenSet[str] = frozenset({
    PERM_USER_MGMT,
    PERM_RECEIPTS,
    PERM_TICKETS,
    PERM_TICKETS_ACTIVE,
    PERM_TICKETS_CLOSED,
    PERM_TICKETS_REPLY,
})

# Parent grants all children (wildcard)
_PERM_PARENTS: dict[str, str] = {
    PERM_TICKETS_ACTIVE: PERM_TICKETS,
    PERM_TICKETS_CLOSED: PERM_TICKETS,
    PERM_TICKETS_SEARCH: PERM_TICKETS,
    PERM_TICKETS_CREATE: PERM_TICKETS,
    PERM_TICKETS_NOTIF: PERM_TICKETS,
    PERM_TICKETS_REPLY: PERM_TICKETS,
    PERM_OVPN_PROFILES: PERM_OVPN,
    PERM_OVPN_CONNECTION: PERM_OVPN,
    PERM_OVPN_MANAGE: PERM_OVPN,
    PERM_CONFIG_CARDS: "config",
    PERM_CONFIG_MESSAGES: "config",
    PERM_CONFIG_PRESETS: "config",
    PERM_CONFIG_SUBJECTS: "config",
    PERM_CONFIG_LANG: "config",
    PERM_CONFIG_CURRENCY: "config",
    PERM_CONFIG_SYNC: "config",
    PERM_CONFIG_MAINTENANCE: "config",
    PERM_SALES_DISCOUNTS: PERM_SALES,
}

# Exact callback -> permission (menus and stable actions)
CALLBACK_PERMISSION_EXACT: dict[str, str | None] = {
    "admin_start": None,
    "search_user": PERM_USER_MGMT,
    "pending_receipts": PERM_RECEIPTS,
    "pending_receipts_noop": PERM_RECEIPTS,
    "receipt_notif_mode": PERM_RECEIPTS,
    "receipt_group_admin": PERM_RECEIPTS,
    "admin_tickets": PERM_TICKETS,
    "admin_tickets_active": PERM_TICKETS_ACTIVE,
    "admin_tickets_closed": PERM_TICKETS_CLOSED,
    "admin_tickets_search": PERM_TICKETS_SEARCH,
    "admin_tickets_create": PERM_TICKETS_CREATE,
    "admin_tickets_notif_mode": PERM_TICKETS_NOTIF,
    "ovpn_l2tp_mgmt_menu": PERM_OVPN,
    "list_profiles": PERM_OVPN_PROFILES,
    "settings_connection": PERM_OVPN_CONNECTION,
    "manage_ovpn": PERM_OVPN_MANAGE,
    "upload_ovpn": PERM_OVPN_MANAGE,
    "wg_mgmt_menu": PERM_WG_PLANS,
    "list_wg_profiles": PERM_WG_PLANS,
    "list_wg_users": PERM_WG_USERS,
    "list_wg_interfaces": PERM_WG_INTERFACES,
    "add_wg_interface_start": PERM_WG_ADD_INTERFACE,
    "back_to_int_settings": PERM_WG_INTERFACES,
    "back_to_wg_section": PERM_WG_INTERFACES,
    "sales_mgmt_menu": PERM_SALES,
    "sales_capacity_menu": PERM_SALES,
    "renew_mgmt_menu": PERM_SALES,
    "sales_toggle_global": PERM_SALES,
    "discount_codes_menu": PERM_SALES_DISCOUNTS,
    "admin_reports": PERM_REPORTS,
    "report_export_sales": PERM_REPORTS,
    "list_servers": PERM_SERVERS,
    "server_add": PERM_SERVERS,
    "bot_config_menu": PERM_CONFIG_CARDS,
    "settings_cards": PERM_CONFIG_CARDS,
    "settings_messages": PERM_CONFIG_MESSAGES,
    "settings_purchase_terms": PERM_CONFIG_MESSAGES,
    "settings_presets": PERM_CONFIG_PRESETS,
    "settings_subjects": PERM_CONFIG_SUBJECTS,
    "settings_lang": PERM_CONFIG_LANG,
    "settings_currency": PERM_CONFIG_CURRENCY,
    "settings_sync": PERM_CONFIG_SYNC,
    "admin_maintenance_menu": PERM_CONFIG_MAINTENANCE,
    "shared_users_menu": PERM_SHARED_USERS,
    "set_default_shared": PERM_SHARED_USERS,
    "edit_shared_user": PERM_SHARED_USERS,
    "notification_menu": PERM_NOTIFY,
    "notify_broadcast": PERM_NOTIFY,
    "notify_targeted": PERM_NOTIFY,
    "backup_menu": PERM_BACKUP,
    "backup_set_interval": PERM_BACKUP,
    "backup_export": PERM_BACKUP,
    "clean_db_menu": PERM_CLEANUP,
    "clean_expired_subs": PERM_CLEANUP,
    "clean_expired_wg_subs": PERM_CLEANUP,
    "clean_pending_receipts": PERM_CLEANUP,
    "clean_old_transactions": PERM_CLEANUP,
    "clean_closed_tickets": PERM_CLEANUP,
    "clean_inactive_users": PERM_CLEANUP,
    "clean_mt_orphans": PERM_CLEANUP,
    "clear_mt_sessions": PERM_CLEANUP,
    "clean_settings_menu": PERM_CLEANUP,
    "warn_clean_expired_subs": PERM_CLEANUP,
    "warn_clean_expired_wg_subs": PERM_CLEANUP,
    "force_clean_expired_subs": PERM_CLEANUP,
    "force_clean_expired_wg_subs": PERM_CLEANUP,
    "admin_mgmt_menu": None,
    "admin_add_start": None,
    "admin_remove_start": None,
}

# Prefix -> permission (first match wins)
CALLBACK_PERMISSION_PREFIXES: tuple[tuple[str, str], ...] = (
    ("pending_receipts_page_", PERM_RECEIPTS),
    ("wg_ifaces_page_", PERM_WG_INTERFACES),
    ("wg_ifaces_noop", PERM_WG_INTERFACES),
    ("view_receipt_", PERM_RECEIPTS),
    ("receipt_approve_", PERM_RECEIPTS),
    ("receipt_reject_", PERM_RECEIPTS),
    ("receipt_set_mode_", PERM_RECEIPTS),
    ("admin_ticket_", PERM_TICKETS),
    ("admin_reply_", PERM_TICKETS_REPLY),
    ("admin_close_", PERM_TICKETS),
    ("admin_tickets_set_mode_", PERM_TICKETS_NOTIF),
    ("server_test_", PERM_SERVERS),
    ("server_edit_", PERM_SERVERS),
    ("server_delete_", PERM_SERVERS),
    ("edit_srv_", PERM_SERVERS),
    ("admin_user_hub_", PERM_USER_MGMT),
    ("admin_user_", PERM_USER_MGMT),
    ("manage_sub_", PERM_USER_MGMT),
    ("manage_wg_", PERM_USER_MGMT),
    ("set_lang_", PERM_CONFIG_LANG),
    ("set_curr_", PERM_CONFIG_CURRENCY),
    ("card_delete_", PERM_CONFIG_CARDS),
    ("card_add", PERM_CONFIG_CARDS),
    ("msg_edit_", PERM_CONFIG_MESSAGES),
    ("purchase_terms_", PERM_CONFIG_MESSAGES),
    ("preset_delete_", PERM_CONFIG_PRESETS),
    ("preset_add", PERM_CONFIG_PRESETS),
    ("subj_delete_", PERM_CONFIG_SUBJECTS),
    ("subj_add", PERM_CONFIG_SUBJECTS),
    ("subj_reset", PERM_CONFIG_SUBJECTS),
    ("conn_server_", PERM_OVPN_CONNECTION),
    ("sales_toggle_", PERM_SALES),
    ("sales_set_limit_", PERM_SALES),
    ("sales_add_capacity_", PERM_SALES),
    ("sales_edit_msg_", PERM_SALES),
    ("toggle_renew_", PERM_SALES),
    ("discount_", PERM_SALES_DISCOUNTS),
    ("edit_wg_interface_", PERM_WG_INTERFACES),
    ("edit_wg_prof_", PERM_WG_PLANS),
    ("del_wg_profile_", PERM_WG_PLANS),
    ("wg_delete_", PERM_WG_INTERFACES),
    ("wg_migrate_", PERM_WG_INTERFACES),
    ("add_wg_profile", PERM_WG_PLANS),
    ("ban_user_", PERM_USER_MGMT),
    ("unban_user_", PERM_USER_MGMT),
    ("edit_balance_byuid_", PERM_USER_MGMT),
    ("edit_balance_", PERM_USER_MGMT),
    ("reset_pass_", PERM_USER_MGMT),
    ("del_profile_", PERM_OVPN_PROFILES),
    ("admin_wg_cfg_", PERM_USER_MGMT),
    ("admin_perm_", None),
    ("admin_pt_", None),
    ("admin_pe_", None),
)

# Callbacks that look admin-ish (for gate detection)
_ADMIN_CALLBACK_PREFIXES = (
    "admin_",
    "pending_receipts",
    "search_user",
    "list_servers",
    "server_",
    "view_receipt_",
    "receipt_approve_",
    "receipt_reject_",
    "clean_",
    "clear_mt_",
    "force_clean_",
    "warn_clean_",
    "wg_",
    "list_wg_",
    "add_wg_",
    "edit_wg_",
    "del_wg_",
    "ovpn_",
    "list_profiles",
    "manage_ovpn",
    "upload_ovpn",
    "bot_config",
    "settings_",
    "sales_",
    "edit_renew_",
    "set_renew_",
    "toggle_renew",
    "notify_",
    "notification_",
    "backup_",
    "shared_users",
    "set_default_shared",
    "edit_shared_user",
    "manage_sub_",
    "manage_wg_",
    "ban_user_",
    "unban_user_",
    "edit_balance_",
    "reset_pass_",
    "del_profile_",
    "admin_wg_cfg_",
    "card_",
    "msg_edit_",
    "preset_",
    "subj_",
    "conn_server_",
    "set_lang_",
    "set_curr_",
    "wallet_custom_toggle",
    "discount_",
)

SUPER_ONLY_CALLBACKS_EXACT = frozenset({
    "admin_mgmt_menu",
    "admin_add_start",
    "admin_remove_start",
    "admin_ps_full",
    "admin_ps_limited",
    "admin_pf_save",
    "backup_import",
    "confirm_restore_db",
    "cancel_restore_db",
    "force_clean_expired_subs",
    "force_clean_expired_wg_subs",
})

SUPER_ONLY_CALLBACK_PREFIXES = (
    "admin_perm_",
    "admin_pt_",
    "admin_pe_",
    "admin_ps_",
    "admin_pf_",
)


def permission_for_callback(callback_data: str) -> str | None:
    """Resolve required permission for a callback; None = any admin."""
    if callback_data in CALLBACK_PERMISSION_EXACT:
        return CALLBACK_PERMISSION_EXACT[callback_data]
    for prefix, perm in CALLBACK_PERMISSION_PREFIXES:
        if callback_data.startswith(prefix):
            return perm
    if callback_data.startswith("topup_") or callback_data in ("wallet_menu", "main_menu", "payment_help"):
        return "__user__"
    if callback_data.startswith("receipt_yes") or callback_data.startswith("receipt_no"):
        return "__user__"
    return None


def is_admin_panel_callback(callback_data: str) -> bool:
    if callback_data in CALLBACK_PERMISSION_EXACT:
        return True
    if callback_data.startswith("pending_receipts_page_"):
        return True
    for prefix in _ADMIN_CALLBACK_PREFIXES:
        if callback_data.startswith(prefix) or callback_data == prefix.rstrip("_"):
            return True
    for prefix, _ in CALLBACK_PERMISSION_PREFIXES:
        if callback_data.startswith(prefix):
            return True
    return False


def is_super_only_callback(callback_data: str) -> bool:
    if callback_data in SUPER_ONLY_CALLBACKS_EXACT:
        return True
    return any(callback_data.startswith(p) for p in SUPER_ONLY_CALLBACK_PREFIXES)


def _perm_matches(granted: Iterable[str], required: str) -> bool:
    granted_set = set(granted)
    if required in granted_set:
        return True
    parent = _PERM_PARENTS.get(required)
    if parent and parent in granted_set:
        return True
    if required.startswith("tickets.") and PERM_TICKETS in granted_set:
        return True
    if required.startswith("ovpn.") and PERM_OVPN in granted_set:
        return True
    if required in (PERM_WG_PLANS, PERM_WG_USERS, PERM_WG_INTERFACES, PERM_WG_ADD_INTERFACE):
        return required in granted_set
    if required.startswith("config.") and "config" in granted_set:
        return True
    parts = required.split(".")
    if len(parts) > 1 and ".".join(parts[:-1]) in granted_set:
        return True
    return False


def has_perm_in_set(granted: Iterable[str], required: str) -> bool:
    if not required:
        return True
    return _perm_matches(granted, required)


async def get_admin_permissions(telegram_id: int) -> set[str]:
    from vpn_bot.admin_management import is_super_admin

    if await is_super_admin(telegram_id):
        return set(ALL_PERMISSION_KEYS)
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Admin).where(Admin.telegram_id == telegram_id))
        admin = res.scalars().first()
        if not admin:
            return set()
        raw = admin.permissions_json
        if not raw:
            return set(LIMITED_PERMISSION_PRESET)
        try:
            data = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(data, list):
                return {p for p in data if p in ALL_PERMISSION_KEYS}
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid permissions_json for admin %s — defaulting to limited preset", telegram_id)
        return set(LIMITED_PERMISSION_PRESET)


async def has_admin_perm(telegram_id: int, perm: str) -> bool:
    from vpn_bot.admin_management import is_super_admin

    if await is_super_admin(telegram_id):
        return True
    if perm in (None, ""):
        return True
    granted = await get_admin_permissions(telegram_id)
    return has_perm_in_set(granted, perm)


async def set_admin_permissions(telegram_id: int, perms: set[str]) -> bool:
    """Persist permission set for a DB admin."""
    cleaned = {p for p in perms if p in ALL_PERMISSION_KEYS}
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Admin).where(Admin.telegram_id == telegram_id))
        admin = res.scalars().first()
        if not admin:
            return False
        admin.permissions_json = json.dumps(sorted(cleaned))
        await session.commit()
        return True


# Ordered list for permission editor UI (key, locale label key)
PERMISSION_UI_ENTRIES: tuple[tuple[str, str], ...] = (
    (PERM_USER_MGMT, "admin.perm.user_mgmt"),
    (PERM_RECEIPTS, "admin.perm.receipts"),
    (PERM_TICKETS, "admin.perm.tickets"),
    (PERM_TICKETS_ACTIVE, "admin.perm.tickets_active"),
    (PERM_TICKETS_CLOSED, "admin.perm.tickets_closed"),
    (PERM_TICKETS_SEARCH, "admin.perm.tickets_search"),
    (PERM_TICKETS_CREATE, "admin.perm.tickets_create"),
    (PERM_TICKETS_NOTIF, "admin.perm.tickets_notif"),
    (PERM_TICKETS_REPLY, "admin.perm.tickets_reply"),
    (PERM_OVPN, "admin.perm.ovpn"),
    (PERM_OVPN_PROFILES, "admin.perm.ovpn_profiles"),
    (PERM_OVPN_CONNECTION, "admin.perm.ovpn_connection"),
    (PERM_OVPN_MANAGE, "admin.perm.ovpn_manage"),
    (PERM_WG_PLANS, "admin.perm.wg_plans"),
    (PERM_WG_USERS, "admin.perm.wg_users"),
    (PERM_WG_INTERFACES, "admin.perm.wg_interfaces"),
    (PERM_WG_ADD_INTERFACE, "admin.perm.wg_add_interface"),
    (PERM_SALES, "admin.perm.sales"),
    (PERM_SALES_DISCOUNTS, "admin.perm.sales_discounts"),
    (PERM_REPORTS, "admin.perm.reports"),
    (PERM_SERVERS, "admin.perm.servers"),
    (PERM_CONFIG_CARDS, "admin.perm.config_cards"),
    (PERM_CONFIG_MESSAGES, "admin.perm.config_messages"),
    (PERM_CONFIG_PRESETS, "admin.perm.config_presets"),
    (PERM_CONFIG_SUBJECTS, "admin.perm.config_subjects"),
    (PERM_CONFIG_LANG, "admin.perm.config_lang"),
    (PERM_CONFIG_CURRENCY, "admin.perm.config_currency"),
    (PERM_CONFIG_SYNC, "admin.perm.config_sync"),
    (PERM_CONFIG_MAINTENANCE, "admin.perm.config_maintenance"),
    (PERM_NOTIFY, "admin.perm.notify"),
    (PERM_BACKUP, "admin.perm.backup"),
    (PERM_CLEANUP, "admin.perm.cleanup"),
    (PERM_SHARED_USERS, "admin.perm.shared_users"),
)


def callback_allowed_for_permissions(
    callback_data: str,
    granted: set[str],
    *,
    is_super: bool = False,
) -> bool:
    """Check if callback is allowed for a permission set (no DB)."""
    if callback_data == "__user__":
        return False
    if is_super_only_callback(callback_data):
        return is_super
    if not is_admin_panel_callback(callback_data):
        return True
    perm = permission_for_callback(callback_data)
    if perm is None:
        return True
    return has_perm_in_set(granted, perm)


async def get_all_admin_telegram_ids() -> list[int]:
    """Super admins from env plus all DB admins."""
    ids = list(config.ADMIN_IDS)
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(Admin.telegram_id))
        for row in res.scalars().all():
            tid = int(row)
            if tid not in ids:
                ids.append(tid)
    return ids


async def get_admins_for_permission(perm: str) -> list[int]:
    """Telegram IDs that should receive notifications for a permission."""
    out: list[int] = []
    for tid in await get_all_admin_telegram_ids():
        if await has_admin_perm(tid, perm):
            out.append(tid)
    return out


ChatContext = Literal["any", "private", "support", "support_or_private", "backup", "receipt", "receipt_or_private"]

GroupAdminScope = Literal["private", "backup", "support", "receipt", "unknown"]

_SCOPE_CALLBACK_PREFIXES: dict[GroupAdminScope, tuple[str, ...]] = {
    "receipt": (
        "pending_receipts",
        "pending_receipts_page_",
        "pending_receipts_noop",
        "view_receipt_",
        "receipt_approve_",
        "receipt_reject_",
        "receipt_notif_mode",
        "receipt_set_mode_",
        "receipt_group_admin",
    ),
    "backup": ("backup_",),
    "support": (
        "admin_tickets",
        "admin_tickets_",
        "admin_ticket_",
        "admin_reply_",
        "admin_close_",
    ),
}


async def resolve_group_admin_scope(
    chat_id: int,
    *,
    chat_type: str | None = None,
) -> GroupAdminScope:
    """Classify chat for scoped /admin and callback allowlists."""
    if chat_type == "private":
        return "private"

    if config.BACKUP_GROUP_ID and str(chat_id) == str(config.BACKUP_GROUP_ID):
        return "backup"

    from vpn_bot.admin_settings_service import get_receipt_group_id, get_support_group_id

    support_gid = await get_support_group_id()
    if support_gid and int(chat_id) == int(support_gid):
        return "support"

    receipt_gid = await get_receipt_group_id()
    if receipt_gid and int(chat_id) == int(receipt_gid):
        return "receipt"

    if chat_type in ("group", "supergroup"):
        return "unknown"

    return "private"


def is_callback_allowed_in_scope(scope: GroupAdminScope, callback_data: str) -> bool:
    """True when an admin-panel callback is permitted in the given group scope."""
    if scope == "private":
        return True
    if scope == "unknown":
        return False
    prefixes = _SCOPE_CALLBACK_PREFIXES.get(scope, ())
    return any(
        callback_data == prefix or callback_data.startswith(prefix) for prefix in prefixes
    )


async def _chat_context_allowed(update: Update, chat_context: ChatContext) -> bool:
    """Whether the update's chat matches the required context."""
    chat = update.effective_chat
    if not chat or chat_context == "any":
        return True
    if chat_context == "private":
        return chat.type == "private"
    if chat_context in ("support", "support_or_private"):
        if chat_context == "support_or_private" and chat.type == "private":
            return True
        from vpn_bot.admin_settings_service import get_support_group_id

        gid = await get_support_group_id()
        return bool(gid and chat.id == int(gid))
    if chat_context == "backup":
        gid = config.BACKUP_GROUP_ID
        return bool(gid and str(chat.id) == str(gid))
    if chat_context in ("receipt", "receipt_or_private"):
        if chat_context == "receipt_or_private" and chat.type == "private":
            return True
        from vpn_bot.admin_settings_service import get_receipt_group_id

        gid = await get_receipt_group_id()
        return bool(gid and chat.id == int(gid))
    return True


def _wrong_chat_context_message_key(chat_context: ChatContext) -> str | None:
    """Locale key when the update chat does not match the required context."""
    if chat_context in ("receipt", "receipt_or_private"):
        return "admin.wrong_chat_receipt"
    if chat_context in ("support", "support_or_private"):
        return "admin.wrong_chat_support"
    if chat_context == "backup":
        return "admin.wrong_chat_backup"
    return None


async def is_telegram_group_admin(bot, chat_id: int, user_id: int) -> bool:
    """True if user is Telegram group administrator or owner."""
    from telegram.constants import ChatMemberStatus

    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return member.status in (ChatMemberStatus.ADMINISTRATOR, ChatMemberStatus.OWNER)
    except Exception as exc:
        logger.warning("get_chat_member failed chat=%s user=%s: %s", chat_id, user_id, exc)
        return False


async def require_admin_message(
    update: Update,
    *,
    perm: str | None = None,
    chat_context: ChatContext = "any",
    silent: bool = False,
) -> bool:
    """
    Gate for admin MessageHandlers. Returns True if the caller may proceed.
    """
    if not update.effective_user:
        return False

    from vpn_bot.admin_management import is_user_admin

    uid = update.effective_user.id
    if not await is_user_admin(uid):
        if not silent:
            await deny_admin_access(update)
        return False

    if perm and not await has_admin_perm(uid, perm):
        if not silent:
            await deny_admin_access(update)
        return False

    if not await _chat_context_allowed(update, chat_context):
        if not silent:
            hint_key = _wrong_chat_context_message_key(chat_context)
            if hint_key and update.message:
                try:
                    await update.message.reply_text(LanguageManager.get(hint_key))
                except Exception:
                    pass
        return False

    return True


async def deny_admin_access(update: Update, *, alert: bool = False) -> None:
    msg = LanguageManager.get("admin.access_denied")
    if update.callback_query:
        try:
            await update.callback_query.answer(msg, show_alert=alert)
        except Exception:
            pass
        try:
            await update.callback_query.edit_message_text(msg)
        except Exception:
            pass
    elif update.message:
        try:
            await update.message.reply_text(msg)
        except Exception:
            pass


async def admin_callback_access_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    Early callback gate (handler group -5). Denies unauthorized admin panel taps.
    """
    if not update.callback_query or not update.effective_user:
        return
    data = update.callback_query.data or ""
    if not is_admin_panel_callback(data):
        return

    from vpn_bot.admin_management import is_super_admin, is_user_admin

    uid = update.effective_user.id
    if not await is_user_admin(uid):
        await deny_admin_access(update, alert=True)
        raise ApplicationHandlerStop

    if is_super_only_callback(data):
        if not await is_super_admin(uid):
            await deny_admin_access(update, alert=True)
            raise ApplicationHandlerStop
        return

    perm = permission_for_callback(data)
    if perm == "__user__":
        return
    if perm and not await has_admin_perm(uid, perm):
        await deny_admin_access(update, alert=True)
        raise ApplicationHandlerStop

    chat = update.effective_chat
    if chat:
        scope = await resolve_group_admin_scope(chat.id, chat_type=chat.type)
        if scope != "private" and not is_callback_allowed_in_scope(scope, data):
            await deny_admin_access(update, alert=True)
            raise ApplicationHandlerStop


def guard_admin_handler(handler: Callable | None = None, *, perm: str | None = None):
    """Decorator for admin handlers (non-callback or explicit perm)."""

    def decorator(fn):
        @wraps(fn)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            if not update.effective_user:
                return await fn(update, context, *args, **kwargs)
            from vpn_bot.admin_management import is_super_admin, is_user_admin

            uid = update.effective_user.id
            if not await is_user_admin(uid):
                await deny_admin_access(update)
                return
            required = perm
            if update.callback_query and update.callback_query.data:
                cb = update.callback_query.data
                if is_super_only_callback(cb) and not await is_super_admin(uid):
                    await deny_admin_access(update)
                    return
                if required is None:
                    required = permission_for_callback(cb)
            if required and required != "__user__" and not await has_admin_perm(uid, required):
                await deny_admin_access(update)
                return
            return await fn(update, context, *args, **kwargs)

        return wrapper

    if handler is not None:
        return decorator(handler)
    return decorator
