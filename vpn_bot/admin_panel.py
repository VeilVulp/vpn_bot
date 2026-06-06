import os
import asyncio
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from datetime import datetime

from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.admin_management import (
    is_user_admin,
    is_super_admin,
    add_admin,
    remove_admin,
    list_admins,
    set_admin_permissions,
    full_permission_preset,
    limited_permission_preset,
    get_admin_permissions,
)
from vpn_bot.notification_manager import NotificationManager
from vpn_bot.admin_audit import audit_log
from vpn_bot.bot_handler import MENU_BUTTONS_FILTER
from vpn_bot.admin_conversation import admin_exit_to_menu
from vpn_bot.config import config
from vpn_bot.utils import (
    logger,
    LanguageManager,
    get_profile_price,
    format_currency,
    safe_response,
)
from vpn_bot.admin_cleanup import AdminCleanup
from vpn_bot.settings_utils import get_admin_setting
from vpn_bot.admin_server_service import (
    get_multi_server_health,
    format_server_list_text, get_server_by_id, create_server,
    update_server, delete_server, toggle_server_status,
    get_server_upstream_interfaces, set_server_upstream_interface,
    get_servers_for_admin_list,
)
from vpn_bot.admin_profile_service import (
    get_all_profiles, create_profile_full,
    update_profile, delete_profile_full
)
from vpn_bot.admin_ovpn_service import (
    get_all_ovpn_configs, create_ovpn_config, 
    update_ovpn_config, delete_ovpn_config
)
from vpn_bot.admin_shared_service import (
    get_default_shared_users, set_default_shared_users, 
    get_subscription_by_username, get_user_shared_count_from_mt, 
    set_user_shared_count_on_mt
)
from vpn_bot.admin_panel_shared import *  # noqa: F401,F403
from vpn_bot.admin_panel_shared import _with_conv_cancel, _admin_start_back_handler  # noqa: F401

@safe_response
async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin main menu."""
    user_id = update.effective_user.id
    if not await is_user_admin(user_id):
        return # Silent fail for security

    from vpn_bot.admin_menu import build_admin_main_keyboard, format_admin_menu_title
    from vpn_bot.coupon_flow import clear_coupon_flow

    clear_coupon_flow(context.user_data)

    reply_markup = await build_admin_main_keyboard(user_id)
    text = format_admin_menu_title()
    
    await universal_reply(update, text, reply_markup=reply_markup, parse_mode='Markdown')
    return ConversationHandler.END

# --- Server Management ---

@safe_response
async def list_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored list_servers using admin_server_service."""
    from vpn_bot.admin_server_service import build_server_list_keyboard

    servers = await get_servers_for_admin_list()
    max_show = 25
    markup = build_server_list_keyboard(servers, max_manage=max_show)

    if not servers:
        text = (
            LanguageManager.get("admin.server.list_title")
            + LanguageManager.get("admin.server.no_servers")
        )
        await universal_reply(update, text, reply_markup=markup)
        return ConversationHandler.END

    display_servers = servers[:max_show]
    health_map = await get_multi_server_health(display_servers)
    text = await format_server_list_text(servers, health_map, max_lines=max_show)
    await universal_reply(update, text, reply_markup=markup)
    return ConversationHandler.END

# --- Server Addition Steps ---
SERVER_NAME, SERVER_HOST, SERVER_USER, SERVER_PASS, SERVER_PORT = range(30, 35)

async def prompt_server_name(update, context):
    await admin_conv_prompt(update, LanguageManager.get('admin.server.add_step_1'))
    return SERVER_NAME

async def prompt_server_host(update, context):
    await admin_conv_prompt(update, LanguageManager.get('admin.server.add_step_2'))
    return SERVER_HOST

async def prompt_server_user(update, context):
    await admin_conv_prompt(update, LanguageManager.get('admin.server.add_step_3'))
    return SERVER_USER

async def prompt_server_pass(update, context):
    await admin_conv_prompt(update, LanguageManager.get('admin.server.add_step_4'))
    return SERVER_PASS

async def prompt_server_port(update, context):
    await admin_conv_prompt(update, LanguageManager.get('admin.server.add_step_5'))
    return SERVER_PORT

async def server_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await prompt_server_name(update, context)

async def server_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_server_name'] = update.message.text.strip()
    return await prompt_server_host(update, context)

async def server_host_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_server_host'] = update.message.text.strip()
    return await prompt_server_user(update, context)

async def server_user_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_server_user'] = update.message.text.strip()
    return await prompt_server_pass(update, context)

async def server_pass_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_server_pass'] = update.message.text.strip()
    return await prompt_server_port(update, context)

async def server_port_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored server addition completion."""
    text = update.message.text.strip()
    try:
        port = int(text)
    except ValueError:
        port = 8728
    
    data = {
        'name': context.user_data['new_server_name'],
        'host': context.user_data['new_server_host'],
        'username': context.user_data['new_server_user'],
        'password': context.user_data['new_server_pass'],
        'port': port
    }
    
    new_server = await create_server(data)
    
    if new_server:
        await update.message.reply_text(LanguageManager.get('admin.server.added_success', name=new_server.name), parse_mode='Markdown')
    else:
        await update.message.reply_text(LanguageManager.get('common.error'))
        
    return ConversationHandler.END

async def test_server_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored test_server_connection using get_server_by_id."""
    query = update.callback_query
    server_id = int(query.data.split('_')[2])
    
    await query.answer(LanguageManager.get('admin.server.test_conn'), cache_time=0)
    server = await get_server_by_id(server_id)
    
    if not server:
        await query.edit_message_text(LanguageManager.get('admin.server.not_found'))
        return
        
    start_time = datetime.now()
    host_port = f"{server.host}:{server.port or 8728}"
    try:
        mgr = get_mikrotik_manager(server)
        await asyncio.to_thread(mgr.connect_with_retry)

        def get_stats():
            res = mgr._get_resource('/system/resource').get()[0]
            sessions = len(mgr._get_resource('/user-manager/session').get(active='true'))
            return res, sessions

        res_data, active_sessions = await asyncio.to_thread(get_stats)

        latency = (datetime.now() - start_time).total_seconds() * 1000
        logger.info("Server connection test OK %s latency=%.0fms", host_port, latency)
        await asyncio.to_thread(mgr.close)
        
        info = LanguageManager.get('admin.server.conn_success',
            name=escape_markdown(server.name, version=1),
            latency=latency,
            cpu=res_data.get('cpu-load'),
            version=res_data.get('version'),
            uptime=res_data.get('uptime'),
            active=active_sessions
        )
        
        keyboard = [[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='list_servers')]]
        await query.edit_message_text(info, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        
    except Exception as e:
        logger.error("Test failed for %s (%s): %s", server.name, host_port, e)
        await query.edit_message_text(
            LanguageManager.get('admin.server.conn_fail', 
                                 name=escape_markdown(server.name, version=1), 
                                 error=escape_markdown(str(e), version=1)),
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='list_servers')]])
        )

# Server Edit and Delete
SERVER_EDIT_SELECT, SERVER_EDIT_VALUE = range(40, 42)
SERVER_EDIT_SELECT, SERVER_EDIT_VALUE = range(40, 42)
SERVER_DELETE_CONFIRM = 42
SERVER_UPSTREAM_SELECT = 43

def _server_edit_menu_markup(server_id: int, server_name: str) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.server.add_step_1').split('\n')[0].split(': ')[1], callback_data='edit_srv_name')],
        [InlineKeyboardButton(LanguageManager.get('admin.server.add_step_2').split('\n')[0].split(': ')[1], callback_data='edit_srv_host')],
        [InlineKeyboardButton(LanguageManager.get('admin.server.add_step_3').split('\n')[0].split(': ')[1], callback_data='edit_srv_user')],
        [InlineKeyboardButton(LanguageManager.get('admin.server.add_step_4').split('\n')[0].split(': ')[1], callback_data='edit_srv_pass')],
        [InlineKeyboardButton(LanguageManager.get('admin.server.add_step_5').split('\n')[0].split(': ')[1], callback_data='edit_srv_port')],
        [InlineKeyboardButton(LanguageManager.get('admin.server.test_conn'), callback_data=f'server_test_{server_id}')],
        [InlineKeyboardButton(LanguageManager.get('admin.user.btn_toggle'), callback_data='edit_srv_toggle')],
        [InlineKeyboardButton(LanguageManager.get('admin.server.btn_delete'), callback_data=f'server_delete_{server_id}')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='list_servers')],
    ]
    return InlineKeyboardMarkup(keyboard)


async def _show_server_edit_menu(update: Update, server_id: int) -> int | None:
    """Render server edit screen. Returns SERVER_EDIT_SELECT or None if missing."""
    server = await get_server_by_id(server_id)
    if not server:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                LanguageManager.get('admin.server.not_found')
            )
        return None
    text = LanguageManager.get('admin.server.btn_manage', name=server.name)
    markup = _server_edit_menu_markup(server_id, server.name)
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=markup, parse_mode='Markdown'
        )
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')
    return SERVER_EDIT_SELECT


async def server_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Open server edit menu (entry: server_edit_{id} or refresh from edit_srv_*)."""
    query = update.callback_query
    if query:
        await query.answer()

    if query and query.data and query.data.startswith('server_edit_'):
        server_id = int(query.data.rsplit('_', 1)[-1])
        context.user_data['edit_server_id'] = server_id
    else:
        server_id = context.user_data.get('edit_server_id')
        if not server_id:
            if query:
                await query.edit_message_text(LanguageManager.get('common.error'))
            return ConversationHandler.END

    state = await _show_server_edit_menu(update, server_id)
    if state is None:
        return ConversationHandler.END
    return state

async def server_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    field = query.data.replace('edit_srv_', '')
    server_id = context.user_data.get('edit_server_id')
    
    if field == 'toggle':
        success, new_active = await toggle_server_status(server_id)
        if success:
            status = "enabled" if new_active else "disabled"
            await query.answer(LanguageManager.get('admin.server.toggle_success', status=status), show_alert=True)
        state = await _show_server_edit_menu(update, server_id)
        return state if state is not None else ConversationHandler.END
    
    if field == 'upstream':
        interfaces = await get_server_upstream_interfaces(server_id)
        if interfaces is not None:
            keyboard = []
            for iface in interfaces:
                 status = LanguageManager.get('admin.server.status_running') if iface['running'] else LanguageManager.get('admin.server.status_disconnected')
                 btn_text = LanguageManager.get('admin.server.btn_upstream_item', iface=iface['name'], status=status)
                 keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"set_upstream_{iface['name']}")])
            keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data=f"server_edit_{server_id}")])
            
            text = LanguageManager.get('admin.server.upstream_prompt')
            
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            return SERVER_UPSTREAM_SELECT
    
    context.user_data['edit_field'] = field
    
    prompts = {
        'name': LanguageManager.get('admin.server.add_step_1'),
        'host': LanguageManager.get('admin.server.add_step_2'),
        'user': LanguageManager.get('admin.server.add_step_3'),
        'pass': LanguageManager.get('admin.server.add_step_4'),
        'port': LanguageManager.get('admin.server.add_step_5')
    }
    
    await query.edit_message_text(prompts.get(field, LanguageManager.get('common.processing')))
    return SERVER_EDIT_VALUE

async def server_edit_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored server_edit_save."""
    value = update.message.text.strip()
    field = context.user_data.get('edit_field')
    server_id = context.user_data.get('edit_server_id')
    
    # Map internal field names to DB model fields if they differ
    field_map = {'user': 'username', 'pass': 'password'}
    db_field = field_map.get(field, field)
    
    data = {db_field: value}
    if db_field == 'port':
        try: data['port'] = int(value)
        except ValueError:
            await update.message.reply_text(LanguageManager.get('common.error'))
            return SERVER_EDIT_VALUE
            
    success = await update_server(server_id, data)
    if success:
        admin_id = update.effective_user.id if update.effective_user else 0
        await audit_log(
            admin_id,
            "server_edit",
            target_type="server",
            target_id=str(server_id),
            detail={"field": db_field},
        )
        await update.message.reply_text(LanguageManager.get('common.success'))
    else:
        await update.message.reply_text(LanguageManager.get('admin.server.not_found'))
        
    return await admin_start(update, context)



async def set_upstream_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored set_upstream_callback using service."""
    query = update.callback_query
    await query.answer()
    
    iface_name = query.data.replace('set_upstream_', '')
    server_id = context.user_data.get('edit_server_id')
    
    success = await set_server_upstream_interface(server_id, iface_name)
    
    keyboard = [[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data=f"server_edit_{server_id}")]]
    
    if success:
        await query.edit_message_text(LanguageManager.get('admin.server.upstream_success', iface=iface_name), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await query.edit_message_text(LanguageManager.get('admin.server.upstream_error'), reply_markup=InlineKeyboardMarkup(keyboard))
            
    return SERVER_UPSTREAM_SELECT

async def server_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm server deletion (from server edit menu or entry point)."""
    query = update.callback_query
    await query.answer()
    server_id = int(query.data.rsplit("_", 1)[-1])
    context.user_data["delete_server_id"] = server_id

    server = await get_server_by_id(server_id)
    if not server:
        await query.edit_message_text(LanguageManager.get("admin.server.not_found"))
        return ConversationHandler.END

    back_markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LanguageManager.get("common.back"),
                    callback_data=f"server_edit_{server_id}",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.server.btn_back_list"),
                    callback_data="list_servers",
                )
            ],
        ]
    )
    await query.edit_message_text(
        LanguageManager.get("admin.server.delete_prompt", name=server.name),
        reply_markup=back_markup,
        parse_mode="Markdown",
    )
    return SERVER_DELETE_CONFIRM


async def server_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete server after user types DELETE."""
    text = update.message.text.strip()
    server_id = context.user_data.get("delete_server_id")

    if text != "DELETE":
        await update.message.reply_text(LanguageManager.get("common.cancelled"))
        return ConversationHandler.END

    success, result = await delete_server(server_id)
    context.user_data.pop("delete_server_id", None)
    list_btn = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.server.btn_back_list"),
                    callback_data="list_servers",
                )
            ]
        ]
    )

    if success:
        admin_id = update.effective_user.id if update.effective_user else 0
        await audit_log(
            admin_id,
            "server_delete",
            target_type="server",
            target_id=str(server_id),
            detail={"name": result},
        )
        await update.message.reply_text(
            LanguageManager.get("admin.server.delete_success", name=result),
            reply_markup=list_btn,
            parse_mode="Markdown",
        )
    elif result == "blocked_dependencies":
        await update.message.reply_text(
            LanguageManager.get("admin.server.delete_blocked"),
            reply_markup=list_btn,
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            LanguageManager.get("admin.server.not_found"),
            reply_markup=list_btn,
        )

    return ConversationHandler.END

admin_server_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(server_add_start, pattern='^server_add$'),
        CallbackQueryHandler(server_edit_start, pattern='^server_edit_'),
        CallbackQueryHandler(server_delete_start, pattern='^server_delete_')
    ],
    states=_with_conv_cancel({
        SERVER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, server_name_received), CommandHandler('edit', list_servers)],
        SERVER_HOST: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, server_host_received), CommandHandler('edit', prompt_server_name)],
        SERVER_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, server_user_received), CommandHandler('edit', prompt_server_host)],
        SERVER_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, server_pass_received), CommandHandler('edit', prompt_server_user)],
        SERVER_PORT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, server_port_received), CommandHandler('edit', prompt_server_pass)],
        SERVER_EDIT_SELECT: [
            CallbackQueryHandler(server_edit_field, pattern='^edit_srv_'),
            CallbackQueryHandler(server_delete_start, pattern='^server_delete_'),
            CallbackQueryHandler(list_servers, pattern='^list_servers$'),
            CallbackQueryHandler(test_server_connection, pattern='^server_test_')
        ],
        SERVER_UPSTREAM_SELECT: [
            CallbackQueryHandler(set_upstream_callback, pattern='^set_upstream_'),
            CallbackQueryHandler(server_edit_start, pattern='^server_edit_')
        ],
        SERVER_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, server_edit_save)],
        SERVER_DELETE_CONFIRM: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, server_delete_confirm),
            CallbackQueryHandler(server_edit_start, pattern='^server_edit_'),
            CallbackQueryHandler(list_servers, pattern='^list_servers$'),
        ],
    }),
    fallbacks=admin_conversation_fallbacks(),
)


# --- Profile Management ---

@safe_response
async def list_profiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored list_profiles using admin_profile_service."""
    profiles = await get_all_profiles()
    text = LanguageManager.get('admin.profile.list_title')
    keyboard_buttons = []
    
    if not profiles:
        text += LanguageManager.get('admin.profile.no_profiles')
    else:
        for p in profiles:
            price_val = await get_profile_price(p)
            display_price = await format_currency(price_val)
            safe_name = escape_markdown(p.name, version=1)
            text += LanguageManager.get('admin.profile.item', name=safe_name, version=p.version, price=display_price, data=p.data_limit_gb, days=p.validity_days, count='?')
            
            btn_label = LanguageManager.get('admin.btn_edit_speed_name', label=LanguageManager.get('admin.btn_edit_speed'), name=p.name)
            keyboard_buttons.append([InlineKeyboardButton(btn_label, callback_data=f"edit_speed_{p.id}")])
            keyboard_buttons.append([
                InlineKeyboardButton(LanguageManager.get('admin.profile.btn_edit_name'), callback_data=f"edit_prof_name_{p.id}"),
            ])
            keyboard_buttons.append([
                InlineKeyboardButton(LanguageManager.get('admin.profile.btn_edit_price'), callback_data=f"edit_prof_price_{p.id}"),
            ])
            keyboard_buttons.append([
                InlineKeyboardButton(LanguageManager.get('admin.profile.btn_delete'), callback_data=f"del_profile_{p.id}"),
            ])
            
    keyboard_buttons.append([InlineKeyboardButton(LanguageManager.get('admin.profile.btn_add'), callback_data='add_profile')])
    keyboard_buttons.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='ovpn_l2tp_mgmt_menu')])
    await universal_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard_buttons))
    return ConversationHandler.END

async def edit_profile_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pid = int(query.data.split('_')[3])
    context.user_data['edit_profile_id'] = pid
    await query.answer()
    await universal_reply(update, LanguageManager.get('admin.profile.prompt_edit_name'))
    return PROFILE_EDIT_NAME

async def edit_profile_name_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored edit_profile_name_finish using admin_profile_service."""
    new_name = update.message.text.strip()
    pid = context.user_data.get('edit_profile_id')
    
    success = await update_profile(pid, {'name': new_name})
    if success:
        await update.message.reply_text(LanguageManager.get('common.success_update'))
    else:
        await update.message.reply_text(LanguageManager.get('common.error'))
        
    return ConversationHandler.END

async def edit_profile_price_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pid = int(query.data.split('_')[3])
    context.user_data['edit_profile_id'] = pid
    await query.answer()
    await universal_reply(update, LanguageManager.get('admin.profile.prompt_edit_price'))
    return PROFILE_EDIT_PRICE

async def edit_profile_price_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored edit_profile_price_finish using admin_profile_service."""
    try:
        new_price = int(update.message.text.strip())
        pid = context.user_data.get('edit_profile_id')
        
        success = await update_profile(pid, {'price_toman': new_price})
        if success:
            await update.message.reply_text(LanguageManager.get('common.success_update'))
        else:
            await update.message.reply_text(LanguageManager.get('common.error'))
    except ValueError:
        await update.message.reply_text(LanguageManager.get('admin.profile.error_invalid_price'))
        return PROFILE_EDIT_PRICE
        
    return ConversationHandler.END

async def delete_profile_flow(update, context):
    """Refactored delete_profile_flow using admin_profile_service."""
    query = update.callback_query
    pid = int(query.data.split('_')[2])
    
    success, msg_detail = await delete_profile_full(pid)
    
    if success:
        if "Archived" in msg_detail:
            await query.answer(LanguageManager.get('admin.profile.archived_success'), show_alert=True)
        else:
            await query.answer(LanguageManager.get('admin.profile.success_delete'), show_alert=True)
    else:
        await query.answer(LanguageManager.get('common.error'), show_alert=True)
        
    await list_profiles(update, context)
    return ConversationHandler.END

async def prompt_profile_name(update, context):
    await admin_conv_prompt(update, LanguageManager.get('admin.profile.add_prompt_name'))
    return PROFILE_NAME

async def prompt_profile_data(update, context):
    await admin_conv_prompt(update, LanguageManager.get('admin.profile.add_prompt_data'))
    return PROFILE_DATA

async def prompt_profile_days(update, context):
    await admin_conv_prompt(update, LanguageManager.get('admin.profile.add_prompt_days'))
    return PROFILE_DAYS

async def prompt_profile_price_usd(update, context):
    await admin_conv_prompt(update, LanguageManager.get('admin.profile.add_prompt_price_usd'))
    return PROFILE_PRICE_USD

async def prompt_profile_price_toman(update, context):
    await admin_conv_prompt(update, LanguageManager.get('admin.profile.add_prompt_price_toman'))
    return PROFILE_PRICE_TOMAN

async def prompt_profile_server(update, context):
    servers = await get_servers_for_admin_list()
    if not servers:
        await universal_reply(update, LanguageManager.get('admin.profile.error_no_servers'))
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(s.name, callback_data=f"prof_srv_{s.id}")] for s in servers]
    await admin_conv_prompt(
        update,
        LanguageManager.get('admin.profile.add_prompt_server'),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return PROFILE_SERVER

async def add_profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE): return await prompt_profile_name(update, context)
async def get_profile_name(update, context): context.user_data['new_profile_name'] = update.message.text.strip(); return await prompt_profile_data(update, context)
async def get_profile_data(update, context):
    try: context.user_data['new_profile_data'] = int(update.message.text.strip()); return await prompt_profile_days(update, context)
    except: await update.message.reply_text(LanguageManager.get('admin.profile.error_invalid_gb')); return PROFILE_DATA
async def get_profile_days(update, context):
    try: 
        context.user_data['new_profile_days'] = int(update.message.text.strip())
        return await prompt_profile_price_usd(update, context)
    except: 
        await update.message.reply_text(LanguageManager.get('admin.profile.error_invalid_days'))
        return PROFILE_DAYS

async def get_profile_price_usd(update, context):
    try: 
        context.user_data['new_profile_price_usd'] = float(update.message.text.strip())
        return await prompt_profile_price_toman(update, context)
    except: 
        await update.message.reply_text(LanguageManager.get('admin.profile.error_invalid_price'))
        return PROFILE_PRICE_USD

async def get_profile_price_toman(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.message.text.isdigit():
        await update.message.reply_text(LanguageManager.get('admin.profile.error_invalid_price'))
        return PROFILE_PRICE_TOMAN
    context.user_data['new_profile_price_toman'] = int(update.message.text)
    await admin_conv_prompt(
        update,
        LanguageManager.get('admin.profile.add_prompt_rate_limit'),
        reply_markup=get_admin_edit_inline_keyboard('list_profiles'),
    )
    return PROFILE_RATE_LIMIT

async def get_profile_rate_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rate = update.message.text.strip()
    if rate.lower() == 'unlimited': rate = None
    context.user_data['new_profile_rate_limit'] = rate
    
    servers = await get_servers_for_admin_list()
    if not servers:
        await update.message.reply_text(LanguageManager.get('admin.profile.error_no_servers'))
        return ConversationHandler.END
    
    keyboard = []
    for s in servers:
        btn_text = LanguageManager.get('admin.server.btn_item', name=s.name)
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f'prof_srv_{s.id}')])
    
    await admin_conv_prompt(
        update,
        LanguageManager.get('admin.profile.add_prompt_server'),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return PROFILE_SERVER

async def select_profile_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored select_profile_server using admin_profile_service."""
    query = update.callback_query
    server_id = int(query.data.split('_')[2])
    data = context.user_data
    
    profile_data = {
        'name': data['new_profile_name'],
        'limit': data['new_profile_data'],
        'days': data['new_profile_days'],
        'price_usd': data.get('new_profile_price_usd', 0),
        'price_toman': data.get('new_profile_price_toman', 0),
        'rate_limit': data.get('new_profile_rate_limit'),
        'server_id': server_id
    }
    
    new_prof, error = await create_profile_full(profile_data)
    
    if new_prof:
        msg = LanguageManager.get('admin.profile.success', name=new_prof.name, server="Router") # Server name retrieval could be added if needed
    else:
        msg = LanguageManager.get('admin.profile.fail', server=error or "Unknown")
        
    await universal_reply(update, msg)
    return ConversationHandler.END

admin_profile_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(add_profile_start, pattern='^add_profile$'),
        CallbackQueryHandler(edit_profile_name_start, pattern='^edit_prof_name_'),
        CallbackQueryHandler(edit_profile_price_start, pattern='^edit_prof_price_'),
        CallbackQueryHandler(delete_profile_flow, pattern='^del_profile_')
    ],
    states=_with_conv_cancel({
        PROFILE_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_profile_name), CommandHandler('edit', list_profiles)],
        PROFILE_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_profile_data), CommandHandler('edit', prompt_profile_name)],
        PROFILE_DAYS: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_profile_days), CommandHandler('edit', prompt_profile_data)],
        PROFILE_PRICE_USD: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_profile_price_usd), CommandHandler('edit', prompt_profile_days)],
        PROFILE_PRICE_TOMAN: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_profile_price_toman), CommandHandler('edit', prompt_profile_price_usd)],
        PROFILE_RATE_LIMIT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_profile_rate_limit), CommandHandler('edit', prompt_profile_price_toman)],
        PROFILE_SERVER: [CallbackQueryHandler(select_profile_server, pattern='^prof_srv_'), CommandHandler('edit', get_profile_rate_limit)],
        PROFILE_EDIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_profile_name_finish)],
        PROFILE_EDIT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_profile_price_finish)],
    }),
    fallbacks=admin_conversation_fallbacks(),
)

# --- Backup, Admin MGMT, Notify, Clean ---

async def backup_menu(update, context):
    await update.callback_query.answer()
    from vpn_bot.admin_settings import get_admin_setting
    interval = await get_admin_setting('backup_interval_hours', "6h")
    group = config.BACKUP_GROUP_ID or LanguageManager.get('common.not_set')
    text = LanguageManager.get('admin.backup.menu', group=group, interval=interval)
    keyboard = [[InlineKeyboardButton(LanguageManager.get('admin.backup.btn_interval'), callback_data='backup_set_interval')],
                [InlineKeyboardButton(LanguageManager.get('admin.backup.btn_export'), callback_data='backup_export')],
                [InlineKeyboardButton(LanguageManager.get('admin.backup.btn_import'), callback_data='backup_import')],
                [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_start')]]
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def manual_export(update, context):
    await update.callback_query.answer(LanguageManager.get('common.processing'))
    from vpn_bot.backup_manager import BackupManager
    await BackupManager(context.bot).send_backup_to_telegram(update.effective_chat.id, is_auto=False)
    await update.callback_query.message.reply_text(LanguageManager.get('common.success'))
    return ConversationHandler.END

async def start_import(update, context):
    await update.callback_query.answer()
    await admin_conv_prompt(
        update,
        LanguageManager.get('admin.backup.import_warning'),
        reply_markup=get_admin_edit_inline_keyboard('backup_menu'),
    )
    return WAIT_IMPORT_FILE

async def process_import(update, context):
    if not update.message.document or not update.message.document.file_name.endswith('.sql'):
        await update.message.reply_text(LanguageManager.get('common.error'))
        return WAIT_IMPORT_FILE
    await update.message.reply_text(LanguageManager.get('common.processing'))
    f = await update.message.document.get_file()
    path = f"temp_restore_{datetime.now().timestamp()}.sql"
    await f.download_to_drive(path)
    
    context.user_data['restore_path'] = path
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.backup.btn_confirm_restore'), callback_data='confirm_restore_db')],
        [InlineKeyboardButton(LanguageManager.get('admin.backup.btn_cancel_restore'), callback_data='cancel_restore_db')]
    ]
    await update.message.reply_text(
        LanguageManager.get('admin.backup.import_confirm'),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return CONFIRM_RESTORE

async def confirm_restore_action(update, context):
    query = update.callback_query
    await query.answer()
    
    path = context.user_data.pop('restore_path', None)
    
    if query.data == 'cancel_restore_db':
        if path and os.path.exists(path):
            os.remove(path)
        await query.edit_message_text(LanguageManager.get('common.cancelled'))
        return ConversationHandler.END
        
    if query.data == 'confirm_restore_db':
        if not path or not os.path.exists(path):
            await query.edit_message_text(LanguageManager.get('common.error'))
            return ConversationHandler.END
            
        await query.edit_message_text(LanguageManager.get('common.processing'))
        from vpn_bot.backup_manager import BackupManager
        success = await BackupManager.restore_database(path)
        
        if os.path.exists(path): 
            os.remove(path)
            
        text = LanguageManager.get('admin.backup.restore_success') if success else LanguageManager.get('common.error')
        await query.edit_message_text(text)
        return ConversationHandler.END

BACKUP_INTERVAL = 34

async def set_backup_interval_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start setting backup interval."""
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(
        LanguageManager.get('admin.settings.backup_interval_prompt'),
        reply_markup=get_admin_edit_inline_keyboard('backup_menu'),
        parse_mode='Markdown',
    )
    return BACKUP_INTERVAL

async def receive_backup_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and save backup interval with robust validation."""
    import re
    from vpn_bot.utils import parse_duration_to_seconds, format_seconds_human
    from vpn_bot.admin_settings import set_admin_setting
    
    back = get_admin_edit_inline_keyboard('backup_menu')
    raw = update.message.text
    if not raw or not raw.strip():
        await update.message.reply_text(LanguageManager.get('admin.settings.backup_interval_invalid'), reply_markup=back)
        return BACKUP_INTERVAL
    
    val_str = raw.strip()
    
    # Reject pure text / alphabetic-only input
    if re.fullmatch(r'[a-zA-Z\u0600-\u06FF\s]+', val_str):
        await update.message.reply_text(LanguageManager.get('admin.settings.backup_interval_invalid'), reply_markup=back)
        return BACKUP_INTERVAL
    
    seconds = parse_duration_to_seconds(val_str)
    
    if seconds <= 0:
        await update.message.reply_text(LanguageManager.get('admin.settings.backup_interval_invalid'), reply_markup=back)
        return BACKUP_INTERVAL

    # Minimum 1 minute (60s) for backups
    if seconds < 60:
        await update.message.reply_text(
            LanguageManager.get('admin.settings.backup_interval_min_warn'),
            reply_markup=back,
        )
        seconds = 60
        val_str = "1m"
    
    # Normalize: if user enters plain number like "6", store as "6h"
    if re.fullmatch(r'\d+(\.\d+)?', val_str):
        val_str = f"{val_str}h"
    
    await set_admin_setting('backup_interval_hours', val_str)
    
    human = format_seconds_human(seconds)
    await update.message.reply_text(
        LanguageManager.get('admin.settings.backup_interval_updated', val=val_str, human=human),
        reply_markup=get_admin_edit_inline_keyboard('backup_menu'),
        parse_mode='Markdown',
    )
    return ConversationHandler.END

_backup_menu_back_handler = CallbackQueryHandler(backup_menu, pattern='^backup_menu$')

admin_backup_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(backup_menu, pattern='^backup_menu$'), 
        CallbackQueryHandler(manual_export, pattern='^backup_export$'), 
        CallbackQueryHandler(start_import, pattern='^backup_import$'),
        CallbackQueryHandler(set_backup_interval_start, pattern='^backup_set_interval$')
    ],
    states=_with_conv_cancel({
        WAIT_IMPORT_FILE: [
            MessageHandler(filters.Document.ALL, process_import),
            _backup_menu_back_handler,
        ],
        CONFIRM_RESTORE: [CallbackQueryHandler(confirm_restore_action, pattern='^(confirm_restore_db|cancel_restore_db)$')],
        BACKUP_INTERVAL: [
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, receive_backup_interval),
            _backup_menu_back_handler,
        ],
    }),
    fallbacks=admin_conversation_fallbacks(),
)

async def admin_mgmt_menu(update, context):
    await update.callback_query.answer()
    if not await is_super_admin(update.effective_user.id):
        await update.callback_query.edit_message_text(LanguageManager.get('admin.admin_mgmt.denied'))
        return ConversationHandler.END
    admins = await list_admins()
    supers = "".join([f"- `{sa}` (Perm)\n" for sa in config.ADMIN_IDS])
    db_lines = []
    for a in admins:
        db_lines.append(
            f"- `{a.telegram_id}` (@{a.username or 'N/A'})\n"
        )
    db_adm = "".join(db_lines) or LanguageManager.get('admin.admin_mgmt.no_db_admins')
    text = LanguageManager.get('admin.admin_mgmt.menu', super_admins=supers, db_admins=db_adm)
    keyboard = [
        [
            InlineKeyboardButton(LanguageManager.get('admin.admin_mgmt.btn_add'), callback_data='admin_add_start'),
            InlineKeyboardButton(LanguageManager.get('admin.admin_mgmt.btn_remove'), callback_data='admin_remove_start'),
        ],
    ]
    for a in admins:
        keyboard.append([
            InlineKeyboardButton(
                LanguageManager.get('admin.admin_mgmt.btn_edit_perms', id=a.telegram_id),
                callback_data=f'admin_pe_{a.telegram_id}',
            ),
        ])
    keyboard.append([
        InlineKeyboardButton(
            LanguageManager.get('admin.audit.btn_view'),
            callback_data='admin_audit_log',
        ),
    ])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_start')])
    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END


@safe_response
async def admin_audit_log_view(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if not await is_super_admin(update.effective_user.id):
        await query.edit_message_text(LanguageManager.get('admin.admin_mgmt.denied'))
        return ConversationHandler.END
    from vpn_bot.admin_audit_service import format_audit_log_text

    text = await format_audit_log_text(limit=50)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_mgmt_menu')],
    ])
    await query.edit_message_text(text, reply_markup=keyboard, parse_mode='Markdown')
    return ConversationHandler.END

ADMIN_PERM_EDIT = 12


def _build_permission_editor_keyboard(context) -> InlineKeyboardMarkup:
    from vpn_bot.admin_permissions import PERMISSION_UI_ENTRIES

    perms: set[str] = set(context.user_data.get('perm_edit_set', []))
    keyboard: list[list[InlineKeyboardButton]] = []
    for idx, (key, label_key) in enumerate(PERMISSION_UI_ENTRIES):
        on = key in perms
        icon = "✅" if on else "❌"
        keyboard.append([
            InlineKeyboardButton(
                f"{icon} {LanguageManager.get(label_key)}",
                callback_data=f'admin_pt_{idx}',
            )
        ])
    keyboard.append([
        InlineKeyboardButton(LanguageManager.get('admin.perm.preset_full'), callback_data='admin_ps_full'),
        InlineKeyboardButton(LanguageManager.get('admin.perm.preset_limited'), callback_data='admin_ps_limited'),
    ])
    keyboard.append([
        InlineKeyboardButton(LanguageManager.get('admin.perm.btn_save'), callback_data='admin_pf_save'),
    ])
    keyboard.append([
        InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_mgmt_menu'),
    ])
    return InlineKeyboardMarkup(keyboard)


async def _show_permission_editor(update, context, *, telegram_id: int, mode: str):
    from vpn_bot.admin_permissions import PERMISSION_UI_ENTRIES

    if mode == 'edit':
        context.user_data['perm_edit_set'] = set(await get_admin_permissions(telegram_id))
    else:
        context.user_data['perm_edit_set'] = set(full_permission_preset())
    context.user_data['perm_edit_tid'] = telegram_id
    context.user_data['perm_edit_mode'] = mode
    context.user_data['perm_key_list'] = [k for k, _ in PERMISSION_UI_ENTRIES]
    text = LanguageManager.get('admin.perm.editor_title', id=telegram_id)
    markup = _build_permission_editor_keyboard(context)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')


async def admin_perm_edit_start(update, context):
    query = update.callback_query
    await query.answer()
    if not await is_super_admin(update.effective_user.id):
        await query.edit_message_text(LanguageManager.get('admin.admin_mgmt.denied'))
        return ConversationHandler.END
    tid = int(query.data.split('_', 2)[2])
    await _show_permission_editor(update, context, telegram_id=tid, mode='edit')
    return ADMIN_PERM_EDIT


async def admin_perm_toggle(update, context):
    from vpn_bot.admin_permissions import PERMISSION_UI_ENTRIES

    query = update.callback_query
    await query.answer()
    idx = int(query.data.split('_')[-1])
    keys = [k for k, _ in PERMISSION_UI_ENTRIES]
    if 0 <= idx < len(keys):
        perms = set(context.user_data.get('perm_edit_set', []))
        key = keys[idx]
        if key in perms:
            perms.discard(key)
        else:
            perms.add(key)
        context.user_data['perm_edit_set'] = perms
    text = LanguageManager.get(
        'admin.perm.editor_title',
        id=context.user_data.get('perm_edit_tid', '?'),
    )
    await query.edit_message_text(
        text,
        reply_markup=_build_permission_editor_keyboard(context),
        parse_mode='Markdown',
    )
    return ADMIN_PERM_EDIT


async def admin_perm_preset(update, context):
    query = update.callback_query
    await query.answer()
    if query.data.endswith('full'):
        context.user_data['perm_edit_set'] = set(full_permission_preset())
    else:
        context.user_data['perm_edit_set'] = set(limited_permission_preset())
    text = LanguageManager.get(
        'admin.perm.editor_title',
        id=context.user_data.get('perm_edit_tid', '?'),
    )
    await query.edit_message_text(
        text,
        reply_markup=_build_permission_editor_keyboard(context),
        parse_mode='Markdown',
    )
    return ADMIN_PERM_EDIT


async def admin_perm_save(update, context):
    query = update.callback_query
    await query.answer()
    tid = context.user_data.get('perm_edit_tid')
    mode = context.user_data.get('perm_edit_mode')
    perms = set(context.user_data.get('perm_edit_set', []))
    if not tid:
        await query.edit_message_text(LanguageManager.get('common.error'))
        return ConversationHandler.END
    if mode == 'add':
        success = await add_admin(
            int(tid),
            added_by=update.effective_user.id,
            permissions=perms,
        )
        msg = (
            LanguageManager.get('admin.admin_mgmt.add_success', id=tid)
            if success
            else LanguageManager.get('admin.admin_mgmt.add_fail')
        )
    else:
        success = await set_admin_permissions(int(tid), perms)
        msg = (
            LanguageManager.get('admin.admin_mgmt.perms_saved', id=tid)
            if success
            else LanguageManager.get('common.error')
        )
    for k in ('perm_edit_tid', 'perm_edit_mode', 'perm_edit_set', 'perm_key_list', 'admin_mgmt_action'):
        context.user_data.pop(k, None)
    await query.edit_message_text(
        msg,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_mgmt_menu')]]
        ),
        parse_mode='Markdown',
    )
    return ConversationHandler.END


async def admin_mgmt_received(update, context):
    action = context.user_data.get('admin_mgmt_action')
    try:
        tid = int(update.message.text.strip())
    except Exception:
        await update.message.reply_text(
            LanguageManager.get('admin.admin_mgmt.invalid_id'),
            reply_markup=get_admin_edit_inline_keyboard('admin_mgmt_menu'),
        )
        return ADMIN_MGMT_ID
    if action == 'add':
        await _show_permission_editor(update, context, telegram_id=tid, mode='add')
        return ADMIN_PERM_EDIT
    if tid in config.ADMIN_IDS:
        msg = LanguageManager.get('admin.admin_mgmt.remove_super')
    else:
        success = await remove_admin(tid)
        msg = (
            LanguageManager.get('admin.admin_mgmt.remove_success', id=tid)
            if success
            else LanguageManager.get('admin.admin_mgmt.remove_fail')
        )
    await update.message.reply_text(
        msg,
        reply_markup=InlineKeyboardMarkup(
            [[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_mgmt_menu')]]
        ),
        parse_mode='Markdown',
    )
    return ConversationHandler.END

async def admin_mgmt_start(update, context):
    query = update.callback_query
    await query.answer()
    action = "add" if "add" in query.data else "remove"
    context.user_data['admin_mgmt_action'] = action
    prompt_key = (
        'admin.admin_mgmt.add_prompt' if action == "add" else 'admin.admin_mgmt.remove_prompt'
    )
    await admin_conv_prompt(
        update,
        LanguageManager.get(prompt_key),
        reply_markup=get_admin_edit_inline_keyboard('admin_mgmt_menu'),
    )
    return ADMIN_MGMT_ID

_admin_mgmt_menu_back_handler = CallbackQueryHandler(admin_mgmt_menu, pattern='^admin_mgmt_menu$')

admin_mgmt_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(admin_mgmt_menu, pattern='^admin_mgmt_menu$'),
        CallbackQueryHandler(admin_mgmt_start, pattern='^admin_(add|remove)_start$'),
        CallbackQueryHandler(admin_perm_edit_start, pattern='^admin_pe_\\d+$'),
        CallbackQueryHandler(admin_perm_toggle, pattern='^admin_pt_\\d+$'),
        CallbackQueryHandler(admin_perm_preset, pattern='^admin_ps_(full|limited)$'),
        CallbackQueryHandler(admin_perm_save, pattern='^admin_pf_save$'),
    ],
    states=_with_conv_cancel({
        ADMIN_MGMT_ID: [
            MessageHandler(filters.TEXT & ~filters.COMMAND, admin_mgmt_received),
            _admin_mgmt_menu_back_handler,
        ],
        ADMIN_PERM_EDIT: [
            CallbackQueryHandler(admin_perm_toggle, pattern='^admin_pt_\\d+$'),
            CallbackQueryHandler(admin_perm_preset, pattern='^admin_ps_(full|limited)$'),
            CallbackQueryHandler(admin_perm_save, pattern='^admin_pf_save$'),
            CallbackQueryHandler(admin_perm_edit_start, pattern='^admin_pe_\\d+$'),
            _admin_mgmt_menu_back_handler,
        ],
    }),
    fallbacks=admin_conversation_fallbacks(),
)

async def notification_menu(update, context):
    query = update.callback_query
    await query.answer()
    text = LanguageManager.get('admin.notify.menu')
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.notify.btn_broadcast'), callback_data='notify_broadcast')],
        [InlineKeyboardButton(LanguageManager.get('admin.notify.btn_targeted'), callback_data='notify_targeted')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_start')]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

def _notify_flow_back_markup():
    from vpn_bot.admin_menu import build_notification_back_markup
    return build_notification_back_markup()

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop('notify_target_id', None)
    await admin_conv_prompt(
        update,
        LanguageManager.get('admin.notify.broadcast_prompt'),
        reply_markup=_notify_flow_back_markup(),
    )
    return BROADCAST_MSG

async def targeted_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await admin_conv_prompt(
        update,
        LanguageManager.get('admin.notify.targeted_prompt'),
        reply_markup=_notify_flow_back_markup(),
    )
    return TARGETED_USER_ID

async def process_targeted_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = int(update.message.text.strip())
        context.user_data['notify_target_id'] = user_id
        await update.message.reply_text(
            LanguageManager.get('admin.notify.id_received', id=user_id),
            reply_markup=_notify_flow_back_markup(),
            parse_mode='Markdown',
        )
        return TARGETED_MSG
    except ValueError:
        await update.message.reply_text(LanguageManager.get('admin.admin_mgmt.invalid_id'))
        return TARGETED_USER_ID

async def process_notification(update, context):
    msg = update.message.text.strip()
    if not msg:
        await update.message.reply_text(LanguageManager.get('admin.notify.empty_message'))
        return BROADCAST_MSG if not context.user_data.get('notify_target_id') else TARGETED_MSG

    context.user_data['notify_pending_msg'] = msg
    target_id = context.user_data.get('notify_target_id')
    if target_id:
        preview = LanguageManager.get('admin.notify.preview_targeted', id=target_id, message=msg)
    else:
        preview = LanguageManager.get('admin.notify.preview_broadcast', message=msg)

    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(LanguageManager.get('admin.notify.btn_confirm_send'), callback_data='notify_confirm_send')],
        [InlineKeyboardButton(LanguageManager.get('common.cancel'), callback_data='notify_cancel_send')],
    ])
    await update.message.reply_text(preview, reply_markup=keyboard, parse_mode='Markdown')
    return BROADCAST_CONFIRM


async def confirm_notification_send(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if query.data == 'notify_cancel_send':
        context.user_data.pop('notify_pending_msg', None)
        context.user_data.pop('notify_target_id', None)
        await query.edit_message_text(LanguageManager.get('common.cancelled'))
        return ConversationHandler.END

    msg = context.user_data.pop('notify_pending_msg', None)
    target_id = context.user_data.pop('notify_target_id', None)
    if not msg:
        await query.edit_message_text(LanguageManager.get('admin.notify.empty_message'))
        return ConversationHandler.END

    admin_id = update.effective_user.id if update.effective_user else 0
    status = await query.edit_message_text(LanguageManager.get('admin.notify.processing'))
    mgr = NotificationManager(context.bot)
    if target_id:
        success = await mgr.send_to_user(target_id, msg)
        await audit_log(
            admin_id, "notify_targeted",
            target_type="user", target_id=str(target_id),
            detail={"preview": msg[:100]},
        )
        res = LanguageManager.get('admin.notify.targeted_success', id=target_id) if success else LanguageManager.get('admin.notify.targeted_fail', id=target_id)
    else:
        stats = await mgr.broadcast_to_all(msg)
        await audit_log(
            admin_id, "notify_broadcast",
            detail={"preview": msg[:100], "stats": stats},
        )
        res = LanguageManager.get('admin.notify.broadcast_result', **stats)
    await status.edit_text(res, parse_mode='Markdown')
    return ConversationHandler.END

_notify_back_handler = CallbackQueryHandler(notification_menu, pattern='^notification_menu$')

admin_notification_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(notification_menu, pattern='^notification_menu$'), 
        CallbackQueryHandler(broadcast_start, pattern='^notify_broadcast$'),
        CallbackQueryHandler(targeted_start, pattern='^notify_targeted$')
    ],
    states=_with_conv_cancel({
        BROADCAST_MSG: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, process_notification),
            _notify_back_handler,
        ],
        BROADCAST_CONFIRM: [
            CallbackQueryHandler(confirm_notification_send, pattern='^notify_(confirm|cancel)_send$'),
            _notify_back_handler,
        ],
        TARGETED_USER_ID: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, process_targeted_id),
            _notify_back_handler,
        ],
        TARGETED_MSG: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, process_notification),
            _notify_back_handler,
        ],
    }),
    fallbacks=admin_conversation_fallbacks() + [_notify_back_handler],
)

async def clean_db_menu(update, context):
    query = update.callback_query
    if query:
        await query.answer()
    h, t = await AdminCleanup.get_db_health(), await AdminCleanup.get_thresholds()
    text = LanguageManager.get('admin.clean.menu', 
        subs_d=t['subs'] // 86400, subs_c=h['expired_subs'], 
        wg_expired_c=h['expired_wg_subs'],
        rcpt_d=t['receipts'] // 86400, rcpt_c=h['pending_receipts'], 
        tx_m=t['tx'] // 2592000, tx_c=h['old_transactions'], 
        tkt_d=t['tickets'] // 86400, tkt_c=h['closed_tickets'], 
        user_d=t['inactive_users'] // 86400, user_c=h['inactive_users']
    )
    from vpn_bot.admin_menu import build_clean_db_keyboard
    markup = await build_clean_db_keyboard(update.effective_user.id)
    try:
        if query:
            await query.edit_message_text(text, reply_markup=markup, parse_mode='Markdown')
        elif update.message:
            await update.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')
    except Exception:
        pass

@safe_response
async def handle_cleanup_action(update, context):
    action = update.callback_query.data
    uid = update.effective_user.id if update.effective_user else None

    _DESTRUCTIVE_CLEANUP = frozenset({
        "clean_pending_receipts",
        "clean_old_transactions",
        "clean_closed_tickets",
        "clean_inactive_users",
        "clean_mt_orphans",
        "clear_mt_sessions",
        "clean_expired_subs",
        "clean_expired_wg_subs",
    })
    if (
        uid
        and (action in _DESTRUCTIVE_CLEANUP or action.startswith("force_clean_"))
        and not await is_super_admin(uid)
    ):
        await update.callback_query.answer(
            LanguageManager.get("admin.access_denied"), show_alert=True
        )
        return await clean_db_menu(update, context)

    await update.callback_query.answer(LanguageManager.get('common.processing'))
    t = await AdminCleanup.get_thresholds()
    c = 0
    msg = ""

    # Pre-compute converted values from seconds
    subs_seconds = t['subs']
    receipts_days = max(t['receipts'] // 86400, 1)
    tx_months = max(t['tx'] // 2592000, 1)
    tickets_days = max(t['tickets'] // 86400, 1)
    users_days = max(t['inactive_users'] // 86400, 1)

    # Intercept for safety prompt
    if action in ['clean_expired_subs', 'clean_expired_wg_subs']:
        um_renew = await get_admin_setting('sales_um_renew_active', True)
        wg_renew = await get_admin_setting('sales_wg_renew_active', True)
        
        # Determine if we should show safety prompt
        show_prompt = False
        if action == 'clean_expired_subs' and not um_renew: show_prompt = True
        if action == 'clean_expired_wg_subs' and not wg_renew: show_prompt = True
        
        if show_prompt:
            text = LanguageManager.get('admin.clean.warn_instead_prompt')
            keyboard = [
                [InlineKeyboardButton(LanguageManager.get('admin.clean.btn_warn_eligible'), callback_data=f'warn_{action}')],
                [InlineKeyboardButton(LanguageManager.get('admin.common.btn_force_delete'), callback_data=f'force_{action}')],
                [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='clean_db_menu')]
            ]
            await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            return

    # Handle prefixed actions
    bot = context.bot
    if action.startswith('warn_'):
        target = action.replace('warn_', '')
        if target == 'clean_expired_subs':
            c = await AdminCleanup.clean_expired_subscriptions(bot=bot, mode='warn', seconds=subs_seconds)
            msg = LanguageManager.get('admin.clean.res_warned', count=c)
        elif target == 'clean_expired_wg_subs':
            c = await AdminCleanup.clean_expired_wg_subscriptions(bot=bot, mode='warn', seconds=subs_seconds)
            msg = LanguageManager.get('admin.clean.res_warned', count=c)
    
    elif action.startswith('force_'):
        target = action.replace('force_', '')
        if target == 'clean_expired_subs':
            c = await AdminCleanup.clean_expired_subscriptions(bot=bot, mode='delete', seconds=subs_seconds)
            msg = LanguageManager.get('admin.clean.res_subs', count=c)
        elif target == 'clean_expired_wg_subs':
            c = await AdminCleanup.clean_expired_wg_subscriptions(bot=bot, mode='delete', seconds=subs_seconds)
            msg = LanguageManager.get('admin.clean.res_wg_subs', count=c)

    elif action == 'clean_expired_subs': 
        c = await AdminCleanup.clean_expired_subscriptions(seconds=subs_seconds)
        msg = LanguageManager.get('admin.clean.res_subs', count=c)
    elif action == 'clean_expired_wg_subs':
        c = await AdminCleanup.clean_expired_wg_subscriptions(seconds=subs_seconds)
        msg = LanguageManager.get('admin.clean.res_wg_subs', count=c)
    elif action == 'clean_pending_receipts': 
        c = await AdminCleanup.clean_pending_receipts(receipts_days)
        msg = LanguageManager.get('admin.clean.res_rcpt', count=c)
    elif action == 'clean_old_transactions': 
        c = await AdminCleanup.clean_old_transactions(tx_months)
        msg = LanguageManager.get('admin.clean.res_tx', months=tx_months, count=c)
    elif action == 'clean_closed_tickets': 
        c = await AdminCleanup.clean_closed_tickets(tickets_days)
        msg = LanguageManager.get('admin.clean.res_tickets', count=c)
    elif action == 'clean_inactive_users': 
        c = await AdminCleanup.clean_inactive_users(users_days)
        msg = LanguageManager.get('admin.clean.res_users', count=c)
    elif action == 'clean_mt_orphans': 
        c = await AdminCleanup.sync_mikrotik_orphans()
        msg = LanguageManager.get('admin.clean.res_orphans', count=c)
    elif action == 'clear_mt_sessions': 
        c = await AdminCleanup.clear_phantom_sessions()
        msg = LanguageManager.get('admin.clean.res_sessions', count=c)
    
    if msg:
        if c == 0:
            await update.callback_query.message.reply_text(LanguageManager.get('admin.clean.no_items'), parse_mode='Markdown')
        else:
            await update.callback_query.message.reply_text(msg, parse_mode='Markdown')
        admin_id = update.effective_user.id if update.effective_user else 0
        await audit_log(admin_id, "cleanup", detail={"action": action, "count": c})

    return await clean_db_menu(update, context)

async def prompt_clean_value(update, context):
    target = update.callback_query.data.replace('setclean_', '')
    context.user_data['clean_target'] = target
    await update.callback_query.answer()
    t = await AdminCleanup.get_thresholds()
    from vpn_bot.utils import format_seconds_human
    current_human = format_seconds_human(t.get(target, 0))
    await update.callback_query.edit_message_text(
        LanguageManager.get('admin.clean.prompt_value', target=target, current=current_human),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='clean_settings_menu')]]),
        parse_mode='Markdown'
    )
    return CLEAN_SET_VALUE


async def save_cleanup_setting(update, context):
    try:
        val_str = update.message.text.strip()
        target = context.user_data.pop('clean_target')
        from vpn_bot.utils import parse_duration_to_seconds, format_seconds_human
        from vpn_bot.admin_settings import set_admin_setting
        
        seconds = parse_duration_to_seconds(val_str)
        if seconds <= 0:
            await update.message.reply_text(
                LanguageManager.get('admin.clean.invalid_duration'),
                parse_mode='Markdown'
            )
            return await clean_db_menu(update, context)
        
        # Store as human-readable string (e.g. '3d', '6h')
        s = await get_admin_setting('cleanup_settings', {})
        s[target] = val_str
        await set_admin_setting('cleanup_settings', s)
        
        human = format_seconds_human(seconds)
        await update.message.reply_text(
            LanguageManager.get('admin.clean.update_success', target=target, value=human),
            parse_mode='Markdown'
        )
    except Exception:
        await update.message.reply_text(LanguageManager.get('common.error'))
    return await clean_db_menu(update, context)

async def clean_settings_menu_callback(update, context):
    query = update.callback_query
    await query.answer()
    
    t = await AdminCleanup.get_thresholds()
    from vpn_bot.utils import format_seconds_human
    
    text = LanguageManager.get('admin.clean.settings_menu')
    keyboard = [
        [InlineKeyboardButton(f"📅 {LanguageManager.get('admin.clean.btn_subs')} ({format_seconds_human(t['subs'])})", callback_data='setclean_subs')],
        [InlineKeyboardButton(f"🧾 {LanguageManager.get('admin.clean.btn_rcpt')} ({format_seconds_human(t['receipts'])})", callback_data='setclean_receipts')],
        [InlineKeyboardButton(f"💳 {LanguageManager.get('admin.clean.btn_tx')} ({format_seconds_human(t['tx'])})", callback_data='setclean_tx')],
        [InlineKeyboardButton(f"🎫 {LanguageManager.get('admin.clean.btn_tickets')} ({format_seconds_human(t['tickets'])})", callback_data='setclean_tickets')],
        [InlineKeyboardButton(f"👤 {LanguageManager.get('admin.clean.btn_users')} ({format_seconds_human(t['inactive_users'])})", callback_data='setclean_inactive_users')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='clean_db_menu')]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

admin_cleanup_conv_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(clean_db_menu, pattern='^clean_db_menu$'), 
        CallbackQueryHandler(clean_settings_menu_callback, pattern='^clean_settings_menu$'),
        CallbackQueryHandler(prompt_clean_value, pattern='^setclean_')
    ],
    states=_with_conv_cancel({
        CLEAN_SET_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_cleanup_setting)],
    }),
    fallbacks=admin_conversation_fallbacks(),
    allow_reentry=True
)

# OpenVPN / L2TP Management Menu
@safe_response
async def ovpn_l2tp_mgmt_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mother menu for OpenVPN and L2TP management."""
    query = update.callback_query
    if query: await query.answer()

    text = LanguageManager.get('admin.ovpn_l2tp.menu_title')
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.ovpn_l2tp.btn_plans'), callback_data='list_profiles')],
        [InlineKeyboardButton(LanguageManager.get('admin.ovpn_l2tp.btn_settings'), callback_data='settings_connection')],
        [InlineKeyboardButton(LanguageManager.get('admin.ovpn_l2tp.btn_ovpn'), callback_data='manage_ovpn')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_start')]
    ]
    await universal_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))

async def manage_ovpn_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored manage_ovpn_files using admin_ovpn_service."""
    query = update.callback_query
    if query: await query.answer()
    
    configs = await get_all_ovpn_configs()
    
    text = LanguageManager.get('admin.ovpn.title')
    keyboard = []
    
    if not configs:
        text += LanguageManager.get('admin.ovpn.empty')
    else:
        for c, s_name in configs:
            label = c.display_name or LanguageManager.get('common.no_label')
            text += f"📄 **{label}** ({s_name or LanguageManager.get('admin.ovpn.all_servers')})\n"
            keyboard.append([
                InlineKeyboardButton(LanguageManager.get('admin.ovpn.btn_edit', label=label), callback_data=f'edit_ovpn_{c.id}'),
                InlineKeyboardButton(LanguageManager.get('admin.ovpn.btn_del', label=label), callback_data=f'del_ovpn_{c.id}'),
            ])
            
    keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.ovpn.btn_upload'), callback_data='upload_ovpn')])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='ovpn_l2tp_mgmt_menu')])
    
    await universal_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END

async def start_ovpn_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data.pop('edit_ovpn_id', None)
    await admin_conv_prompt(
        update,
        LanguageManager.get('admin.ovpn.prompt_file'),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='manage_ovpn')]]),
    )
    return WAIT_OVPN_FILE

async def edit_ovpn_config_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    ovpn_id = int(query.data.split('_')[2])
    await query.answer()
    context.user_data['edit_ovpn_id'] = ovpn_id
    await admin_conv_prompt(
        update,
        LanguageManager.get('admin.ovpn.prompt_edit'),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='manage_ovpn')]]),
    )
    return WAIT_OVPN_FILE

async def process_ovpn_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc:
        await update.message.reply_text(LanguageManager.get('admin.ovpn.error_file'))
        return WAIT_OVPN_FILE
    f = await doc.get_file()
    content_byte = await f.download_as_bytearray()
    content = content_byte.decode('utf-8')
    edit_id = context.user_data.get('edit_ovpn_id')
    if edit_id:
        success = await update_ovpn_config(edit_id, {'filename': doc.file_name, 'content': content})
        if success:
            await update.message.reply_text(LanguageManager.get('admin.ovpn.success_edit'))
        else:
            await update.message.reply_text(LanguageManager.get('admin.ovpn.error_load'))
        context.user_data.pop('edit_ovpn_id', None)
        return await manage_ovpn_files(update, context)
    context.user_data['temp_ovpn'] = {'filename': doc.file_name, 'content': content}
    from vpn_bot.admin_server_service import get_servers_for_admin_list

    servers = await get_servers_for_admin_list()
    
    keyboard = [[InlineKeyboardButton(LanguageManager.get('admin.ovpn.all_servers'), callback_data='ovpn_srv_all')]]
    for s in servers:
        btn_text = LanguageManager.get('admin.server.btn_host_item', name=s.name)
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f'ovpn_srv_{s.id}')])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='manage_ovpn')])
    await update.message.reply_text(LanguageManager.get('admin.ovpn.prompt_server'), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return WAIT_OVPN_SERVER

async def ovpn_server_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['temp_ovpn']['server_id'] = None if query.data == 'ovpn_srv_all' else int(query.data.split('_')[2])
    await query.edit_message_text(LanguageManager.get('admin.ovpn.prompt_label'))
    return WAIT_OVPN_LABEL

async def ovpn_label_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    label = update.message.text.strip()
    ovpn_data = context.user_data.get('temp_ovpn')
    if not ovpn_data:
        await update.message.reply_text(LanguageManager.get('admin.access_denied')) # Session lost
        return ConversationHandler.END
    ovpn_data['display_name'] = label
    await create_ovpn_config(ovpn_data)
    await update.message.reply_text(LanguageManager.get('admin.ovpn.success_upload', label=label), parse_mode='Markdown')
    context.user_data.pop('temp_ovpn', None)
    return await manage_ovpn_files(update, context)

async def delete_ovpn_config_handler(update: Update, context: ContextTypes.DEFAULT_TYPE): # Renamed to avoid name clash
    query = update.callback_query
    ovpn_id = int(query.data.split('_')[2])
    await query.answer()
    
    success = await delete_ovpn_config(ovpn_id)
    if success:
        await query.answer(LanguageManager.get('common.success'))
    return await manage_ovpn_files(update, context)

admin_ovpn_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(manage_ovpn_files, pattern='^manage_ovpn$'),
        CallbackQueryHandler(start_ovpn_upload, pattern='^upload_ovpn$'),
        CallbackQueryHandler(edit_ovpn_config_start, pattern='^edit_ovpn_'),
        CallbackQueryHandler(delete_ovpn_config_handler, pattern='^del_ovpn_'),
    ],
    states=_with_conv_cancel({
        WAIT_OVPN_FILE: [MessageHandler(filters.Document.ALL, process_ovpn_upload)],
        WAIT_OVPN_SERVER: [CallbackQueryHandler(ovpn_server_received, pattern='^ovpn_srv_')],
        WAIT_OVPN_LABEL: [MessageHandler(filters.TEXT & ~filters.COMMAND, ovpn_label_received)],
    }),
    fallbacks=admin_conversation_fallbacks([
        CallbackQueryHandler(manage_ovpn_files, pattern='^manage_ovpn$'),
    ]),
)


# --- Shared Users Management ---

async def shared_users_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.shared.btn_set_default'), callback_data='set_default_shared')],
        [InlineKeyboardButton(LanguageManager.get('admin.shared.btn_edit_individual'), callback_data='edit_shared_user')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_start')]
    ]
    markup = InlineKeyboardMarkup(keyboard)
    text = LanguageManager.get('admin.shared.menu_title')
    if query:
        await query.edit_message_text(text, reply_markup=markup, parse_mode='Markdown')
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')
    return SHARED_DEFAULT_VALUE # reusing state for menu structure or just return to wait for input

async def set_default_shared_users_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored set_default_shared_users_start."""
    query = update.callback_query
    await query.answer()
    
    current_default = await get_default_shared_users()
    
    await query.edit_message_text(
        LanguageManager.get('admin.shared.default_prompt', current=str(current_default)),
        reply_markup=get_admin_edit_inline_keyboard('shared_users_menu'),
        parse_mode='Markdown',
    )
    return SHARED_DEFAULT_VALUE

async def process_default_shared_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored process_default_shared_value."""
    text = update.message.text.strip()
    shared_back = get_admin_edit_inline_keyboard('shared_users_menu')
    if not text.isdigit() or not (1 <= int(text) <= 10):
        await update.message.reply_text(LanguageManager.get('admin.shared.invalid_value'), reply_markup=shared_back)
        return SHARED_DEFAULT_VALUE
    
    val = int(text)
    await set_default_shared_users(val)
    await update.message.reply_text(LanguageManager.get('admin.shared.default_success', value=str(val)), parse_mode='Markdown')
    return await shared_users_menu(update, context) # Go back to menu logic, but via message reply it might need a fresh message.
    # Actually shared_users_menu expects a callback query usually. Let's send a new message instead.
    
    # Re-show menu
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.shared.btn_set_default'), callback_data='set_default_shared')],
        [InlineKeyboardButton(LanguageManager.get('admin.shared.btn_edit_individual'), callback_data='edit_shared_user')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_start')]
    ]
    await update.message.reply_text(
        LanguageManager.get('admin.shared.menu_title'),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return ConversationHandler.END # End the sub-conversation or stay? 
    # Structure: shared_users_handler conv.
    # Entry: shared_users_menu (callback)
    # States: 
    #   MENU_STATE (waiting for choice) -> set_default or edit_individual
    # Let's simplify: shared_users_handler will handle the "set default" and "edit individual" flows.

async def edit_shared_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        LanguageManager.get('admin.shared.user_prompt'),
        reply_markup=get_admin_edit_inline_keyboard('shared_users_menu'),
        parse_mode='Markdown',
    )
    return SHARED_USER_LOOKUP

async def process_shared_user_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored process_shared_user_search."""
    username = update.message.text.strip()
    
    shared_back = get_admin_edit_inline_keyboard('shared_users_menu')
    sub = await get_subscription_by_username(username)
    if not sub:
         await update.message.reply_text(
             LanguageManager.get('admin.user.not_found_db', username=username),
             reply_markup=shared_back,
         )
         return SHARED_USER_LOOKUP
    
    current_shared = await get_user_shared_count_from_mt(username, sub.server_id)
    
    if current_shared is None:
         await update.message.reply_text(
             LanguageManager.get('admin.shared.user_not_found', username=username),
             reply_markup=shared_back,
         )
         return SHARED_USER_LOOKUP

    context.user_data['shared_user_target'] = username
    context.user_data['shared_user_server_id'] = sub.server_id
    
    await update.message.reply_text(
        LanguageManager.get('admin.shared.user_info', username=username, current=str(current_shared)),
        reply_markup=shared_back,
        parse_mode='Markdown',
    )
    return SHARED_USER_VALUE

async def process_shared_user_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored process_shared_user_value."""
    text = update.message.text.strip()
    shared_back = get_admin_edit_inline_keyboard('shared_users_menu')
    if not text.isdigit() or not (1 <= int(text) <= 10):
        await update.message.reply_text(LanguageManager.get('admin.shared.invalid_value'), reply_markup=shared_back)
        return SHARED_USER_VALUE
    
    val = int(text)
    username = context.user_data.get('shared_user_target')
    server_id = context.user_data.get('shared_user_server_id')
    
    success = await set_user_shared_count_on_mt(username, server_id, val)
    
    if success:
        await update.message.reply_text(LanguageManager.get('admin.shared.user_success', username=username, value=str(val)), parse_mode='Markdown')
    else:
        await update.message.reply_text(LanguageManager.get('common.error'))
            
    # Re-show menu
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.shared.btn_set_default'), callback_data='set_default_shared')],
        [InlineKeyboardButton(LanguageManager.get('admin.shared.btn_edit_individual'), callback_data='edit_shared_user')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_start')]
    ]
    await update.message.reply_text(
        LanguageManager.get('admin.shared.menu_title'),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return ConversationHandler.END


SHARED_MENU_STATE = 45

async def shared_users_menu_entry(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await shared_users_menu(update, context)

# Redefine shared_users_menu to return SHARED_MENU_STATE
async def shared_users_menu_impl(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.shared.btn_set_default'), callback_data='set_default_shared')],
        [InlineKeyboardButton(LanguageManager.get('admin.shared.btn_edit_individual'), callback_data='edit_shared_user')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_start')]
    ]
    await query.edit_message_text(
        LanguageManager.get('admin.shared.menu_title'),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return SHARED_MENU_STATE

_shared_users_menu_back_handler = CallbackQueryHandler(shared_users_menu_impl, pattern='^shared_users_menu$')

shared_users_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(shared_users_menu_impl, pattern='^shared_users_menu$')],
    states=_with_conv_cancel({
        SHARED_MENU_STATE: [
            CallbackQueryHandler(set_default_shared_users_start, pattern='^set_default_shared$'),
            CallbackQueryHandler(edit_shared_user_start, pattern='^edit_shared_user$'),
            CallbackQueryHandler(admin_exit_to_menu, pattern='^admin_start$'),
        ],
        SHARED_DEFAULT_VALUE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, process_default_shared_value),
            _shared_users_menu_back_handler,
        ],
        SHARED_USER_LOOKUP: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, process_shared_user_search),
            _shared_users_menu_back_handler,
        ],
        SHARED_USER_VALUE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, process_shared_user_value),
            _shared_users_menu_back_handler,
        ],
    }),
    fallbacks=admin_conversation_fallbacks(),
)

# admin_wg_config_handler and related helpers were removed — the per-subscription
# WG config flow (admin_wg_cfg_<sub_id>) registered in admin_search_user_handler
# supersedes this standalone conversation.

# Split handler modules (imported last to avoid circular imports)
from vpn_bot.admin_user_handlers import *  # noqa: F401,F403,E402
from vpn_bot.admin_receipt_handlers import *  # noqa: F401,F403,E402
from vpn_bot.admin_wg_handlers import *  # noqa: F401,F403,E402

# Star import omits leading-underscore helpers; re-export for tests and internal callers.
import vpn_bot.admin_wg_handlers as _wg_handlers_mod

for _sym, _val in vars(_wg_handlers_mod).items():
    if _sym.startswith("_") and not _sym.startswith("__"):
        globals()[_sym] = _val
