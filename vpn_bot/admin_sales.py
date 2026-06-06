"""
Admin Sales Management Module
Handles global/protocol sales toggles, capacity limits, and custom block messages.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CallbackQueryHandler, MessageHandler, filters

from vpn_bot.settings_utils import get_admin_setting, set_admin_setting
from vpn_bot.admin_sales_service import (
    apply_sales_capacity_delta,
    count_active_sales_slots,
    get_sales_dashboard_data,
    get_renewal_dashboard_data,
    protocol_key_from_limit_setting,
    sales_capacity_remaining,
    toggle_renewal_status,
    toggle_sales_status,
    broadcast_renewal_notification,
)
from vpn_bot.utils import LanguageManager, safe_response
from vpn_bot.admin_conversation import admin_exit_to_menu, build_admin_fallback_handlers
from vpn_bot.bot_handler import MENU_BUTTONS_FILTER
from vpn_bot.conversation_controls import conv_control_handlers, is_conv_cancel, reply_conv_prompt

# States
SALES_MSG, SALES_LIMIT, SALES_ADD_CAPACITY, RENEW_MSG = range(30, 34)


def _format_cap_limit(limit: int) -> str:
    return "∞" if limit <= 0 else str(limit)

@safe_response
async def sales_mgmt_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored sales_mgmt_menu."""
    query = update.callback_query
    if query: await query.answer()

    data = await get_sales_dashboard_data()
    
    status_active = LanguageManager.get('admin.sales.status_active')
    status_disabled = LanguageManager.get('admin.sales.status_disabled')

    def get_status_label(active):
        return status_active if active else status_disabled

    text = LanguageManager.get('admin.sales.menu_title') + "\n\n"
    text += LanguageManager.get('admin.sales.global_title', status=get_status_label(data['global_active'])) + "\n\n"
    text += LanguageManager.get(
        'admin.sales.ovpn_title',
        status=get_status_label(data['ovpn_active']),
        limit=_format_cap_limit(data['ovpn_limit']),
        current=data['ovpn_count'],
    ) + "\n\n"
    text += LanguageManager.get(
        'admin.sales.wg_title',
        status=get_status_label(data['wg_active']),
        limit=_format_cap_limit(data['wg_limit']),
        current=data['wg_count'],
    )

    keyboard = [
        # Global Sales
        [InlineKeyboardButton(LanguageManager.get('admin.sales.label_global', status=get_status_label(data['global_active'])), callback_data='sales_toggle_global')],
        [InlineKeyboardButton(LanguageManager.get('admin.sales.btn_edit_msg_global'), callback_data='sales_edit_msg_global')],
        
        # OpenVPN Section
        [InlineKeyboardButton(LanguageManager.get('admin.sales.label_ovpn', status=get_status_label(data['ovpn_active'])), callback_data='sales_toggle_ovpn')],
        [InlineKeyboardButton(LanguageManager.get('admin.sales.btn_edit_msg_ovpn'), callback_data='sales_edit_msg_ovpn')],
        
        # WireGuard Section
        [InlineKeyboardButton(LanguageManager.get('admin.sales.label_wg', status=get_status_label(data['wg_active'])), callback_data='sales_toggle_wg')],
        [InlineKeyboardButton(LanguageManager.get('admin.sales.btn_edit_msg_wg'), callback_data='sales_edit_msg_wg')],
        
        # Sub-Menus
        [
            InlineKeyboardButton(LanguageManager.get('admin.sales.btn_set_limit'), callback_data='sales_capacity_menu'),
            InlineKeyboardButton(LanguageManager.get('admin.sales.btn_renew_mgmt'), callback_data='renew_mgmt_menu')
        ],
        [InlineKeyboardButton(LanguageManager.get('admin.discount.menu_btn'), callback_data='discount_codes_menu')],
        
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_start')]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    return ConversationHandler.END

async def capacity_mgmt_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored capacity_mgmt_menu."""
    query = update.callback_query
    if query:
        await query.answer()

    data = await get_sales_dashboard_data()

    text = LanguageManager.get('admin.sales.capacity_menu_title') + "\n\n"
    text += LanguageManager.get(
        'admin.sales.capacity_ovpn',
        count=data['ovpn_count'],
        limit=_format_cap_limit(data['ovpn_limit']),
        remaining=data['ovpn_remaining'],
    ) + "\n"
    text += LanguageManager.get(
        'admin.sales.capacity_wg',
        count=data['wg_count'],
        limit=_format_cap_limit(data['wg_limit']),
        remaining=data['wg_remaining'],
    )

    keyboard = [
        [
            InlineKeyboardButton(
                LanguageManager.get('admin.sales.btn_set_limit_ovpn'),
                callback_data='sales_set_limit_ovpn',
            ),
            InlineKeyboardButton(
                LanguageManager.get('admin.sales.btn_set_limit_wg'),
                callback_data='sales_set_limit_wg',
            ),
        ],
        [
            InlineKeyboardButton(
                LanguageManager.get('admin.sales.btn_add_capacity_ovpn'),
                callback_data='sales_add_capacity_ovpn',
            ),
            InlineKeyboardButton(
                LanguageManager.get('admin.sales.btn_add_capacity_wg'),
                callback_data='sales_add_capacity_wg',
            ),
        ],
        [InlineKeyboardButton(LanguageManager.get('admin.sales.btn_edit_msg_full'), callback_data='sales_edit_msg_full')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='sales_mgmt_menu')]
    ]

    markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.edit_message_text(text, reply_markup=markup, parse_mode='Markdown')
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')
    return ConversationHandler.END

async def renew_mgmt_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored renew_mgmt_menu."""
    query = update.callback_query
    if query: await query.answer()

    data = await get_renewal_dashboard_data()

    status_active = LanguageManager.get('admin.sales.status_active')
    status_disabled = LanguageManager.get('admin.sales.status_disabled')

    def get_status_label(active):
        return status_active if active else status_disabled

    text = LanguageManager.get('admin.sales.renew_menu_title')
    text += LanguageManager.get('admin.sales.renew_ovpn_label', status=get_status_label(data['um_renew'])) + "\n"
    text += LanguageManager.get('admin.sales.renew_wg_label', status=get_status_label(data['wg_renew'])) + "\n"
    text += LanguageManager.get('admin.sales.renew_window_label', window=data['window']) + "\n"
    text += LanguageManager.get(
        'admin.sales.renew_strict_window_label',
        status=get_status_label(data['strict_window']),
    ) + "\n"
    text += LanguageManager.get(
        'admin.sales.renew_waiting_label',
        um=data['waiting_um'],
        wg=data['waiting_wg'],
    ) + "\n"
    text += LanguageManager.get('admin.sales.renew_policy_hint') + "\n"

    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.sales.btn_renew_um'), callback_data='toggle_renew_um')],
        [InlineKeyboardButton(LanguageManager.get('admin.sales.btn_renew_wg'), callback_data='toggle_renew_wg')],
        [InlineKeyboardButton(LanguageManager.get('admin.sales.btn_renew_window'), callback_data='set_renew_window')],
        [InlineKeyboardButton(
            LanguageManager.get('admin.sales.btn_toggle_strict_window', status=get_status_label(data['strict_window'])),
            callback_data='toggle_renew_strict_window',
        )],
        [InlineKeyboardButton(LanguageManager.get('admin.sales.btn_edit_early_msg'), callback_data='edit_renew_early_msg')],
        [InlineKeyboardButton(LanguageManager.get('admin.sales.btn_edit_renew_msg'), callback_data='edit_renew_block_msg')],
        [InlineKeyboardButton(LanguageManager.get('admin.sales.btn_edit_notify_msg'), callback_data='edit_renew_notify_msg')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='sales_mgmt_menu')]
    ]

    markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.edit_message_text(text, reply_markup=markup, parse_mode='Markdown')
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode='Markdown')
    return ConversationHandler.END

async def toggle_renew_strict_window(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle early-renewal window restriction (quota/expired always allowed when renew on)."""
    query = update.callback_query
    await query.answer()
    current = await get_admin_setting('sales_renew_strict_window', True)
    await set_admin_setting('sales_renew_strict_window', not current)
    return await renew_mgmt_menu(update, context)


async def toggle_renew(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored toggle_renew using service."""
    query = update.callback_query
    await query.answer()
    
    proto = 'um' if 'um' in query.data else 'wg'
    new_state = await toggle_renewal_status(proto)
    
    if new_state:
        # Prompt to notify waiting users
        context.user_data['renew_notify_proto'] = proto
        text = LanguageManager.get('admin.sales.notify_waiters_ask')
        keyboard = [
            [InlineKeyboardButton(LanguageManager.get('common.yes'), callback_data='notify_waiters_yes')],
            [InlineKeyboardButton(LanguageManager.get('common.no'), callback_data='renew_mgmt_menu')]
        ]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await query.answer(LanguageManager.get('admin.server.toggle_success'))
        return await renew_mgmt_menu(update, context)

async def notify_waiting_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored notify_waiting_users using service."""
    query = update.callback_query
    proto = context.user_data.get('renew_notify_proto')
    if not proto: return await renew_mgmt_menu(update, context)

    await query.answer(LanguageManager.get('common.processing'))
    await query.edit_message_text(LanguageManager.get('common.processing'))

    count = await broadcast_renewal_notification(context.bot, proto)
            
    await query.message.reply_text(LanguageManager.get('admin.sales.notify_confirm', count=count))
    return await renew_mgmt_menu(update, context)

async def toggle_sales(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored toggle_sales using service."""
    query = update.callback_query
    await toggle_sales_status(query.data)
    
    await query.answer(LanguageManager.get('admin.server.toggle_success'))
    return await sales_mgmt_menu(update, context)

async def edit_sales_msg_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start editing a sales message."""
    query = update.callback_query
    data = query.data
    
    if data == 'edit_renew_block_msg':
        key = 'sales_renew_disabled_msg'
    elif data == 'edit_renew_notify_msg':
        key = 'sales_renew_notification_msg'
    elif data == 'edit_renew_early_msg':
        key = 'sales_renew_too_early_msg'
    else:
        key = data.replace('sales_edit_msg_', 'sales_') + '_msg'
        
    context.user_data['sales_msg_key'] = key
    
    await query.answer()
    back_cb = 'renew_mgmt_menu' if 'renew' in key else 'sales_capacity_menu' if key.endswith('_limit') else 'sales_mgmt_menu'
    keyboard = [[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data=back_cb)]]
    await reply_conv_prompt(
        update,
        LanguageManager.get('admin.sales.prompt_msg'),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SALES_MSG

async def receive_sales_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and save the custom message."""
    if is_conv_cancel(update):
        return await admin_exit_to_menu(update, context)
    key = context.user_data.get('sales_msg_key')
    text = update.message.text
    
    await set_admin_setting(key, text)
    await update.message.reply_text(LanguageManager.get('admin.sales.msg_updated'))
    
    context.user_data.clear()
    
    if 'renew' in key:
        return await renew_mgmt_menu(update, context)
    return await sales_mgmt_menu(update, context)

async def set_sales_limit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start setting a sales capacity limit or renewal window."""
    query = update.callback_query
    data = query.data

    if data == 'set_renew_window':
        key = 'sales_renew_window_days'
        prompt = LanguageManager.get('admin.sales.renew_window_prompt')
    else:
        key = data.replace('sales_set_limit_', 'sales_') + '_limit'
        prompt = LanguageManager.get('admin.sales.prompt_limit')

    context.user_data['sales_limit_key'] = key
    context.user_data.pop('sales_add_capacity_protocol', None)
    back_cb = 'renew_mgmt_menu' if key == 'sales_renew_window_days' else 'sales_capacity_menu'

    await query.answer()
    keyboard = [[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data=back_cb)]]
    await reply_conv_prompt(update, prompt, reply_markup=InlineKeyboardMarkup(keyboard))
    return SALES_LIMIT


async def set_add_capacity_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start adding N new purchase slots on top of current active count."""
    query = update.callback_query
    proto = query.data.replace('sales_add_capacity_', '')
    context.user_data['sales_add_capacity_protocol'] = proto
    context.user_data.pop('sales_limit_key', None)

    await query.answer()
    keyboard = [[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='sales_capacity_menu')]]
    await reply_conv_prompt(
        update,
        LanguageManager.get('admin.sales.prompt_add_capacity'),
        reply_markup=InlineKeyboardMarkup(keyboard),
    )
    return SALES_ADD_CAPACITY


async def _reply_capacity_save_feedback(
    update: Update,
    *,
    protocol: str,
    limit: int,
    count: int,
    is_add: bool = False,
    delta: int = 0,
) -> None:
    remaining = sales_capacity_remaining(count, limit)
    remaining_display = remaining if remaining != "∞" else "∞"
    limit_display = _format_cap_limit(limit)

    if is_add:
        msg = LanguageManager.get(
            'admin.sales.add_capacity_updated',
            delta=delta,
            limit=limit_display,
            count=count,
            remaining=remaining_display,
        )
    elif limit > 0 and limit <= count:
        msg = LanguageManager.get(
            'admin.sales.limit_updated_blocked',
            limit=limit_display,
            count=count,
        )
    elif limit > 0:
        msg = LanguageManager.get(
            'admin.sales.limit_updated_with_slots',
            limit=limit_display,
            count=count,
            remaining=remaining_display,
        )
    else:
        msg = LanguageManager.get('admin.sales.limit_updated')

    await update.message.reply_text(msg, parse_mode='Markdown')


async def receive_sales_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and save the sales limit or renewal window."""
    if is_conv_cancel(update):
        return await admin_exit_to_menu(update, context)
    key = context.user_data.get('sales_limit_key')
    context.user_data.pop('sales_limit_key', None)

    if key == 'sales_renew_window_days':
        from vpn_bot.utils import parse_duration_to_seconds
        if parse_duration_to_seconds(update.message.text.strip()) <= 0:
            await update.message.reply_text(LanguageManager.get('common.error'))
            return SALES_LIMIT
        val = update.message.text.strip()
        await set_admin_setting(key, val)
        await update.message.reply_text(LanguageManager.get('admin.sales.limit_updated'))
        return await renew_mgmt_menu(update, context)

    try:
        val = int(update.message.text.strip())
        if val < 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text(LanguageManager.get('common.error'))
        return SALES_LIMIT

    await set_admin_setting(key, val)
    proto = await protocol_key_from_limit_setting(key)
    if proto:
        count = await count_active_sales_slots(proto)
        await _reply_capacity_save_feedback(update, protocol=proto, limit=val, count=count)
    else:
        await update.message.reply_text(LanguageManager.get('admin.sales.limit_updated'))

    return await capacity_mgmt_menu(update, context)


async def receive_add_capacity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Add N purchase slots: new limit = current active + delta."""
    if is_conv_cancel(update):
        return await admin_exit_to_menu(update, context)
    proto = context.user_data.pop('sales_add_capacity_protocol', None)
    if proto not in ('ovpn', 'wg'):
        await update.message.reply_text(LanguageManager.get('common.error'))
        return await capacity_mgmt_menu(update, context)

    try:
        delta = int(update.message.text.strip())
        if delta < 0:
            raise ValueError()
    except ValueError:
        await update.message.reply_text(LanguageManager.get('common.error'))
        return SALES_ADD_CAPACITY

    new_limit = await apply_sales_capacity_delta(proto, delta)
    count = await count_active_sales_slots(proto)
    await _reply_capacity_save_feedback(
        update,
        protocol=proto,
        limit=new_limit,
        count=count,
        is_add=True,
        delta=delta,
    )
    return await capacity_mgmt_menu(update, context)

async def cancel_sales_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel sales setting action."""
    await update.message.reply_text(LanguageManager.get('common.cancel'))
    return await sales_mgmt_menu(update, context)

from vpn_bot.bot_handler import main_menu_text_dispatch
# Handlers
sales_mgmt_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(sales_mgmt_menu, pattern='^sales_mgmt_menu$'),
        CallbackQueryHandler(capacity_mgmt_menu, pattern='^sales_capacity_menu$'),
        CallbackQueryHandler(renew_mgmt_menu, pattern='^renew_mgmt_menu$'),
        CallbackQueryHandler(toggle_renew, pattern='^toggle_renew_(um|wg)$'),
        CallbackQueryHandler(toggle_renew_strict_window, pattern='^toggle_renew_strict_window$'),
        CallbackQueryHandler(notify_waiting_users, pattern='^notify_waiters_yes$'),
        CallbackQueryHandler(toggle_sales, pattern='^sales_toggle_'),
        CallbackQueryHandler(edit_sales_msg_start, pattern='^(sales_edit_msg_|edit_renew_block_msg|edit_renew_notify_msg|edit_renew_early_msg)'),
        CallbackQueryHandler(set_sales_limit_start, pattern='^(sales_set_limit_|set_renew_window)'),
        CallbackQueryHandler(set_add_capacity_start, pattern='^sales_add_capacity_(ovpn|wg)$'),
    ],
    states={
        SALES_MSG: [
            *conv_control_handlers(admin_exit_to_menu),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, receive_sales_msg),
        ],
        SALES_LIMIT: [
            *conv_control_handlers(admin_exit_to_menu),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, receive_sales_limit),
        ],
        SALES_ADD_CAPACITY: [
            *conv_control_handlers(admin_exit_to_menu),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, receive_add_capacity),
        ],
    },
    fallbacks=[
        *build_admin_fallback_handlers(main_menu_text_dispatch),
        CallbackQueryHandler(sales_mgmt_menu, pattern='^sales_mgmt_menu$'),
        CallbackQueryHandler(capacity_mgmt_menu, pattern='^sales_capacity_menu$'),
        CallbackQueryHandler(renew_mgmt_menu, pattern='^renew_mgmt_menu$'),
        CallbackQueryHandler(admin_exit_to_menu, pattern='^.*$'),
    ],
)
