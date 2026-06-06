"""Shared admin panel states and helpers (no handler imports)."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import CallbackQueryHandler, ContextTypes

from vpn_bot.admin_conversation import admin_exit_to_menu, build_admin_fallback_handlers
from vpn_bot.admin_user_service import (
    build_user_hub_keyboard,
    format_user_info_text,
    get_user_by_id,
    get_user_comprehensive_info,
    user_hub_back_markup,
)
from vpn_bot.bot_handler import main_menu_text_dispatch
from vpn_bot.conversation_controls import append_conv_footer, conv_control_handlers, merge_markup
from vpn_bot.utils import LanguageManager

# User search / hub states
SEARCH_USERNAME, USER_ACTION, RESET_PASS, ADD_DATA, EXTEND_TIME, DELETE_CONFIRM, EDIT_BALANCE = range(7)
USER_NOTIFY_MSG, WG_EXTEND, WG_ADD_DATA, PICK_USER, USER_SHARED = range(48, 53)
WAIT_OVPN_FILE, WAIT_OVPN_SERVER, WAIT_OVPN_LABEL = range(7, 10)
WAIT_IMPORT_FILE = 10
ADMIN_MGMT_ID = 11
BROADCAST_MSG, TARGETED_USER_ID, TARGETED_MSG, BROADCAST_CONFIRM = range(20, 24)
CLEAN_SET_VALUE = 24
SHARED_DEFAULT_VALUE, SHARED_USER_LOOKUP, SHARED_USER_VALUE = range(40, 43)
SEARCH_WG_INPUT = 46
CONFIRM_RESTORE = 47

# Profile / WireGuard conversation states
PROFILE_NAME, PROFILE_DATA, PROFILE_DAYS, PROFILE_PRICE_USD, PROFILE_PRICE_TOMAN, PROFILE_RATE_LIMIT, PROFILE_SERVER = range(20, 27)
PROFILE_EDIT_NAME, PROFILE_EDIT_PRICE = range(27, 29)
WG_PROFILE_NAME, WG_PROFILE_VOLUME, WG_PROFILE_DAYS, WG_PROFILE_PRICE_USD, WG_PROFILE_PRICE_TOMAN, WG_PROFILE_RATE_LIMIT, WG_PROFILE_SERVER = range(80, 87)
WG_PROFILE_EDIT_NAME, WG_PROFILE_EDIT_PRICE = range(87, 89)
EDIT_PROFILE_SPEED, EDIT_WG_PROFILE_SPEED = range(90, 92)
WG_INT_SETTINGS, WG_INT_PORT, WG_INT_DNS, WG_INT_ENDPOINT, WG_INT_MTU, WG_INT_KEEPALIVE, WG_INT_ADDRESS, WG_INT_UPSTREAM, WG_INT_ROUTING_MARK, WG_INT_NAT_DST, WG_INT_GATEWAY, WG_INT_MAX_USERS, WG_INT_NOTIFY_ASK, WG_NOTIFICATION_TEMPLATE, WG_ADD_INT_SERVER = range(100, 115)
WG_MIGRATE_TARGET, WG_MIGRATE_CONFIRM = 115, 116
WG_DELETE_CONFIRM = 117
WG_INT_NAT_ROUTING_MARK = 118
WG_INT_NAT_DST_NEGATE = 119
WG_ADD_INT_MAN_NAME, WG_ADD_INT_MAN_PORT, WG_ADD_INT_MAN_ADDR, WG_ADD_INT_MAN_DNS, WG_ADD_INT_MAN_EP, WG_ADD_INT_MAN_MTU, WG_ADD_INT_MAN_KA, WG_ADD_INT_MAN_UPSTREAM, WG_ADD_INT_MAN_RM, WG_ADD_INT_MAN_NATRM, WG_ADD_INT_MAN_NAT, WG_ADD_INT_MAN_NAT_NEGATE, WG_ADD_INT_MAN_ROUTE_TABLE, WG_ADD_INT_MAN_ROUTE_DST, WG_ADD_INT_MAN_ROUTE_GW, WG_ADD_INT_MAN_ROUTE_DIST = range(120, 136)
WG_INT_ROUTE_TABLE, WG_INT_ROUTE_DST, WG_INT_ROUTE_GW, WG_INT_ROUTE_DIST = range(137, 141)

_admin_log = logging.getLogger("vpn_bot.admin")


async def _reply_wg_service_error(update: Update, error, *, return_state: int):
    """Log server-side detail; show generic message to admin."""
    _admin_log.error("WG interface update failed: %s", error)
    await update.message.reply_text(LanguageManager.get("common.error"))
    return return_state


def admin_conversation_fallbacks(extra=None):
    """Standard admin ConversationHandler fallbacks (/admin, cancel, admin_start, menu buttons)."""
    handlers = list(build_admin_fallback_handlers(main_menu_text_dispatch))
    if extra:
        handlers.extend(extra)
    return handlers


def _with_conv_cancel(states: dict) -> dict:
    """Prepend cancel/skip inline handlers to each conversation state."""
    ch = conv_control_handlers(admin_exit_to_menu)
    return {state: [*ch, *handlers] for state, handlers in states.items()}


async def admin_conv_prompt(
    update: Update,
    text: str,
    reply_markup=None,
    *,
    with_skip: bool = False,
    parse_mode: str = "Markdown",
) -> None:
    """Multi-step admin prompt with cancel (and optional skip) buttons."""
    body = append_conv_footer(text, with_skip=with_skip)
    markup = merge_markup(reply_markup, with_cancel=True, with_skip=with_skip)
    await universal_reply(update, body, reply_markup=markup, parse_mode=parse_mode)


def get_admin_edit_inline_keyboard(back_callback_data):
    from vpn_bot.admin_menu import build_admin_back_markup

    return build_admin_back_markup(back_callback_data)


def _admin_main_back():
    from vpn_bot.admin_menu import build_admin_back_markup

    return build_admin_back_markup("admin_start")


def _user_hub_back(user_id: int):
    return user_hub_back_markup(user_id)


async def show_user_hub(update: Update, context: ContextTypes.DEFAULT_TYPE, user_id: int):
    """Render or refresh the user management hub."""
    user = await get_user_by_id(user_id)
    if not user:
        await universal_reply(update, LanguageManager.get("common.error"))
        return USER_ACTION

    context.user_data["admin_target_user_id"] = user_id
    ovpn_page = context.user_data.get("user_ovpn_page", 0)
    wg_page = context.user_data.get("user_wg_page", 0)
    data = await get_user_comprehensive_info(user_id, ovpn_page=ovpn_page, wg_page=wg_page)
    info_text = await format_user_info_text(user, data)
    keyboard = build_user_hub_keyboard(user, data)

    if update.callback_query:
        try:
            await update.callback_query.edit_message_text(
                info_text, reply_markup=keyboard, parse_mode="Markdown"
            )
        except Exception:
            await update.callback_query.message.reply_text(
                info_text, reply_markup=keyboard, parse_mode="Markdown"
            )
    elif update.message:
        await update.message.reply_text(
            info_text, reply_markup=keyboard, parse_mode="Markdown"
        )
    return USER_ACTION


_admin_start_back_handler = CallbackQueryHandler(admin_exit_to_menu, pattern="^admin_start$")


async def universal_reply(update: Update, text: str, reply_markup=None, parse_mode="Markdown"):
    """Helper to handle both callback queries and text messages."""
    from vpn_bot.utils import ensure_telegram_text

    body = ensure_telegram_text(text)
    if update.callback_query:
        try:
            await update.callback_query.answer()
            await update.callback_query.message.edit_text(
                body, reply_markup=reply_markup, parse_mode=parse_mode
            )
        except Exception:
            await update.callback_query.message.reply_text(
                body, reply_markup=reply_markup, parse_mode=parse_mode
            )
    else:
        await update.message.reply_text(body, reply_markup=reply_markup, parse_mode=parse_mode)
