from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from vpn_bot.admin_settings_service import (
    get_payment_cards, add_payment_card, delete_payment_card,
    get_wallet_presets, add_wallet_preset, delete_wallet_preset,
    is_wallet_custom_amount_enabled,
    set_wallet_custom_amount_enabled,
    get_wallet_custom_limits,
    set_wallet_custom_min,
    set_wallet_custom_max,
    get_ticket_subjects, add_ticket_subject, delete_ticket_subject, reset_ticket_subjects,
    get_server_connection_info, update_server_connection_info,
    get_custom_message, set_custom_message,
    is_purchase_terms_enabled,
    set_purchase_terms_enabled,
    get_purchase_terms_mode,
    set_purchase_terms_mode,
    get_purchase_terms_text,
    get_purchase_terms_version,
    set_purchase_terms_text,
    PURCHASE_TERMS_MODE_ONCE,
    PURCHASE_TERMS_MODE_EVERY,
)
from vpn_bot.utils import LanguageManager, format_currency, get_currency_unit
from vpn_bot.bot_handler import MENU_BUTTONS_FILTER, main_menu_text_dispatch
from vpn_bot.admin_conversation import build_admin_fallback_handlers
from vpn_bot.conversation_controls import (
    append_conv_footer,
    conv_control_handlers,
    conv_markup,
    is_conv_cancel,
    is_conv_skip,
    legacy_cancel_handlers,
    merge_markup,
    reply_conv_prompt,
)
from vpn_bot.settings_utils import get_admin_setting, set_admin_setting

# States for settings conversations
(SETTINGS_MENU, CARD_NUMBER, CARD_HOLDER, CARD_BANK,
 MESSAGE_KEY, MESSAGE_VALUE,
 PRESET_AMOUNT, WALLET_CUSTOM_LIMIT,
 CONN_SERVER, CONN_L2TP_IP, CONN_L2TP_VERSION, CONN_L2TP_PORT, CONN_L2TP_SECRET, 
 CONN_SSTP_IP, CONN_SSTP_PORT, SUBJECT_VALUE,
 SYNC_INTERVAL, BACKUP_INTERVAL, TERMS_TEXT_VALUE) = range(19)

# --- Helper Functions ---
async def universal_reply(
    update: Update,
    text: str,
    reply_markup=None,
    parse_mode='Markdown',
    answer_query=True,
    *,
    conv_controls: bool = False,
    conv_skip: bool = False,
):
    """Helper to handle both callback queries and text messages."""
    if conv_controls:
        text = append_conv_footer(text, with_skip=conv_skip)
        reply_markup = merge_markup(reply_markup, with_cancel=True, with_skip=conv_skip)
    if update.callback_query:
        try:
            # Answer query if not already persistent loading or toast
            if answer_query:
                try:
                    await update.callback_query.answer()
                except:
                    pass
            await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception as e:
            from vpn_bot.utils import logger
            logger.debug(f"universal_reply edit_text failed: {e}")
            try:
                await update.callback_query.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
            except:
                pass
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode=parse_mode)

# --- Settings Main Menu ---

async def bot_config_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin Bot Configurations menu."""
    text = LanguageManager.get('admin.settings.menu')
    from vpn_bot.admin_menu import build_bot_config_keyboard
    uid = update.effective_user.id
    await universal_reply(update, text, reply_markup=await build_bot_config_keyboard(uid))
    return SETTINGS_MENU

# --- Connection Re-prompts ---
async def prompt_conn_sstp_port(update, context):
    """Refactored prompt_conn_sstp_port."""
    await universal_reply(update, LanguageManager.get('admin.settings.conn_sstp_port'))
    return CONN_SSTP_PORT

# --- Payment Cards Management ---

async def manage_payment_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored manage_payment_cards."""
    query = update.callback_query
    if query: await query.answer()
    
    cards = await get_payment_cards()
    
    text = LanguageManager.get('admin.settings.cards_title')
    keyboard = []
    
    if cards:
        for idx, card in enumerate(cards):
            masked = card.get('number', '')[:4] + '-****-****-' + card.get('number', '')[-4:]
            bank = card.get('bank', LanguageManager.get('common.unknown'))
            text += f"{idx + 1}. `{masked}` ({bank})\n"
            keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.settings.btn_del_card', idx=idx + 1), callback_data=f'card_delete_{idx}')])
        text += "\n"
    else:
        text += LanguageManager.get('admin.settings.cards_empty')
    
    keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.settings.btn_add_card'), callback_data='card_add')])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='bot_config_menu')])
    
    await universal_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SETTINGS_MENU

async def add_card_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start adding new card."""
    query = update.callback_query
    await query.answer()
    
    await reply_conv_prompt(update, LanguageManager.get('admin.settings.add_card_prompt'))
    return CARD_NUMBER

async def receive_card_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive card number."""
    if is_conv_cancel(update):
        return await cancel_settings_action(update, context)
    number = update.message.text.replace('-', '').replace(' ', '')
    
    if not number.isdigit() or len(number) != 16:
        await update.message.reply_text(
            LanguageManager.get('admin.settings.card_invalid'),
            reply_markup=conv_markup(),
            parse_mode='Markdown',
        )
        return CARD_NUMBER
    
    context.user_data['card_number'] = number
    await reply_conv_prompt(update, LanguageManager.get('admin.settings.card_saved'), with_skip=True)
    return CARD_HOLDER


async def skip_card_holder_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['card_holder'] = ''
    await reply_conv_prompt(update, LanguageManager.get('admin.settings.bank_prompt'), with_skip=True)
    return CARD_BANK


async def receive_card_holder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive card holder name."""
    if is_conv_cancel(update):
        return await cancel_settings_action(update, context)
    if is_conv_skip(update):
        context.user_data['card_holder'] = ''
    elif update.message and update.message.text:
        context.user_data['card_holder'] = update.message.text.strip()

    await reply_conv_prompt(update, LanguageManager.get('admin.settings.bank_prompt'), with_skip=True)
    return CARD_BANK


async def skip_card_bank_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['card_bank'] = ''
    return await receive_card_bank_finish(update, context)


async def receive_card_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive bank name and save card."""
    if is_conv_cancel(update):
        return await cancel_settings_action(update, context)
    if is_conv_skip(update):
        context.user_data['card_bank'] = ''
    elif update.message and update.message.text:
        context.user_data['card_bank'] = update.message.text.strip()
    return await receive_card_bank_finish(update, context)


async def receive_card_bank_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    
    # Save card
    card_data = {
        'number': context.user_data['card_number'],
        'holder': context.user_data.get('card_holder', ''),
        'bank': context.user_data.get('card_bank', '')
    }
    await add_payment_card(card_data)
    
    # Clear context after using data for message
    masked = context.user_data.get('card_number', '')[:4] + '-****-****-' + context.user_data.get('card_number', '')[-4:]
    card_holder = context.user_data.get('card_holder', 'N/A')
    card_bank = context.user_data.get('card_bank', 'N/A')
    context.user_data.clear()

    msg = LanguageManager.get(
        'admin.settings.card_added', number=masked, holder=card_holder, bank=card_bank
    )
    if update.callback_query:
        await update.callback_query.message.reply_text(msg, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg, parse_mode='Markdown')

    return await manage_payment_cards(update, context)

async def delete_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored delete_card."""
    query = update.callback_query
    idx = int(query.data.split('_')[2])
    
    success, deleted = await delete_payment_card(idx)
    if success:
        await query.answer(LanguageManager.get('admin.settings.card_deleted', bank=deleted.get('bank', 'Unknown')), show_alert=True)
    else:
        await query.answer(LanguageManager.get('admin.tickets.not_found'), show_alert=True)
    
    # Refresh display
    await manage_payment_cards(update, context)
    return SETTINGS_MENU

# --- Purchase Terms Management ---

async def manage_purchase_terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Purchase/service terms settings sub-menu."""
    query = update.callback_query
    if query:
        await query.answer()

    enabled = await is_purchase_terms_enabled()
    mode = await get_purchase_terms_mode()
    version = await get_purchase_terms_version()
    preview = await get_purchase_terms_text()
    if len(preview) > 200:
        preview = preview[:200] + "…"

    status_key = "admin.settings.terms_on" if enabled else "admin.settings.terms_off"
    mode_key = (
        "admin.settings.terms_mode_once"
        if mode == PURCHASE_TERMS_MODE_ONCE
        else "admin.settings.terms_mode_every"
    )
    text = LanguageManager.get(
        "admin.settings.terms_title",
        status=LanguageManager.get(status_key),
        mode=LanguageManager.get(mode_key),
        version=version,
        preview=preview,
    )

    toggle_label = LanguageManager.get(
        "admin.settings.terms_btn_disable" if enabled else "admin.settings.terms_btn_enable"
    )
    mode_toggle_label = LanguageManager.get(
        "admin.settings.terms_btn_mode_every"
        if mode == PURCHASE_TERMS_MODE_ONCE
        else "admin.settings.terms_btn_mode_once"
    )
    keyboard = [
        [InlineKeyboardButton(toggle_label, callback_data="purchase_terms_toggle")],
        [InlineKeyboardButton(mode_toggle_label, callback_data="purchase_terms_mode_toggle")],
        [InlineKeyboardButton(LanguageManager.get("admin.settings.terms_btn_edit"), callback_data="purchase_terms_edit")],
        [InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="bot_config_menu")],
    ]
    await universal_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SETTINGS_MENU


async def toggle_purchase_terms(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await set_purchase_terms_enabled(not await is_purchase_terms_enabled())
    return await manage_purchase_terms(update, context)


async def toggle_purchase_terms_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mode = await get_purchase_terms_mode()
    new_mode = (
        PURCHASE_TERMS_MODE_EVERY
        if mode == PURCHASE_TERMS_MODE_ONCE
        else PURCHASE_TERMS_MODE_ONCE
    )
    await set_purchase_terms_mode(new_mode)
    return await manage_purchase_terms(update, context)


async def edit_purchase_terms_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    current = await get_purchase_terms_text()
    await reply_conv_prompt(
        update,
        LanguageManager.get("admin.settings.terms_edit_prompt", current=current),
        with_skip=True,
    )
    return TERMS_TEXT_VALUE


async def skip_purchase_terms_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(LanguageManager.get("admin.settings.terms_unchanged"))
    return await manage_purchase_terms(update, context)


async def receive_purchase_terms_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_conv_cancel(update):
        return await cancel_settings_action(update, context)
    if is_conv_skip(update):
        await update.callback_query.message.reply_text(LanguageManager.get("admin.settings.terms_unchanged"))
    elif update.message and update.message.text:
        new_version = await set_purchase_terms_text(update.message.text)
        await update.message.reply_text(
            LanguageManager.get("admin.settings.terms_updated", version=new_version)
        )
    return await manage_purchase_terms(update, context)


# --- Custom Messages Management ---

MESSAGE_TEMPLATES = {
    'welcome_message': 'Welcome Message',
    'buy_service_text': 'Buy Service Text',
    'wallet_info_text': 'Wallet Info Text',
    'empty_wallet_text': 'Empty Wallet Text',
    'insufficient_balance_text': 'Insufficient Balance Text',
    'payment_success_text': 'Payment Success Text',
    'support_hours_text': 'Support Hours Text',
    'tutorial_text': '📚 Tutorials Menu Text',
    'tutorial_android': '🤖 Android Tutorial',
    'tutorial_ios': '🍎 iOS Tutorial',
    'tutorial_windows': '💻 Windows Tutorial',
    'tutorial_mac': '🖥 macOS Tutorial',
    'download_apps_text': 'Download Apps Text',
    'admin_secret_keyword': 'Admin Secret Keyword',
    'receipt_approve_msg': '✅ Receipt Approval Message',
    'receipt_deny_msg': '❌ Receipt Rejection Message',
    'maintenance_mode_msg': '🛠 Maintenance Mode Message',
    'reg_welcome_msg': '👋 Registration: Welcome Message',
    'reg_name_prompt': '📝 Registration: Name Prompt',
    'reg_name_error': '❌ Registration: Name Error',
    'reg_phone_prompt': '📱 Registration: Phone Prompt',
    'reg_phone_btn': '🔘 Registration: Phone Button Text',
    'reg_complete_msg': '✅ Registration: Success Message',
}

async def manage_custom_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display custom messages menu."""
    query = update.callback_query
    if query:
        await query.answer()
    
    text = LanguageManager.get('admin.settings.msg_menu')
    keyboard = []
    
    for key, label in MESSAGE_TEMPLATES.items():
        keyboard.append([InlineKeyboardButton(label, callback_data=f'msg_edit_{key}')])
    
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='bot_config_menu')])
    
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return SETTINGS_MENU

async def edit_message_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start editing a message."""
    query = update.callback_query
    key = "_".join(query.data.split('_')[2:])
    
    context.user_data['message_key'] = key
    current = await get_admin_setting(key, "Not set")
    
    hint = ""
    if key == 'welcome_message':
        hint = "\n\n💡 **Placeholders:**\n`{name}` - User's name\n`{balance}` - Wallet balance\n`{active_subs}` - Count of active services"
    
    await query.answer()
    await reply_conv_prompt(
        update,
        LanguageManager.get('admin.settings.msg_edit_prompt', label=MESSAGE_TEMPLATES.get(key, key), current=current) + hint,
        with_skip=True,
    )
    return MESSAGE_VALUE


async def skip_message_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.message.reply_text(LanguageManager.get('admin.settings.msg_unchanged'))
    context.user_data.clear()
    return await manage_custom_messages(update, context)


async def receive_message_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored receive_message_value."""
    if is_conv_cancel(update):
        return await cancel_settings_action(update, context)
    key = context.user_data.get('message_key')
    if is_conv_skip(update):
        await update.message.reply_text(LanguageManager.get('admin.settings.msg_unchanged'))
    elif update.message and update.message.text:
        await set_custom_message(key, update.message.text)
        await update.message.reply_text(LanguageManager.get('admin.settings.msg_updated', label=MESSAGE_TEMPLATES.get(key, key)))
    
    context.user_data.clear()
    return await manage_custom_messages(update, context)

# --- Wallet Presets Management ---

async def manage_wallet_presets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored manage_wallet_presets."""
    query = update.callback_query
    if query: await query.answer()
    
    presets = await get_wallet_presets()
    custom_on = await is_wallet_custom_amount_enabled()
    cmin, cmax = await get_wallet_custom_limits()
    min_disp = await format_currency(cmin)
    max_disp = await format_currency(cmax)
    status_key = "admin.settings.custom_on" if custom_on else "admin.settings.custom_off"
    
    text = LanguageManager.get('admin.settings.presets_title')
    text += LanguageManager.get(
        'admin.settings.custom_amount_block',
        status=LanguageManager.get(status_key),
        min=min_disp,
        max=max_disp,
    )
    keyboard = []
    
    for idx, amount in enumerate(presets):
        display_amount = await format_currency(amount)
        text += f"{idx + 1}. {display_amount}\n"
        keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.settings.btn_del_preset', amount=display_amount), callback_data=f'preset_delete_{idx}')])
    
    text += "\n"
    toggle_label = LanguageManager.get(
        'admin.settings.btn_toggle_custom',
        status=LanguageManager.get(status_key),
    )
    keyboard.append([InlineKeyboardButton(toggle_label, callback_data='wallet_custom_toggle')])
    keyboard.append([
        InlineKeyboardButton(LanguageManager.get('admin.settings.btn_set_custom_min', min=min_disp), callback_data='wallet_custom_min'),
        InlineKeyboardButton(LanguageManager.get('admin.settings.btn_set_custom_max', max=max_disp), callback_data='wallet_custom_max'),
    ])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.settings.btn_add_preset'), callback_data='preset_add')])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='bot_config_menu')])
    
    await universal_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SETTINGS_MENU


async def toggle_wallet_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    enabled = await is_wallet_custom_amount_enabled()
    await set_wallet_custom_amount_enabled(not enabled)
    return await manage_wallet_presets(update, context)


async def wallet_custom_limit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = "min" if query.data.endswith("_min") else "max"
    context.user_data["wallet_limit_field"] = field
    unit = await get_currency_unit()
    prompt_key = (
        "admin.settings.set_custom_min_prompt"
        if field == "min"
        else "admin.settings.set_custom_max_prompt"
    )
    await reply_conv_prompt(update, LanguageManager.get(prompt_key, unit=unit))
    return WALLET_CUSTOM_LIMIT


async def receive_wallet_custom_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if is_conv_cancel(update):
        return await cancel_settings_action(update, context)
    field = context.user_data.pop("wallet_limit_field", "min")
    try:
        amount = float(update.message.text.strip().replace(",", ""))
        if field == "min":
            ok, err = await set_wallet_custom_min(amount)
        else:
            ok, err = await set_wallet_custom_max(amount)
        if ok:
            disp = await format_currency(amount)
            msg_key = (
                "admin.settings.custom_min_updated"
                if field == "min"
                else "admin.settings.custom_max_updated"
            )
            await update.message.reply_text(LanguageManager.get(msg_key, amount=disp), parse_mode="Markdown")
        elif err == "min_above_max":
            await update.message.reply_text(LanguageManager.get("admin.settings.custom_min_above_max"))
            context.user_data["wallet_limit_field"] = field
            return WALLET_CUSTOM_LIMIT
        elif err == "max_below_min":
            await update.message.reply_text(LanguageManager.get("admin.settings.custom_max_below_min"))
            context.user_data["wallet_limit_field"] = field
            return WALLET_CUSTOM_LIMIT
        else:
            await update.message.reply_text(LanguageManager.get("admin.settings.preset_invalid"))
            context.user_data["wallet_limit_field"] = field
            return WALLET_CUSTOM_LIMIT
    except ValueError:
        await update.message.reply_text(LanguageManager.get("admin.settings.preset_invalid"))
        context.user_data["wallet_limit_field"] = field
        return WALLET_CUSTOM_LIMIT

    return await manage_wallet_presets(update, context)

async def add_preset_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start adding preset."""
    query = update.callback_query
    await query.answer()
    
    unit = await get_currency_unit()
    await reply_conv_prompt(update, LanguageManager.get('admin.settings.add_preset_prompt', unit=unit))
    return PRESET_AMOUNT

async def receive_preset_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive preset amount."""
    if is_conv_cancel(update):
        return await cancel_settings_action(update, context)
    try:
        amount = float(update.message.text)
        if amount < 1:
            raise ValueError()
        
        success, reason = await add_wallet_preset(amount)
        display_amount = await format_currency(amount)
        if success:
            await update.message.reply_text(LanguageManager.get('admin.settings.preset_added', amount=display_amount), parse_mode='Markdown')
        elif reason == "exists":
            await update.message.reply_text(LanguageManager.get('admin.settings.preset_exists', amount=display_amount), parse_mode='Markdown')
        else:
            await update.message.reply_text(LanguageManager.get('common.error'))
    except ValueError:
        await update.message.reply_text(LanguageManager.get('admin.settings.preset_invalid'))
        return PRESET_AMOUNT
    
    return await manage_wallet_presets(update, context)

async def delete_preset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored delete_preset."""
    query = update.callback_query
    idx = int(query.data.split('_')[2])
    
    success, deleted = await delete_wallet_preset(idx)
    if success:
        display_amount = await format_currency(deleted)
        await query.answer(LanguageManager.get('admin.settings.preset_deleted', amount=display_amount), show_alert=True)
    else:
        await query.answer(LanguageManager.get('admin.tickets.not_found'), show_alert=True)
    
    await manage_wallet_presets(update, context)
    return SETTINGS_MENU

# --- Connection Info Management ---

async def manage_connection_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored manage_connection_info."""
    query = update.callback_query
    if query: await query.answer()
    
    from vpn_bot.admin_server_service import get_all_servers
    servers = await get_all_servers()
    
    text = LanguageManager.get('admin.settings.conn_menu')
    keyboard = []
    
    for server in servers:
        btn_text = LanguageManager.get('admin.server.btn_item', name=server.name)
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f'conn_server_{server.id}')])
    
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='ovpn_l2tp_mgmt_menu')])
    await universal_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SETTINGS_MENU

async def prompt_conn_l2tp_ip(update, context):
    """Refactored prompt_conn_l2tp_ip."""
    server_id = context.user_data.get('conn_server_id')
    from vpn_bot.admin_server_service import get_server_by_id
    server = await get_server_by_id(server_id)
    
    server_info = await get_server_connection_info(server_id)
    
    text = LanguageManager.get('admin.settings.conn_l2tp_ip',
        server=server.name if server else LanguageManager.get('common.unknown'),
        current=server_info.get('l2tp', {}).get('ip', LanguageManager.get('common.not_set'))
    )
    await universal_reply(update, text, conv_controls=True, conv_skip=True)
    return CONN_L2TP_IP

# Define other prompts as helpers
async def prompt_conn_l2tp_version(update, context):
    text = LanguageManager.get('admin.settings.conn_l2tp_version')
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.settings.btn_l2tp_v2'), callback_data='l2tp_v2'),
         InlineKeyboardButton(LanguageManager.get('admin.settings.btn_l2tp_v3'), callback_data='l2tp_v3')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='prompt_conn_l2tp_ip')]
    ]
    await universal_reply(
        update, text, reply_markup=InlineKeyboardMarkup(keyboard), conv_controls=True
    )
    return CONN_L2TP_VERSION

async def prompt_conn_l2tp_port(update, context):
    await universal_reply(
        update, LanguageManager.get('admin.settings.conn_l2tp_port'), conv_controls=True, conv_skip=True
    )
    return CONN_L2TP_PORT

async def prompt_conn_l2tp_secret(update, context):
    await universal_reply(
        update, LanguageManager.get('admin.settings.conn_l2tp_secret'), conv_controls=True, conv_skip=True
    )
    return CONN_L2TP_SECRET

async def prompt_conn_sstp_ip(update, context):
    await universal_reply(
        update, LanguageManager.get('admin.settings.conn_sstp_ip'), conv_controls=True, conv_skip=True
    )
    return CONN_SSTP_IP

async def edit_connection_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Entry point for server-specific connection info."""
    query = update.callback_query
    server_id = int(query.data.split('_')[2])
    context.user_data['conn_server_id'] = server_id
    return await prompt_conn_l2tp_ip(update, context)

async def receive_l2tp_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive L2TP IP."""
    if is_conv_cancel(update):
        return await cancel_settings_action(update, context)
    if not is_conv_skip(update) and update.message:
        context.user_data['l2tp_ip'] = update.message.text.strip()
    return await prompt_conn_l2tp_version(update, context)

async def receive_l2tp_version(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive L2TP version selection."""
    query = update.callback_query
    await query.answer()
    
    version = query.data # 'l2tp_v2' or 'l2tp_v3'
    context.user_data['l2tp_version'] = version
    
    return await prompt_conn_l2tp_port(update, context)

async def receive_l2tp_port(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive L2TP port."""
    if is_conv_cancel(update):
        return await cancel_settings_action(update, context)
    if not is_conv_skip(update) and update.message:
        context.user_data['l2tp_port'] = update.message.text.strip()
    return await prompt_conn_l2tp_secret(update, context)

async def receive_l2tp_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive L2TP secret."""
    if is_conv_cancel(update):
        return await cancel_settings_action(update, context)
    if not is_conv_skip(update) and update.message:
        context.user_data['l2tp_secret'] = update.message.text.strip()
    return await prompt_conn_sstp_ip(update, context)

async def receive_sstp_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive SSTP IP."""
    if is_conv_cancel(update):
        return await cancel_settings_action(update, context)
    if not is_conv_skip(update) and update.message:
        context.user_data['sstp_ip'] = update.message.text.strip()
    return await prompt_conn_sstp_port(update, context)

async def receive_sstp_port(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored receive_sstp_port."""
    if is_conv_cancel(update):
        return await cancel_settings_action(update, context)
    if not is_conv_skip(update) and update.message:
        context.user_data['sstp_port'] = update.message.text.strip()
    
    server_id = context.user_data.get('conn_server_id')
    
    data = {
        'l2tp': {
            'ip': context.user_data.get('l2tp_ip'),
            'version': context.user_data.get('l2tp_version'),
            'port': context.user_data.get('l2tp_port'),
            'secret': context.user_data.get('l2tp_secret')
        },
        'sstp': {
            'ip': context.user_data.get('sstp_ip'),
            'port': context.user_data.get('sstp_port')
        }
    }
    
    # Filter out None values
    clean_data = {}
    for proto in ['l2tp', 'sstp']:
        p_data = {k: v for k, v in data[proto].items() if v is not None}
        if p_data: clean_data[proto] = p_data
        
    await update_server_connection_info(server_id, clean_data)
    await update.message.reply_text(LanguageManager.get('admin.settings.conn_success'))
    
    context.user_data.clear()
    return await bot_config_menu(update, context)

# --- Ticket Subjects Management ---

async def manage_ticket_subjects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored manage_ticket_subjects."""
    query = update.callback_query
    if query: await query.answer()
    
    subjects = await get_ticket_subjects()
    
    text = LanguageManager.get('admin.settings.subjects_title')
    keyboard = []
    
    if subjects:
        text += LanguageManager.get('admin.settings.subjects_custom')
        for idx, subj in enumerate(subjects):
            text += f"{idx + 1}. {subj}\n"
            keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.settings.btn_del_subj', subj=subj[:20]), callback_data=f'subj_delete_{idx}')])
        text += "\n"
    else:
        text += LanguageManager.get('admin.settings.subjects_default')
        # Default subjects are usually managed by LanguageManager
        default_subjects = LanguageManager.get('ticket.default_subjects')
        if isinstance(default_subjects, list):
            for subj in default_subjects:
                text += f"• {subj}\n"
        text += "\n"
    
    keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.settings.btn_add_subj'), callback_data='subj_add')])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.settings.btn_reset_subj'), callback_data='subj_reset')])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='bot_config_menu')])
    
    await universal_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SETTINGS_MENU

async def add_subject_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start adding a subject."""
    query = update.callback_query
    await query.answer()
    
    await reply_conv_prompt(update, LanguageManager.get('admin.settings.add_subj_prompt'))
    return SUBJECT_VALUE

async def receive_subject_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored receive_subject_value."""
    if is_conv_cancel(update):
        return await cancel_settings_action(update, context)
    subject = update.message.text.strip()
    
    if len(subject) < 3:
        await update.message.reply_text(LanguageManager.get('admin.settings.subj_short'))
        return SUBJECT_VALUE
    
    success, reason = await add_ticket_subject(subject)
    if success:
        await update.message.reply_text(LanguageManager.get('admin.settings.subj_added', subj=subject))
    elif reason == "exists":
        await update.message.reply_text(LanguageManager.get('admin.settings.subj_exists'))
    else:
        await update.message.reply_text(LanguageManager.get('common.error'))
        
    return await manage_ticket_subjects(update, context)

async def delete_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored delete_subject."""
    query = update.callback_query
    idx = int(query.data.split('_')[2])
    
    success, deleted = await delete_ticket_subject(idx)
    if success:
        await query.answer(LanguageManager.get('admin.settings.subj_deleted', subj=deleted[:20]), show_alert=True)
    else:
        await query.answer(LanguageManager.get('admin.tickets.not_found'), show_alert=True)
    
    return await manage_ticket_subjects(update, context)

async def reset_subjects_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored reset_subjects_flow."""
    query = update.callback_query
    await reset_ticket_subjects()
    await query.answer(LanguageManager.get('admin.settings.subj_reset'), show_alert=True)
    return await manage_ticket_subjects(update, context)

# --- Language Settings ---

async def set_language_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the system language."""
    query = update.callback_query
    try:
        lang = query.data.split('_')[-1]
        
        # 1. Update settings FIRST
        await set_admin_setting('system_language', lang)
        
        LanguageManager.invalidate_language_cache()
        await LanguageManager.refresh_language(force=True)
        
        # 3. Answer IMMEDIATELY with the NEW language text
        # This stops the loading spinner and shows the toast
        success_msg = LanguageManager.get('admin.settings.lang_updated', lang=lang)
        await query.answer(success_msg)
        
        # 4. Reload settings menu with new language for live refresh
        # 4. Reload settings menu with new language for live refresh
        return await bot_config_menu(update, context)
    except Exception as e:
        from vpn_bot.utils import logger
        logger.error(f"Error in set_language_callback: {e}")
        try:
            # Fallback answer to ensure spinner stops
            await query.answer(LanguageManager.get('common.error'))
        except:
            pass
        return ConversationHandler.END

# --- Sync Settings ---

async def manage_sync_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage MikroTik sync settings."""
    query = update.callback_query
    if query:
        await query.answer()
        
    interval = await get_admin_setting('sync_interval_hours', "6h")
    last_sync = await get_admin_setting('last_sync_time', 'Never')
    
    text = LanguageManager.get('admin.settings.sync_menu', interval=interval, last=last_sync)
    
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.settings.btn_sync_now'), callback_data='sync_now')],
        [InlineKeyboardButton(LanguageManager.get('admin.settings.btn_sync_interval'), callback_data='sync_set_interval')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='bot_config_menu')]
    ]
    
    await universal_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SETTINGS_MENU

async def trigger_manual_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Trigger manual sync."""
    query = update.callback_query
    await query.answer(LanguageManager.get('admin.settings.sync_starting'), show_alert=False)
    
    # We use a toast and then edit text when done
    await query.edit_message_text(LanguageManager.get('admin.settings.sync_in_progress'))
    
    from vpn_bot.sync_manager import SyncManager
    updated = await SyncManager.sync_all_servers()
    
    await query.message.reply_text(LanguageManager.get('admin.settings.sync_complete', count=updated))
    return await manage_sync_settings(update, context)

async def set_sync_interval_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start setting sync interval."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(LanguageManager.get('admin.settings.sync_interval_prompt'))
    return SYNC_INTERVAL

async def receive_sync_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and save sync interval."""
    from vpn_bot.utils import parse_duration_to_seconds
    val_str = update.message.text.strip()
    seconds = parse_duration_to_seconds(val_str)
    
    if seconds <= 0:
        await update.message.reply_text(LanguageManager.get('admin.settings.sync_interval_invalid'))
        return SYNC_INTERVAL

    # Minimum 10 minutes (600s)
    if seconds < 600:
        val_str = "10m"
    
    await set_admin_setting('sync_interval_hours', val_str)
    await update.message.reply_text(LanguageManager.get('admin.settings.sync_interval_updated', val=val_str))
        
    return await manage_sync_settings(update, context)

# --- Backup Settings ---



async def set_backup_interval_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start setting backup interval."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(LanguageManager.get('admin.settings.backup_interval_prompt'))
    return BACKUP_INTERVAL

async def receive_backup_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and save backup interval with robust validation."""
    import re
    from vpn_bot.utils import parse_duration_to_seconds, format_seconds_human
    
    raw = update.message.text
    if not raw or not raw.strip():
        await update.message.reply_text(LanguageManager.get('admin.settings.backup_interval_invalid'))
        return BACKUP_INTERVAL
    
    val_str = raw.strip()
    
    # Reject pure text / alphabetic-only input
    if re.fullmatch(r'[a-zA-Z\u0600-\u06FF\s]+', val_str):
        await update.message.reply_text(LanguageManager.get('admin.settings.backup_interval_invalid'))
        return BACKUP_INTERVAL
    
    seconds = parse_duration_to_seconds(val_str)
    
    if seconds <= 0:
        await update.message.reply_text(LanguageManager.get('admin.settings.backup_interval_invalid'))
        return BACKUP_INTERVAL

    # Minimum 1 hour (3600s) for backups
    if seconds < 3600:
        await update.message.reply_text(
            LanguageManager.get('admin.settings.backup_interval_min_warn')
        )
        seconds = 3600
        val_str = "1h"
    
    # Normalize: if user enters plain number like "6", store as "6h"
    if re.fullmatch(r'\d+(\.\d+)?', val_str):
        val_str = f"{val_str}h"
    
    await set_admin_setting('backup_interval_hours', val_str)
    
    human = format_seconds_human(seconds)
    await update.message.reply_text(
        LanguageManager.get('admin.settings.backup_interval_updated', val=val_str, human=human)
    )
        
    return await manage_backup_settings(update, context)

# --- Currency Settings ---

async def currency_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show currency selection menu."""
    current = await get_admin_setting('currency_unit', 'USD')
    
    text = LanguageManager.get('admin.settings.currency_menu', current=current)
    
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.settings.curr_usd'), callback_data='set_curr_USD')],
        [InlineKeyboardButton(LanguageManager.get('admin.settings.curr_toman'), callback_data='set_curr_TOMAN')],
        [InlineKeyboardButton(LanguageManager.get('admin.settings.curr_rial'), callback_data='set_curr_RIAL')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='bot_config_menu')]
    ]
    await universal_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SETTINGS_MENU

async def set_currency_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the currency unit."""
    query = update.callback_query
    unit = query.data.split('_')[-1]
    
    await set_admin_setting('currency_unit', unit)
    await query.answer(LanguageManager.get('admin.settings.currency_updated', unit=unit), show_alert=True)
    
    return await currency_menu(update, context)

async def language_menu(update: Update, context: ContextTypes.DEFAULT_TYPE, answer_query=True):
    """Show language selection menu."""
    current = await get_admin_setting('system_language', 'en')
    
    text = LanguageManager.get('admin.settings.lang_menu', current=current)
    
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.settings.lang_en'), callback_data='set_lang_en')],
        [InlineKeyboardButton(LanguageManager.get('admin.settings.lang_fa'), callback_data='set_lang_fa')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='bot_config_menu')]
    ]
    await universal_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard), answer_query=answer_query)
    return SETTINGS_MENU

# --- Common Handlers ---

async def cancel_settings_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel settings multi-step flow and return to admin menu."""
    from vpn_bot.admin_conversation import admin_exit_to_menu

    if update.callback_query:
        await update.callback_query.answer(LanguageManager.get('common.cancelled'), show_alert=False)
    elif update.message:
        await update.message.reply_text(LanguageManager.get('common.cancelled'), parse_mode='Markdown')
    return await admin_exit_to_menu(update, context)

async def admin_start_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from vpn_bot.admin_conversation import admin_exit_to_menu
    return await admin_exit_to_menu(update, context)

# --- Conversation Handler ---

# --- Maintenance Management ---

async def admin_maintenance_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Maintenance management sub-menu."""
    query = update.callback_query
    if query: await query.answer()
    
    active = await get_admin_setting('system_maintenance_active', False)
    status_label = LanguageManager.get('admin.maint.status_active') if active else LanguageManager.get('admin.maint.status_inactive')
    
    text = LanguageManager.get('admin.sales.maintenance_menu_title', status=status_label)
    
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.sales.btn_toggle_maintenance'), callback_data='toggle_system_maintenance')],
        [InlineKeyboardButton(LanguageManager.get('admin.sales.btn_edit_maint_msg'), callback_data='msg_edit_maintenance_mode_msg')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='bot_config_menu')]
    ]
    
    await universal_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SETTINGS_MENU

async def toggle_system_maintenance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle global system maintenance status."""
    query = update.callback_query
    await query.answer()
    
    current = await get_admin_setting('system_maintenance_active', False)
    new_status = not current
    await set_admin_setting('system_maintenance_active', new_status)

    from vpn_bot.admin_audit import audit_log
    await audit_log(
        update.effective_user.id,
        "maintenance_toggle",
        detail={"active": new_status},
    )

    from vpn_bot.utils import logger
    logger.info(f"Admin {update.effective_user.id} toggled system maintenance to {new_status}")
    
    return await admin_maintenance_menu(update, context)

admin_settings_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(bot_config_menu, pattern='^bot_config_menu$'),
        CallbackQueryHandler(manage_payment_cards, pattern='^settings_cards$'),
        CallbackQueryHandler(manage_custom_messages, pattern='^settings_messages$'),
        CallbackQueryHandler(manage_wallet_presets, pattern='^settings_presets$'),
        CallbackQueryHandler(manage_connection_info, pattern='^settings_connection$'),
        CallbackQueryHandler(manage_ticket_subjects, pattern='^settings_subjects$'),
        CallbackQueryHandler(language_menu, pattern='^settings_lang$'),
        CallbackQueryHandler(currency_menu, pattern='^settings_currency$'),
        CallbackQueryHandler(manage_sync_settings, pattern='^settings_sync$'),
        CallbackQueryHandler(manage_purchase_terms, pattern='^settings_purchase_terms$'),
    ],
    states={
        SETTINGS_MENU: [
            CallbackQueryHandler(manage_payment_cards, pattern='^settings_cards$'),
            CallbackQueryHandler(manage_custom_messages, pattern='^settings_messages$'),
            CallbackQueryHandler(manage_purchase_terms, pattern='^settings_purchase_terms$'),
            CallbackQueryHandler(manage_wallet_presets, pattern='^settings_presets$'),
            CallbackQueryHandler(manage_connection_info, pattern='^settings_connection$'),
            CallbackQueryHandler(manage_ticket_subjects, pattern='^settings_subjects$'),
            CallbackQueryHandler(language_menu, pattern='^settings_lang$'),
            CallbackQueryHandler(set_language_callback, pattern='^set_lang_'),
            CallbackQueryHandler(currency_menu, pattern='^settings_currency$'),
            CallbackQueryHandler(set_currency_callback, pattern='^set_curr_'),
            CallbackQueryHandler(add_card_start, pattern='^card_add$'),
            CallbackQueryHandler(delete_card, pattern='^card_delete_'),
            CallbackQueryHandler(edit_message_start, pattern='^msg_edit_'),
            CallbackQueryHandler(add_preset_start, pattern='^preset_add$'),
            CallbackQueryHandler(delete_preset, pattern='^preset_delete_'),
            CallbackQueryHandler(toggle_purchase_terms, pattern='^purchase_terms_toggle$'),
            CallbackQueryHandler(toggle_purchase_terms_mode, pattern='^purchase_terms_mode_toggle$'),
            CallbackQueryHandler(edit_purchase_terms_start, pattern='^purchase_terms_edit$'),
            CallbackQueryHandler(toggle_wallet_custom_amount, pattern='^wallet_custom_toggle$'),
            CallbackQueryHandler(wallet_custom_limit_start, pattern='^wallet_custom_(min|max)$'),
            CallbackQueryHandler(edit_connection_info, pattern='^conn_server_'),
            CallbackQueryHandler(add_subject_start, pattern='^subj_add$'),
            CallbackQueryHandler(delete_subject, pattern='^subj_delete_'),
            CallbackQueryHandler(reset_subjects_flow, pattern='^subj_reset$'),
            CallbackQueryHandler(manage_sync_settings, pattern='^settings_sync$'),
            CallbackQueryHandler(trigger_manual_sync, pattern='^sync_now$'),
            CallbackQueryHandler(set_sync_interval_start, pattern='^sync_set_interval$'),

            CallbackQueryHandler(bot_config_menu, pattern='^bot_config_menu$')
        ],
        CARD_NUMBER: [
            *conv_control_handlers(cancel_settings_action),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, receive_card_number)
        ],
        CARD_HOLDER: [
            *conv_control_handlers(cancel_settings_action, skip_fn=skip_card_holder_step),
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, receive_card_holder)
        ],
        CARD_BANK: [
            *conv_control_handlers(cancel_settings_action, skip_fn=skip_card_bank_step),
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, receive_card_bank)
        ],
        MESSAGE_VALUE: [
            *conv_control_handlers(cancel_settings_action, skip_fn=skip_message_value),
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, receive_message_value)
        ],
        TERMS_TEXT_VALUE: [
            *conv_control_handlers(cancel_settings_action, skip_fn=skip_purchase_terms_value),
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, receive_purchase_terms_value)
        ],
        PRESET_AMOUNT: [
            *conv_control_handlers(cancel_settings_action),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, receive_preset_amount)
        ],
        WALLET_CUSTOM_LIMIT: [
            *conv_control_handlers(cancel_settings_action),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, receive_wallet_custom_limit)
        ],
        CONN_L2TP_IP: [
            *conv_control_handlers(cancel_settings_action),
            CommandHandler('edit', manage_connection_info),
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, receive_l2tp_ip)
        ],
        CONN_L2TP_VERSION: [
            *conv_control_handlers(cancel_settings_action),
            CallbackQueryHandler(receive_l2tp_version, pattern='^l2tp_v[23]$'),
            CallbackQueryHandler(prompt_conn_l2tp_ip, pattern='^prompt_conn_l2tp_ip$'),
        ],
        CONN_L2TP_PORT: [
            *conv_control_handlers(cancel_settings_action),
            CommandHandler('edit', prompt_conn_l2tp_version),
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, receive_l2tp_port)
        ],
        CONN_L2TP_SECRET: [
            *conv_control_handlers(cancel_settings_action),
            CommandHandler('edit', prompt_conn_l2tp_port),
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, receive_l2tp_secret)
        ],
        CONN_SSTP_IP: [
            *conv_control_handlers(cancel_settings_action),
            CommandHandler('edit', prompt_conn_l2tp_secret),
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, receive_sstp_ip)
        ],
        CONN_SSTP_PORT: [
            *conv_control_handlers(cancel_settings_action),
            CommandHandler('edit', prompt_conn_sstp_ip),
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, receive_sstp_port)
        ],
        SUBJECT_VALUE: [
            *conv_control_handlers(cancel_settings_action),
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, receive_subject_value)
        ],
        SYNC_INTERVAL: [
            *conv_control_handlers(cancel_settings_action),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, receive_sync_interval)
        ],
        BACKUP_INTERVAL: [
            *conv_control_handlers(cancel_settings_action),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, receive_backup_interval)
        ],
    },
    fallbacks=[
        CallbackQueryHandler(admin_maintenance_menu, pattern='^admin_maintenance_menu$'),
        CallbackQueryHandler(toggle_system_maintenance, pattern='^toggle_system_maintenance$'),
        CallbackQueryHandler(bot_config_menu, pattern='^bot_config_menu$'),
        *build_admin_fallback_handlers(main_menu_text_dispatch),
        CallbackQueryHandler(admin_start_callback, pattern='^.*$'),
    ],
)
