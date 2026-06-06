"""
Register Telegram handlers on an Application (shared by main.py and E2E tests).
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from vpn_bot.admin_panel import (
    admin_backup_handler,
    admin_cleanup_conv_handler,
    admin_edit_speed_handler,
    admin_mgmt_handler,
    admin_notification_handler,
    admin_ovpn_handler,
    admin_profile_handler,
    admin_search_user_handler,
    admin_server_handler,
    admin_start,
    admin_audit_log_view,
    admin_wg_mgmt_handler,
    admin_wg_profile_handler,
    clean_db_menu,
    confirm_receipt_action,
    handle_cleanup_action,
    list_pending_receipts,
    pending_receipts_page_nav,
    send_receipt_group_admin_menu,
    toggle_receipt_notif_mode,
    list_profiles,
    list_servers,
    list_wg_interfaces,
    wg_interfaces_page_nav,
    list_wg_profiles,
    list_wg_users,
    ovpn_l2tp_mgmt_menu,
    shared_users_handler,
    test_server_connection,
    view_receipt,
    wg_mgmt_menu,
)
from vpn_bot.admin_sales import sales_mgmt_handler as admin_sales_conv_handler
from vpn_bot.admin_discount import admin_discount_handler
from vpn_bot.admin_settings import admin_settings_handler as admin_settings_conv_handler
from vpn_bot.admin_tickets import admin_ticket_handler, handle_admin_group_reply
from vpn_bot.backup_manager import BackupManager
from vpn_bot.bot_handler import (
    buy_handler,
    main_menu_callback,
    my_subscriptions,
    set_backup_group,
    set_receipt_group,
    wallet_handler,
)
from vpn_bot.config import config
from vpn_bot.support_tickets import set_support_group, support_ticket_handler
from vpn_bot.utils import LanguageManager

logger = logging.getLogger("vpn_bot")


def register_all_handlers(app: Application, *, include_user_handlers: bool = True) -> None:
    """Attach production handlers to ``app`` (same set as ``main.main()``)."""
    from vpn_bot.admin_permissions import admin_callback_access_gate

    app.add_handler(TypeHandler(Update, LanguageManager.global_refresh_handler), group=-10)

    if include_user_handlers:
        from vpn_bot.user_middleware import global_banned_user_gate

        app.add_handler(TypeHandler(Update, global_banned_user_gate), group=-6)

    app.add_handler(CallbackQueryHandler(admin_callback_access_gate), group=-5)

    app.add_handler(admin_search_user_handler)
    app.add_handler(admin_ovpn_handler)
    app.add_handler(admin_profile_handler)
    app.add_handler(admin_server_handler)
    app.add_handler(admin_settings_conv_handler)
    app.add_handler(admin_sales_conv_handler)
    app.add_handler(admin_discount_handler)
    app.add_handler(admin_backup_handler)
    app.add_handler(admin_mgmt_handler)
    app.add_handler(shared_users_handler)
    app.add_handler(admin_notification_handler)
    app.add_handler(admin_wg_profile_handler)
    app.add_handler(admin_edit_speed_handler)
    app.add_handler(admin_wg_mgmt_handler)
    app.add_handler(admin_cleanup_conv_handler)

    async def admin_command_proxy(update: Update, context):
        from vpn_bot.admin_conversation import route_admin_command

        await route_admin_command(update, context)

    app.add_handler(CommandHandler("admin", admin_command_proxy))
    app.add_handler(CommandHandler("setsupport", set_support_group))
    app.add_handler(CommandHandler("setreceipt", set_receipt_group))
    app.add_handler(CommandHandler("backup", set_backup_group))

    async def backup_group_settings_btn(update: Update, context):
        from vpn_bot.admin_panel import send_backup_group_menu
        from vpn_bot.admin_permissions import PERM_BACKUP, require_admin_message

        if not await require_admin_message(update, perm=PERM_BACKUP, chat_context="backup"):
            return
        await send_backup_group_menu(update, context)

    async def backup_group_manual_btn(update: Update, context):
        from vpn_bot.admin_permissions import PERM_BACKUP, require_admin_message

        if not await require_admin_message(update, perm=PERM_BACKUP, chat_context="backup"):
            return
        chat_id = update.effective_chat.id
        mgr = BackupManager(context.bot)
        await mgr.send_backup_to_telegram(str(chat_id), is_auto=False)

    app.add_handler(
        MessageHandler(
            filters.Regex(
                f"^({'|'.join(LanguageManager.get_all_translations('admin.backup_group.menu_backup'))})$"
            ),
            backup_group_settings_btn,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.Regex(
                f"^({'|'.join(LanguageManager.get_all_translations('admin.backup_group.menu_manual'))})$"
            ),
            backup_group_manual_btn,
        )
    )

    app.add_handler(
        MessageHandler(filters.ChatType.GROUPS & (~filters.COMMAND), handle_admin_group_reply),
        group=-2,
    )

    app.add_handler(CallbackQueryHandler(admin_start, pattern="^admin_start$"))
    app.add_handler(CallbackQueryHandler(admin_audit_log_view, pattern="^admin_audit_log$"))
    app.add_handler(CallbackQueryHandler(list_servers, pattern="^list_servers$"))
    app.add_handler(CallbackQueryHandler(test_server_connection, pattern="^server_test_"))
    app.add_handler(CallbackQueryHandler(list_profiles, pattern="^list_profiles$"))
    app.add_handler(CallbackQueryHandler(ovpn_l2tp_mgmt_menu, pattern="^ovpn_l2tp_mgmt_menu$"))
    app.add_handler(CallbackQueryHandler(list_pending_receipts, pattern="^pending_receipts$"))
    app.add_handler(
        CallbackQueryHandler(
            pending_receipts_page_nav,
            pattern="^pending_receipts_page_\\d+$|^pending_receipts_noop$",
        )
    )
    app.add_handler(
        CallbackQueryHandler(
            toggle_receipt_notif_mode,
            pattern="^receipt_notif_mode$|^receipt_set_mode_",
        )
    )
    app.add_handler(
        CallbackQueryHandler(send_receipt_group_admin_menu, pattern="^receipt_group_admin$")
    )

    async def receipt_group_pending_btn(update: Update, context):
        from vpn_bot.admin_permissions import PERM_RECEIPTS, require_admin_message

        if not await require_admin_message(
            update, perm=PERM_RECEIPTS, chat_context="receipt_or_private"
        ):
            return
        await list_pending_receipts(update, context)

    app.add_handler(
        MessageHandler(
            filters.Regex(
                f"^({'|'.join(LanguageManager.get_all_translations('admin.receipt_group.menu_pending'))})$"
            ),
            receipt_group_pending_btn,
        )
    )
    app.add_handler(CallbackQueryHandler(view_receipt, pattern="^view_receipt_"))
    app.add_handler(CallbackQueryHandler(confirm_receipt_action, pattern="^receipt_(approve|reject)_"))
    app.add_handler(CallbackQueryHandler(clean_db_menu, pattern="^clean_db_menu$"))
    app.add_handler(
        CallbackQueryHandler(
            handle_cleanup_action,
            pattern=(
                "^(clean_expired_subs|clean_expired_wg_subs|clean_pending_receipts|"
                "clean_old_transactions|clean_closed_tickets|clean_inactive_users|"
                "clean_mt_orphans|clear_mt_sessions|warn_clean_expired_subs|"
                "warn_clean_expired_wg_subs|force_clean_expired_subs|force_clean_expired_wg_subs)$"
            ),
        )
    )
    app.add_handler(CallbackQueryHandler(wg_mgmt_menu, pattern="^wg_mgmt_menu$"))
    app.add_handler(CallbackQueryHandler(list_wg_profiles, pattern="^list_wg_profiles$"))
    app.add_handler(CallbackQueryHandler(list_wg_interfaces, pattern="^list_wg_interfaces$"))
    app.add_handler(
        CallbackQueryHandler(
            wg_interfaces_page_nav,
            pattern="^wg_ifaces_page_\\d+$|^wg_ifaces_noop$",
        )
    )
    app.add_handler(CallbackQueryHandler(list_wg_users, pattern="^list_wg_users$"))

    if not include_user_handlers:
        return

    from vpn_bot.admin_management import AdminKeywordFilter, secret_keyword_listener
    from vpn_bot.bot_handler import check_and_refresh_keyboard
    from vpn_bot.user_features import purchase_history, tutorials_menu

    app.add_handler(MessageHandler(AdminKeywordFilter(), secret_keyword_listener), group=-1)
    app.add_handler(MessageHandler(filters.ALL, check_and_refresh_keyboard), group=-8)

    app.add_handler(
        MessageHandler(
            filters.Regex(f"^({'|'.join(LanguageManager.get_all_translations('menu.my_subs'))})$"),
            my_subscriptions,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.Regex(f"^({'|'.join(LanguageManager.get_all_translations('menu.tutorials'))})$"),
            tutorials_menu,
        )
    )
    app.add_handler(
        MessageHandler(
            filters.Regex(f"^({'|'.join(LanguageManager.get_all_translations('menu.history'))})$"),
            purchase_history,
        )
    )

    from vpn_bot.coupon_flow import (
        global_coupon_enter,
        global_coupon_enter_inline,
        global_coupon_skip,
        global_receive_coupon_code,
    )

    app.add_handler(
        CallbackQueryHandler(global_coupon_skip, pattern="^coupon_skip$", block=False),
    )
    app.add_handler(
        CallbackQueryHandler(global_coupon_enter, pattern="^coupon_enter$", block=False),
    )
    app.add_handler(
        CallbackQueryHandler(global_coupon_enter_inline, pattern="^coupon_enter_inline_", block=False),
    )
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            global_receive_coupon_code,
            block=False,
        ),
        group=1,
    )

    async def settings_handler_proxy(update: Update, context):
        from vpn_bot.admin_management import is_user_admin

        if await is_user_admin(update.effective_user.id):
            await admin_start(update, context)

    app.add_handler(
        MessageHandler(
            filters.Regex(f"^({'|'.join(LanguageManager.get_all_translations('menu.settings'))})$"),
            settings_handler_proxy,
        )
    )

    from vpn_bot.purchase_terms import handle_terms_accept, handle_terms_decline

    app.add_handler(wallet_handler)
    app.add_handler(buy_handler)
    app.add_handler(support_ticket_handler)
    app.add_handler(admin_ticket_handler)
    app.add_handler(CallbackQueryHandler(handle_terms_accept, pattern="^terms_accept$"))
    app.add_handler(CallbackQueryHandler(handle_terms_decline, pattern="^terms_decline$"))
    app.add_handler(CallbackQueryHandler(main_menu_callback))
