"""
Shared admin ConversationHandler utilities: exit to main menu and clear flow state.
"""

from __future__ import annotations

from telegram import Update
from telegram.ext import ConversationHandler, ContextTypes

# Keys written during admin multi-step flows (search user, server edit, receipts, etc.)
_ADMIN_FLOW_KEYS = (
    "target_user",
    "admin_target_user_id",
    "balance_user_db_id",
    "current_sub_id",
    "current_username",
    "is_wg_delete",
    "new_server",
    "edit_server_id",
    "edit_field",
    "new_profile",
    "new_wg_profile",
    "sales_limit_key",
    "sales_msg_key",
    "renew_notify_proto",
    "receipt_id",
    "admin_wg_search_iface",
    "wg_edit_profile_id",
    "ovpn_edit_profile_id",
    "backup_action",
    "notify_target",
    "clean_target",
    "conn_edit_server_id",
    "msg_edit_key",
    "admin_ticket_create",
    "admin_get_wg_config",
    "discount_wizard",
    "discount_edit_id",
    "discount_edit_field",
)


def clear_admin_flow_context(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Drop transient keys so a new admin flow does not inherit stale state."""
    if not context.user_data:
        return
    for key in _ADMIN_FLOW_KEYS:
        context.user_data.pop(key, None)


async def route_admin_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route /admin to the correct menu based on chat scope (private vs group type)."""
    from vpn_bot.admin_permissions import (
        PERM_BACKUP,
        PERM_RECEIPTS,
        PERM_TICKETS,
        deny_admin_access,
        require_admin_message,
        resolve_group_admin_scope,
    )
    from vpn_bot.coupon_flow import clear_coupon_flow

    clear_admin_flow_context(context)
    clear_coupon_flow(context.user_data)
    chat = update.effective_chat
    if not chat:
        return ConversationHandler.END

    scope = await resolve_group_admin_scope(chat.id, chat_type=chat.type)

    if scope == "backup":
        if not await require_admin_message(update, perm=PERM_BACKUP, chat_context="backup"):
            return ConversationHandler.END
        from vpn_bot.admin_panel import send_backup_group_menu

        await send_backup_group_menu(update, context)
        return ConversationHandler.END

    if scope == "support":
        if not await require_admin_message(
            update, perm=PERM_TICKETS, chat_context="support_or_private"
        ):
            return ConversationHandler.END
        from vpn_bot.admin_tickets import admin_ticket_menu

        return await admin_ticket_menu(update, context)

    if scope == "receipt":
        if not await require_admin_message(
            update, perm=PERM_RECEIPTS, chat_context="receipt_or_private"
        ):
            return ConversationHandler.END
        from vpn_bot.admin_panel import send_receipt_group_admin_menu

        return await send_receipt_group_admin_menu(update, context)

    if scope == "unknown":
        await deny_admin_access(update)
        return ConversationHandler.END

    if not await require_admin_message(update):
        return ConversationHandler.END

    from vpn_bot.admin_panel import admin_start

    await admin_start(update, context)
    return ConversationHandler.END


async def admin_exit_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """End any admin conversation and show the scoped admin menu."""
    return await route_admin_command(update, context)


def build_admin_fallback_handlers(main_menu_dispatch):
    """
    Standard fallbacks for all admin ConversationHandlers.
    `main_menu_dispatch` is bot_handler.main_menu_text_dispatch (lazy import avoids cycles).
    """
    from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler

    from vpn_bot.bot_handler import MENU_BUTTONS_FILTER
    from vpn_bot.conversation_controls import legacy_cancel_handlers

    return [
        CommandHandler("admin", admin_exit_to_menu),
        *legacy_cancel_handlers(admin_exit_to_menu),
        CallbackQueryHandler(admin_exit_to_menu, pattern="^admin_start$"),
        MessageHandler(MENU_BUTTONS_FILTER, main_menu_dispatch),
    ]
