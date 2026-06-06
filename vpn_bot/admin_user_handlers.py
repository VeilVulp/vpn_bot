"""Admin user search and user hub handlers (split from admin_panel)."""

from __future__ import annotations

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, filters

from vpn_bot.admin_audit import audit_log
from vpn_bot.admin_user_service import (
    find_users_by_query, get_recent_users, format_user_pick_label,
    get_user_transactions, get_user_receipts_summary,
    update_user_balance, toggle_user_ban, delete_user_full,
)
from vpn_bot.admin_ticket_service import get_user_tickets
from vpn_bot.admin_subscription_service import (
    extend_subscription_validity, reset_subscription_password,
    add_subscription_data, delete_ovpn_subscription,
    get_subscription_comprehensive_info,
)
from vpn_bot.admin_wg_service import (
    extend_wg_subscription, add_wg_subscription_data,
    delete_wg_subscription,
    get_wg_subscription_comprehensive_info,
    format_wg_subscription_info_text,
)
from vpn_bot.admin_shared_service import get_user_shared_count_from_mt, set_user_shared_count_on_mt
from vpn_bot.utils import LanguageManager, format_currency

from vpn_bot.bot_handler import MENU_BUTTONS_FILTER
from vpn_bot.admin_panel_shared import (
    SEARCH_USERNAME, USER_ACTION, RESET_PASS, ADD_DATA, EXTEND_TIME, DELETE_CONFIRM, EDIT_BALANCE,
    USER_NOTIFY_MSG, WG_EXTEND, WG_ADD_DATA, PICK_USER, USER_SHARED,
    admin_conversation_fallbacks, show_user_hub, _with_conv_cancel,
    _admin_start_back_handler,
)

logger = logging.getLogger("vpn_bot.admin")

# --- User Search Workflow ---

async def search_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    from vpn_bot.admin_conversation import clear_admin_flow_context
    clear_admin_flow_context(context)
    context.user_data.pop("user_ovpn_page", None)
    context.user_data.pop("user_wg_page", None)
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get("admin.user.btn_recent"), callback_data="admin_user_recent")],
        [InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="admin_start")],
    ]
    await query.edit_message_text(
        LanguageManager.get("admin.user.search_prompt"),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return SEARCH_USERNAME


def _search_user_back_markup():
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton(LanguageManager.get("admin.user.btn_new_search"), callback_data="admin_user_search")],
            [InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="admin_start")],
        ]
    )


async def admin_user_search_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-open search prompt from hub."""
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get("admin.user.btn_recent"), callback_data="admin_user_recent")],
        [InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="admin_start")],
    ]
    await query.edit_message_text(
        LanguageManager.get("admin.user.search_prompt"),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return SEARCH_USERNAME


async def admin_user_recent(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    users = await get_recent_users(10)
    if not users:
        await query.edit_message_text(
            LanguageManager.get("admin.user.no_recent"),
            reply_markup=_search_user_back_markup(),
        )
        return SEARCH_USERNAME
    keyboard = [
        [InlineKeyboardButton(
            LanguageManager.get("admin.user.pick_user", name=format_user_pick_label(u)),
            callback_data=f"admin_user_hub_{u.id}",
        )]
        for u in users
    ]
    keyboard.append([InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="admin_user_search")])
    await query.edit_message_text(
        LanguageManager.get("admin.user.recent_title"),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return PICK_USER


async def display_user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = (update.message.text or "").strip()
    if not query_text:
        await update.message.reply_text(
            LanguageManager.get("admin.user.search_empty"),
            reply_markup=_search_user_back_markup(),
        )
        return SEARCH_USERNAME

    users = await find_users_by_query(query_text)
    if not users:
        await update.message.reply_text(
            LanguageManager.get("admin.user.no_results", query=query_text),
            reply_markup=_search_user_back_markup(),
        )
        return SEARCH_USERNAME

    if len(users) > 1:
        keyboard = [
            [InlineKeyboardButton(
                LanguageManager.get("admin.user.pick_user", name=format_user_pick_label(u)),
                callback_data=f"admin_user_hub_{u.id}",
            )]
            for u in users
        ]
        keyboard.append([InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="admin_user_search")])
        await update.message.reply_text(
            LanguageManager.get("admin.user.multi_results", count=len(users)),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode="Markdown",
        )
        return PICK_USER

    context.user_data["user_ovpn_page"] = 0
    context.user_data["user_wg_page"] = 0
    return await show_user_hub(update, context, users[0].id)


async def admin_user_hub_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split("_")[-1])
    return await show_user_hub(update, context, user_id)


async def user_ovpn_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    user_id = int(parts[3])
    page = int(parts[4])
    context.user_data["user_ovpn_page"] = page
    return await show_user_hub(update, context, user_id)


async def user_wg_page_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    parts = query.data.split("_")
    user_id = int(parts[3])
    page = int(parts[4])
    context.user_data["user_wg_page"] = page
    return await show_user_hub(update, context, user_id)


async def manage_sub_redirect(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    username = query.data.split("_", 2)[2]

    sub, mt_info = await get_subscription_comprehensive_info(username)
    if not sub:
        await query.message.reply_text(
            LanguageManager.get("admin.user.not_found_db", username=username)
        )
        return USER_ACTION

    if not mt_info:
        await query.message.reply_text(LanguageManager.get("admin.user.mt_connectivity_error"))

    context.user_data["current_sub_id"] = sub.id
    context.user_data["current_username"] = username
    context.user_data["admin_target_user_id"] = sub.user_id

    info_text = await format_subscription_info_text(sub, mt_info)
    hub_id = sub.user_id
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get("admin.user.btn_reset_pass"), callback_data=f"reset_pass_{username}")],
        [InlineKeyboardButton(LanguageManager.get("admin.user.btn_add_data"), callback_data=f"add_data_{username}")],
        [InlineKeyboardButton(LanguageManager.get("admin.user.btn_extend"), callback_data=f"extend_time_{username}")],
        [InlineKeyboardButton(LanguageManager.get("admin.user.btn_toggle"), callback_data=f"disable_user_{username}")],
        [InlineKeyboardButton(LanguageManager.get("admin.user.btn_delete_ovpn"), callback_data=f"delete_ovpn_sub_{username}")],
        [InlineKeyboardButton(LanguageManager.get("admin.user.btn_shared"), callback_data=f"admin_shared_{username}")],
        [InlineKeyboardButton(LanguageManager.get("admin.user.btn_back_hub"), callback_data=f"admin_user_hub_{hub_id}")],
    ]

    await query.edit_message_text(info_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return USER_ACTION


async def manage_wg_redirect(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    wg_sub_id: int | None = None,
):
    query = update.callback_query
    if wg_sub_id is None:
        if not query or not query.data:
            return ConversationHandler.END
        wg_sub_id = int(query.data.split("_")[2])
    if query:
        await query.answer()

    wg_sub, mt_data = await get_wg_subscription_comprehensive_info(str(wg_sub_id))
    if not wg_sub:
        await query.message.reply_text(LanguageManager.get("admin.wg_config.not_found", query=str(wg_sub_id)))
        return USER_ACTION

    context.user_data["current_wg_sub_id"] = wg_sub_id
    context.user_data["admin_target_user_id"] = wg_sub.user_id

    info_text = await format_wg_subscription_info_text(wg_sub, mt_data)
    hub_id = wg_sub.user_id
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get("admin.user.btn_get_wg_config"), callback_data=f"admin_wg_cfg_{wg_sub_id}")],
        [InlineKeyboardButton(LanguageManager.get("admin.user.btn_extend"), callback_data=f"wg_extend_{wg_sub_id}")],
        [InlineKeyboardButton(LanguageManager.get("admin.user.btn_add_data"), callback_data=f"wg_add_data_{wg_sub_id}")],
        [InlineKeyboardButton(LanguageManager.get("admin.user.btn_toggle"), callback_data=f"wg_toggle_{wg_sub_id}")],
        [InlineKeyboardButton(LanguageManager.get("admin.user.btn_delete_wg"), callback_data=f"delete_wg_sub_{wg_sub_id}")],
        [InlineKeyboardButton(LanguageManager.get("admin.user.btn_back_hub"), callback_data=f"admin_user_hub_{hub_id}")],
    ]
    await query.edit_message_text(info_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return USER_ACTION


async def ban_user_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split("_")[2])
    success, _ = await toggle_user_ban(user_id)
    if not success:
        await query.answer(LanguageManager.get("common.error"), show_alert=True)
        return USER_ACTION
    admin_id = update.effective_user.id if update.effective_user else 0
    await audit_log(admin_id, "ban_user", target_type="user", target_id=str(user_id))
    return await show_user_hub(update, context, user_id)


async def unban_user_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split("_")[2])
    success, _ = await toggle_user_ban(user_id)
    if not success:
        await query.answer(LanguageManager.get("common.error"), show_alert=True)
        return USER_ACTION
    admin_id = update.effective_user.id if update.effective_user else 0
    await audit_log(admin_id, "unban_user", target_type="user", target_id=str(user_id))
    return await show_user_hub(update, context, user_id)


async def admin_wg_config_for_sub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send WG config to admin using the same delivery flow as user purchase."""
    query = update.callback_query
    await query.answer()
    wg_sub_id = int(query.data.split('_')[3])  # admin_wg_cfg_<id>
    admin_id = update.effective_user.id if update.effective_user else 0

    ok = await deliver_wg_subscription_by_id(
        context.bot,
        query.message.chat_id,
        wg_sub_id,
    )
    if ok:
        await audit_log(
            admin_id, "pull_wg_config",
            target_type="wg_subscription", target_id=str(wg_sub_id),
        )
    else:
        await query.message.reply_text(LanguageManager.get('admin.wg_config.error'))
    return USER_ACTION


async def edit_balance_by_uid_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored edit_balance_by_uid_flow using service."""
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split('_')[3])  # edit_balance_byuid_<id>
    
    # We can use the information already in the user flow if we assume user exists
    # but let's be safe and fetch basic info or just prompt.
    # We already have user_id.
    context.user_data['balance_user_db_id'] = user_id
    
    user = await get_user_by_id(user_id)
    if not user:
         await query.edit_message_text(LanguageManager.get('common.error'))
         return USER_ACTION
    
    current_balance = user.wallet_balance
    display_name = user.full_name or str(user.telegram_id)
    
    hub_id = user_id
    context.user_data["admin_target_user_id"] = hub_id
    await query.edit_message_text(
        LanguageManager.get('admin.user.balance_prompt', username=display_name, balance=await format_currency(current_balance)),
        reply_markup=_user_hub_back(hub_id),
        parse_mode='Markdown',
    )
    return EDIT_BALANCE

async def reset_password_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    username = query.data.split('_')[2]
    context.user_data['target_user'] = username
    hub_id = context.user_data.get("admin_target_user_id")
    await query.edit_message_text(
        LanguageManager.get('admin.user.reset_prompt', username=username),
        reply_markup=_user_hub_back(hub_id) if hub_id else _admin_main_back(),
        parse_mode='Markdown',
    )
    return RESET_PASS

async def process_reset_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored process_reset_password using admin_subscription_service."""
    new_pass = update.message.text.strip()
    username = context.user_data.get('target_user')
    
    success = await reset_subscription_password(username, new_pass)
    
    hub_id = context.user_data.get("admin_target_user_id")
    markup = _user_hub_back(hub_id) if hub_id else _admin_main_back()
    if success:
        await audit_log(
            update.effective_user.id if update.effective_user else 0,
            "reset_password",
            target_type="ovpn_user",
            target_id=username,
        )
        await update.message.reply_text(
            LanguageManager.get('admin.user.reset_success', username=username, password=new_pass),
            reply_markup=markup,
            parse_mode='Markdown',
        )
    else:
        await update.message.reply_text(LanguageManager.get('common.error'), reply_markup=markup)
    return RESET_PASS

async def add_data_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    username = query.data.split('_')[2]
    context.user_data['target_user'] = username
    hub_id = context.user_data.get("admin_target_user_id")
    await query.edit_message_text(
        LanguageManager.get('admin.user.add_data_prompt', username=username),
        reply_markup=_user_hub_back(hub_id) if hub_id else _admin_main_back(),
        parse_mode='Markdown',
    )
    return ADD_DATA

async def process_add_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored process_add_data using admin_subscription_service."""
    try:
        gb = float(update.message.text.strip())
        username = context.user_data.get('target_user')
        
        success = await add_subscription_data(username, gb)
        
        hub_id = context.user_data.get("admin_target_user_id")
        markup = _user_hub_back(hub_id) if hub_id else _admin_main_back()
        if success:
            await update.message.reply_text(
                LanguageManager.get('admin.user.add_data_success', gb=gb, username=username),
                reply_markup=markup,
            )
        else:
            await update.message.reply_text(
                LanguageManager.get('admin.user.not_found_db', username=username),
                reply_markup=markup,
            )
    except ValueError:
        hub_id = context.user_data.get("admin_target_user_id")
        await update.message.reply_text(
            LanguageManager.get('common.error'),
            reply_markup=_user_hub_back(hub_id) if hub_id else _admin_main_back(),
        )
    return ADD_DATA

async def extend_time_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    username = query.data.split('_')[2]
    context.user_data['target_user'] = username
    hub_id = context.user_data.get("admin_target_user_id")
    await query.edit_message_text(
        LanguageManager.get('admin.user.extend_prompt', username=username),
        reply_markup=_user_hub_back(hub_id) if hub_id else _admin_main_back(),
        parse_mode='Markdown',
    )
    return EXTEND_TIME


async def wg_extend_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    wg_sub_id = int(query.data.split("_")[2])
    context.user_data["current_wg_sub_id"] = wg_sub_id
    hub_id = context.user_data.get("admin_target_user_id")
    await query.edit_message_text(
        LanguageManager.get("admin.user.wg_extend_prompt"),
        reply_markup=_user_hub_back(hub_id) if hub_id else _admin_main_back(),
        parse_mode="Markdown",
    )
    return WG_EXTEND


async def process_wg_extend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        days = int(update.message.text.strip())
        wg_sub_id = context.user_data.get("current_wg_sub_id")
        success, new_expiry = await extend_wg_subscription(wg_sub_id, days)
        hub_id = context.user_data.get("admin_target_user_id")
        markup = _user_hub_back(hub_id) if hub_id else _admin_main_back()
        if success:
            date_str = await format_datetime(new_expiry, include_time=False)
            await update.message.reply_text(
                LanguageManager.get("admin.user.wg_extend_success", days=days, date=date_str),
                reply_markup=markup,
            )
        else:
            await update.message.reply_text(LanguageManager.get("common.error"), reply_markup=markup)
    except ValueError:
        await update.message.reply_text(LanguageManager.get("common.error"), reply_markup=_admin_main_back())
    return WG_EXTEND


async def wg_add_data_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    wg_sub_id = int(query.data.split("_")[3])
    context.user_data["current_wg_sub_id"] = wg_sub_id
    hub_id = context.user_data.get("admin_target_user_id")
    await query.edit_message_text(
        LanguageManager.get("admin.user.wg_add_data_prompt"),
        reply_markup=_user_hub_back(hub_id) if hub_id else _admin_main_back(),
        parse_mode="Markdown",
    )
    return WG_ADD_DATA


async def process_wg_add_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        gb = float(update.message.text.strip())
        wg_sub_id = context.user_data.get("current_wg_sub_id")
        success = await add_wg_subscription_data(wg_sub_id, gb)
        hub_id = context.user_data.get("admin_target_user_id")
        markup = _user_hub_back(hub_id) if hub_id else _admin_main_back()
        if success:
            await update.message.reply_text(
                LanguageManager.get("admin.user.wg_add_data_success", gb=gb),
                reply_markup=markup,
            )
        else:
            await update.message.reply_text(LanguageManager.get("common.error"), reply_markup=markup)
    except ValueError:
        await update.message.reply_text(LanguageManager.get("common.error"), reply_markup=_admin_main_back())
    return WG_ADD_DATA


async def wg_toggle_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    wg_sub_id = int(query.data.split("_")[2])
    await query.answer(LanguageManager.get("common.processing"))
    success, _ = await toggle_wg_subscription_status(wg_sub_id)
    if success:
        return await manage_wg_redirect(update, context, wg_sub_id=wg_sub_id)
    await query.answer(LanguageManager.get("common.error"), show_alert=True)
    return USER_ACTION


async def process_extend_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored process_extend_time using admin_subscription_service."""
    try:
        days = int(update.message.text.strip())
        username = context.user_data.get('target_user')
        
        success, new_expiry = await extend_subscription_validity(username, days)
        
        hub_id = context.user_data.get("admin_target_user_id")
        markup = _user_hub_back(hub_id) if hub_id else _admin_main_back()
        if success:
            date_str = await format_datetime(new_expiry, include_time=False)
            await update.message.reply_text(
                LanguageManager.get('admin.user.extend_success', username=username, days=days, date=date_str),
                reply_markup=markup,
            )
        else:
            await update.message.reply_text(
                LanguageManager.get('admin.user.not_found_db', username=username),
                reply_markup=markup,
            )
    except ValueError:
        hub_id = context.user_data.get("admin_target_user_id")
        await update.message.reply_text(
            LanguageManager.get('common.error'),
            reply_markup=_user_hub_back(hub_id) if hub_id else _admin_main_back(),
        )
    return EXTEND_TIME

async def edit_balance_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored edit_balance_flow using service."""
    query = update.callback_query
    await query.answer()
    username = query.data.split('_')[2]
    context.user_data['target_user'] = username
    
    user = await find_user_by_query(username)
    current_balance = user.wallet_balance if user else 0.0
    await query.edit_message_text(
        LanguageManager.get('admin.user.balance_prompt', username=username, balance=await format_currency(current_balance)),
        reply_markup=_admin_main_back(),
        parse_mode='Markdown',
    )
    return EDIT_BALANCE

async def process_edit_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored process_edit_balance using admin_user_service."""
    try:
        new_balance = float(update.message.text.strip())
        username = context.user_data.get('target_user')
        db_user_id = context.user_data.get('balance_user_db_id')
        
        # We still need to find the user if db_user_id is not set
        user = None
        if db_user_id:
            user = await get_user_by_id(db_user_id)
        else:
            user = await find_user_by_query(username)
            
        hub_id = context.user_data.get("admin_target_user_id") or (user.id if user else None)
        markup = _user_hub_back(hub_id) if hub_id else _admin_main_back()
        if user:
            old_balance = user.wallet_balance
            success = await update_user_balance(user.id, new_balance, log_adjust=True)
            if success:
                await audit_log(
                    update.effective_user.id if update.effective_user else 0,
                    "edit_balance",
                    target_type="user",
                    target_id=str(user.id),
                    detail={"old_balance": old_balance, "new_balance": new_balance},
                )
                display_name = user.full_name or username or str(user.telegram_id)
                await update.message.reply_text(
                    LanguageManager.get('admin.user.balance_success', username=display_name, balance=await format_currency(new_balance)),
                    reply_markup=markup,
                )
            else:
                await update.message.reply_text(LanguageManager.get('common.error'), reply_markup=markup)
        else:
            await update.message.reply_text(
                LanguageManager.get('admin.user.not_found_db', username=username or 'unknown'),
                reply_markup=markup,
            )
    except ValueError:
        hub_id = context.user_data.get("admin_target_user_id")
        await update.message.reply_text(
            LanguageManager.get('common.error'),
            reply_markup=_user_hub_back(hub_id) if hub_id else _admin_main_back(),
        )
    context.user_data.pop('balance_user_db_id', None)
    return EDIT_BALANCE

async def disable_user_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored disable_user_flow using admin_subscription_service."""
    query = update.callback_query
    username = query.data.split('_')[2]
    
    await query.answer(LanguageManager.get('common.processing'))
    
    success, new_status = await toggle_subscription_status(username)
    
    if success:
        status_label = "enabled" if new_status == 'enabled' else "disabled"
        await query.answer(LanguageManager.get('admin.server.toggle_success', status=status_label), show_alert=True)
        # Refresh the view
        return await manage_sub_redirect(update, context)
    else:
        await query.answer(LanguageManager.get('common.error'), show_alert=True)
    return USER_ACTION

async def delete_ovpn_sub_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    username = query.data.split("_", 2)[2]
    context.user_data["delete_mode"] = "ovpn"
    context.user_data["delete_target"] = username
    hub_id = context.user_data.get("admin_target_user_id")
    await query.edit_message_text(
        LanguageManager.get("admin.user.delete_ovpn_confirm", username=username),
        reply_markup=_user_hub_back(hub_id) if hub_id else _admin_main_back(),
        parse_mode="Markdown",
    )
    return DELETE_CONFIRM


async def delete_wg_sub_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    wg_sub_id = int(query.data.split("_")[3])
    context.user_data["delete_mode"] = "wg"
    context.user_data["delete_target"] = str(wg_sub_id)
    hub_id = context.user_data.get("admin_target_user_id")
    await query.edit_message_text(
        LanguageManager.get("admin.user.delete_wg_confirm", uid=wg_sub_id),
        reply_markup=_user_hub_back(hub_id) if hub_id else _admin_main_back(),
        parse_mode="Markdown",
    )
    return DELETE_CONFIRM


async def delete_account_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split("_")[2])
    user = await get_user_by_id(user_id)
    context.user_data["delete_mode"] = "account"
    context.user_data["delete_target"] = str(user_id)
    name = user.full_name if user else str(user_id)
    await query.edit_message_text(
        LanguageManager.get("admin.user.delete_account_confirm", name=name),
        reply_markup=_user_hub_back(user_id),
        parse_mode="Markdown",
    )
    return DELETE_CONFIRM


async def process_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    mode = context.user_data.get("delete_mode")
    target = context.user_data.get("delete_target")
    hub_id = context.user_data.get("admin_target_user_id")

    if text != "DELETE":
        await update.message.reply_text(
            LanguageManager.get("common.cancelled"),
            reply_markup=_user_hub_back(hub_id) if hub_id else _admin_main_back(),
        )
        context.user_data.pop("delete_mode", None)
        context.user_data.pop("delete_target", None)
        return USER_ACTION if hub_id else DELETE_CONFIRM

    success = False
    msg_key = "admin.user.delete_success"

    if mode == "ovpn":
        success = await delete_ovpn_subscription(target)
        msg_key = "admin.user.delete_ovpn_success"
        display = target
    elif mode == "wg":
        success = await delete_wg_subscription(int(target))
        msg_key = "admin.user.delete_wg_success"
        display = target
    elif mode == "account":
        uid = int(target)
        user = await get_user_by_id(uid)
        display = user.full_name if user else str(target)
        success = await delete_user_full(uid)
    else:
        await update.message.reply_text(LanguageManager.get("common.error"))
        return USER_ACTION

    context.user_data.pop("delete_mode", None)
    context.user_data.pop("delete_target", None)

    if success:
        if mode == "account":
            await update.message.reply_text(
                LanguageManager.get(msg_key, username=display),
                reply_markup=_admin_main_back(),
                parse_mode="Markdown",
            )
            return ConversationHandler.END
        await update.message.reply_text(
            LanguageManager.get(msg_key, username=display) if mode == "ovpn"
            else LanguageManager.get(msg_key, uid=display),
            reply_markup=_user_hub_back(hub_id) if hub_id else _admin_main_back(),
            parse_mode="Markdown",
        )
        if hub_id and mode != "account":
            return USER_ACTION
    else:
        await update.message.reply_text(
            LanguageManager.get("common.error"),
            reply_markup=_user_hub_back(hub_id) if hub_id else _admin_main_back(),
        )
    return USER_ACTION if hub_id else ConversationHandler.END


async def admin_user_tickets_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split("_")[-1])
    user = await get_user_by_id(user_id)
    tickets = await get_user_tickets(user_id)
    if not tickets:
        await query.edit_message_text(
            LanguageManager.get("admin.user.no_tickets"),
            reply_markup=_user_hub_back(user_id),
        )
        return USER_ACTION
    text = LanguageManager.get(
        "admin.user.tickets_title",
        name=escape_markdown(user.full_name or "N/A", version=1),
    )
    keyboard = []
    for ticket in tickets[:15]:
        emoji = {"open": "🟢", "waiting_admin": "🔴", "waiting_user": "⏳", "closed": "✅"}.get(
            ticket.status, "🔵"
        )
        text += f"{emoji} **#{ticket.id}**: {escape_markdown(ticket.subject[:40], version=1)}\n"
        keyboard.append([
            InlineKeyboardButton(
                LanguageManager.get("admin.tickets.btn_view", id=ticket.id),
                callback_data=f"admin_ticket_{ticket.id}",
            )
        ])
    keyboard.append([
        InlineKeyboardButton(
            LanguageManager.get("admin.user.btn_back_hub"),
            callback_data=f"admin_user_hub_{user_id}",
        )
    ])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return USER_ACTION


async def admin_user_receipts_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split("_")[-1])
    receipts = await get_user_receipts_summary(user_id, limit=15)
    if not receipts:
        await query.edit_message_text(
            LanguageManager.get("admin.user.no_receipts"),
            reply_markup=_user_hub_back(user_id),
        )
        return USER_ACTION
    text = LanguageManager.get("admin.user.receipts_title") + "\n"
    keyboard = []
    for r in receipts:
        emoji = "⏳" if r.status == "pending" else ("✅" if r.status == "approved" else "❌")
        amt = await format_currency(r.amount)
        text += f"{emoji} #{r.id} — {amt} ({r.status})\n"
        if r.status == "pending":
            keyboard.append([
                InlineKeyboardButton(
                    f"📋 #{r.id}",
                    callback_data=f"view_receipt_{r.id}",
                )
            ])
    keyboard.append([
        InlineKeyboardButton(
            LanguageManager.get("admin.user.btn_back_hub"),
            callback_data=f"admin_user_hub_{user_id}",
        )
    ])
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")
    return USER_ACTION


async def admin_user_txns_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split("_")[-1])
    txns = await get_user_transactions(user_id, limit=10)
    if not txns:
        await query.edit_message_text(
            LanguageManager.get("admin.user.no_transactions"),
            reply_markup=_user_hub_back(user_id),
        )
        return USER_ACTION
    code_ids = {
        t.discount_code_id for t in txns if getattr(t, "discount_code_id", None)
    }
    codes_by_id: dict = {}
    if code_ids:
        from vpn_bot.database import AsyncSessionLocal
        from vpn_bot.models import DiscountCode
        from sqlalchemy import select

        async with AsyncSessionLocal() as session:
            c_res = await session.execute(
                select(DiscountCode).where(DiscountCode.id.in_(code_ids))
            )
            for code_row in c_res.scalars().all():
                codes_by_id[code_row.id] = code_row

    text = LanguageManager.get("admin.user.transactions_title")
    for t in txns:
        text += await format_transaction_summary(t, codes_by_id)
    body = _format_history_separators_rtl(text)
    await query.edit_message_text(
        body,
        reply_markup=_user_hub_back(user_id),
        parse_mode="Markdown",
    )
    return USER_ACTION


async def admin_notify_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = int(query.data.split("_")[-1])
    user = await get_user_by_id(user_id)
    if not user:
        await query.answer(LanguageManager.get("common.error"), show_alert=True)
        return USER_ACTION
    context.user_data["notify_target_id"] = user.telegram_id
    context.user_data["admin_target_user_id"] = user_id
    await query.edit_message_text(
        LanguageManager.get("admin.user.notify_prompt", id=user.telegram_id),
        reply_markup=_user_hub_back(user_id),
        parse_mode="Markdown",
    )
    return USER_NOTIFY_MSG


async def process_admin_user_notify(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message.text
    target_id = context.user_data.pop("notify_target_id", None)
    hub_id = context.user_data.get("admin_target_user_id")
    status = await update.message.reply_text(LanguageManager.get("admin.notify.processing"))
    mgr = NotificationManager(context.bot)
    if target_id:
        success = await mgr.send_to_user(target_id, msg)
        res = (
            LanguageManager.get("admin.notify.targeted_success", id=target_id)
            if success
            else LanguageManager.get("admin.notify.targeted_fail", id=target_id)
        )
    else:
        res = LanguageManager.get("common.error")
    await status.edit_text(res, parse_mode="Markdown")
    if hub_id:
        await update.message.reply_text(
            LanguageManager.get("admin.user.btn_back_hub"),
            reply_markup=_user_hub_back(hub_id),
        )
    return USER_ACTION


async def admin_shared_ovpn_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    username = query.data.split("_", 2)[2]
    context.user_data["shared_lookup_username"] = username
    current = await get_user_shared_count_from_mt(username)
    hub_id = context.user_data.get("admin_target_user_id")
    await query.edit_message_text(
        LanguageManager.get("admin.shared.user_info", username=username, current=current or "?"),
        reply_markup=_user_hub_back(hub_id) if hub_id else _admin_main_back(),
        parse_mode="Markdown",
    )
    return USER_SHARED


async def process_shared_from_hub(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        value = int(update.message.text.strip())
        if value < 1 or value > 10:
            raise ValueError()
        username = context.user_data.pop("shared_lookup_username", None)
        hub_id = context.user_data.get("admin_target_user_id")
        if not username:
            await update.message.reply_text(LanguageManager.get("common.error"))
            return USER_ACTION
        success = await set_user_shared_count_on_mt(username, value)
        markup = _user_hub_back(hub_id) if hub_id else _admin_main_back()
        if success:
            await update.message.reply_text(
                LanguageManager.get("admin.shared.user_success", username=username, value=value),
                reply_markup=markup,
            )
        else:
            await update.message.reply_text(
                LanguageManager.get("admin.shared.user_not_found", username=username),
                reply_markup=markup,
            )
    except ValueError:
        await update.message.reply_text(LanguageManager.get("admin.shared.invalid_value"))
    return USER_SHARED


_user_hub_back_handler = CallbackQueryHandler(admin_user_hub_callback, pattern=r"^admin_user_hub_\d+$")

admin_search_user_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(search_user_start, pattern="^search_user$")],
    states=_with_conv_cancel({
        SEARCH_USERNAME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, display_user_info),
            CallbackQueryHandler(admin_user_recent, pattern="^admin_user_recent$"),
            CallbackQueryHandler(admin_user_search_callback, pattern="^admin_user_search$"),
            _admin_start_back_handler,
        ],
        PICK_USER: [
            CallbackQueryHandler(admin_user_hub_callback, pattern=r"^admin_user_hub_\d+$"),
            CallbackQueryHandler(admin_user_search_callback, pattern="^admin_user_search$"),
            _admin_start_back_handler,
        ],
        USER_ACTION: [
            CallbackQueryHandler(admin_user_hub_callback, pattern=r"^admin_user_hub_\d+$"),
            CallbackQueryHandler(admin_user_search_callback, pattern="^admin_user_search$"),
            CallbackQueryHandler(user_ovpn_page_callback, pattern=r"^user_ovpn_page_\d+_\d+$"),
            CallbackQueryHandler(user_wg_page_callback, pattern=r"^user_wg_page_\d+_\d+$"),
            CallbackQueryHandler(manage_sub_redirect, pattern="^manage_sub_"),
            CallbackQueryHandler(manage_wg_redirect, pattern="^manage_wg_"),
            CallbackQueryHandler(ban_user_flow, pattern="^ban_user_"),
            CallbackQueryHandler(unban_user_flow, pattern="^unban_user_"),
            CallbackQueryHandler(admin_wg_config_for_sub, pattern="^admin_wg_cfg_"),
            CallbackQueryHandler(edit_balance_by_uid_flow, pattern="^edit_balance_byuid_"),
            CallbackQueryHandler(admin_user_tickets_view, pattern=r"^admin_user_tickets_\d+$"),
            CallbackQueryHandler(admin_user_receipts_view, pattern=r"^admin_user_receipts_\d+$"),
            CallbackQueryHandler(admin_user_txns_view, pattern=r"^admin_user_txns_\d+$"),
            CallbackQueryHandler(admin_notify_user_start, pattern=r"^admin_notify_user_\d+$"),
            CallbackQueryHandler(admin_shared_ovpn_start, pattern="^admin_shared_"),
            CallbackQueryHandler(reset_password_flow, pattern="^reset_pass_"),
            CallbackQueryHandler(add_data_flow, pattern="^add_data_"),
            CallbackQueryHandler(extend_time_flow, pattern="^extend_time_"),
            CallbackQueryHandler(wg_extend_flow, pattern="^wg_extend_"),
            CallbackQueryHandler(wg_add_data_flow, pattern="^wg_add_data_"),
            CallbackQueryHandler(wg_toggle_flow, pattern="^wg_toggle_"),
            CallbackQueryHandler(edit_balance_flow, pattern="^edit_balance_"),
            CallbackQueryHandler(disable_user_flow, pattern="^disable_user_"),
            CallbackQueryHandler(delete_ovpn_sub_flow, pattern="^delete_ovpn_sub_"),
            CallbackQueryHandler(delete_wg_sub_flow, pattern="^delete_wg_sub_"),
            CallbackQueryHandler(delete_account_flow, pattern="^delete_account_"),
            _admin_start_back_handler,
        ],
        RESET_PASS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, process_reset_password),
            _user_hub_back_handler,
            _admin_start_back_handler,
        ],
        ADD_DATA: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, process_add_data),
            _user_hub_back_handler,
            _admin_start_back_handler,
        ],
        EXTEND_TIME: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, process_extend_time),
            _user_hub_back_handler,
            _admin_start_back_handler,
        ],
        WG_EXTEND: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, process_wg_extend),
            _user_hub_back_handler,
            _admin_start_back_handler,
        ],
        WG_ADD_DATA: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, process_wg_add_data),
            _user_hub_back_handler,
            _admin_start_back_handler,
        ],
        EDIT_BALANCE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, process_edit_balance),
            _user_hub_back_handler,
            _admin_start_back_handler,
        ],
        USER_NOTIFY_MSG: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, process_admin_user_notify),
            _user_hub_back_handler,
            _admin_start_back_handler,
        ],
        USER_SHARED: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, process_shared_from_hub),
            _user_hub_back_handler,
            _admin_start_back_handler,
        ],
        DELETE_CONFIRM: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, process_delete_confirm),
            _user_hub_back_handler,
            _admin_start_back_handler,
        ],
    }),
    fallbacks=admin_conversation_fallbacks(),
)
