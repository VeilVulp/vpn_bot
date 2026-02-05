"""
Admin Settings Management Module
Handles payment cards, custom messages, wallet presets, and connection info.
"""

import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from sqlalchemy import select
from datetime import datetime

from database import AsyncSessionLocal
from models import AdminSetting, Server
from config import config

# States for settings conversations
(SETTINGS_MENU, CARD_NUMBER, CARD_HOLDER, CARD_BANK,
 MESSAGE_KEY, MESSAGE_VALUE,
 PRESET_AMOUNT,
 CONN_SERVER, CONN_L2TP_IP, CONN_L2TP_SECRET, CONN_SSTP_IP) = range(11)

# --- Helper Functions ---

async def get_admin_setting(key: str, default=None):
    """Get admin setting value."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AdminSetting).where(AdminSetting.key == key)
        )
        setting = result.scalars().first()
        if setting:
            try:
                return json.loads(setting.value) if setting.value else default
            except:
                return setting.value or default
        return default

async def set_admin_setting(key: str, value):
    """Set admin setting value."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AdminSetting).where(AdminSetting.key == key)
        )
        setting = result.scalars().first()
        
        value_str = json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        
        if setting:
            setting.value = value_str
            setting.updated_at = datetime.now()
        else:
            setting = AdminSetting(key=key, value=value_str)
            session.add(setting)
        
        await session.commit()

# --- Settings Main Menu ---

# --- Settings Main Menu -> Bot Configs ---

async def bot_config_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin Bot Configurations menu."""
    query = update.callback_query
    await query.answer()
    
    text = "⚙️ **Bot Configurations**\n━━━━━━━━━━━━━━━━━━━━\n\nSelect category:"
    
    keyboard = [
        [InlineKeyboardButton("💳 Payment Cards", callback_data='settings_cards')],
        [InlineKeyboardButton("📝 Custom Messages", callback_data='settings_messages')],
        [InlineKeyboardButton("💰 Wallet Presets", callback_data='settings_presets')],
        [InlineKeyboardButton("🎫 Ticket Subjects", callback_data='settings_subjects')],
        [InlineKeyboardButton("🔙 Back to Admin", callback_data='admin_start')],
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return SETTINGS_MENU

# --- Payment Cards Management ---

async def manage_payment_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display and manage payment cards."""
    query = update.callback_query
    await query.answer()
    
    cards = await get_admin_setting('payment_cards', [])
    
    text = "💳 **Payment Cards**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    
    if cards:
        for idx, card in enumerate(cards):
            masked = card.get('number', '')[:4] + '-****-****-' + card.get('number', '')[-4:]
            bank = card.get('bank', 'Unknown')
            text += f"{idx + 1}. `{masked}` ({bank})\n"
            keyboard.append([
                InlineKeyboardButton(f"🗑 Delete Card {idx + 1}", callback_data=f'card_delete_{idx}')
            ])
        text += "\n"
    else:
        text += "No cards configured.\n\n"
    
    keyboard.append([InlineKeyboardButton("➕ Add New Card", callback_data='card_add')])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='bot_config_menu')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return SETTINGS_MENU

# ... (skip add_card helpers, just need to update their return redirects if they call admin_start they are fine, but prefer bot_config_menu when done? Code used admin_start. I'll leave admin_start as it exits the sub-convo.)

# --- Custom Messages Management ---

# ...

async def manage_custom_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display custom messages menu."""
    query = update.callback_query
    await query.answer()
    
    text = "📝 **Custom Messages**\n━━━━━━━━━━━━━━━━━━━━\n\nSelect message to edit:"
    keyboard = []
    
    for key, label in MESSAGE_TEMPLATES.items():
        keyboard.append([InlineKeyboardButton(label, callback_data=f'msg_edit_{key}')])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='bot_config_menu')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return SETTINGS_MENU

# ...

# --- Wallet Presets Management ---

async def manage_wallet_presets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display and manage wallet presets."""
    query = update.callback_query
    await query.answer()
    
    presets = await get_admin_setting('wallet_presets', [5, 10, 20])
    
    text = "💰 **Wallet Charge Presets**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    
    for idx, amount in enumerate(presets):
        text += f"{idx + 1}. ${amount:.2f}\n"
        keyboard.append([InlineKeyboardButton(f"🗑 Delete ${amount}", callback_data=f'preset_delete_{idx}')])
    
    text += "\n"
    keyboard.append([InlineKeyboardButton("➕ Add New Preset", callback_data='preset_add')])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='bot_config_menu')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return SETTINGS_MENU

# ...

# --- Connection Info Management (L2TP/SSTP) ---

async def manage_connection_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage L2TP/SSTP connection info per server."""
    query = update.callback_query
    await query.answer()
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Server))
        servers = result.scalars().all()
    
    text = "🔐 **L2TP/SSTP Credentials**\n━━━━━━━━━━━━━━━━━━━━\n\nSelect server to configure:"
    keyboard = []
    
    for server in servers:
        keyboard.append([InlineKeyboardButton(f"📡 {server.name}", callback_data=f'conn_server_{server.id}')])
    
    # BACK POINT: Goes to connection_status_menu (in admin_panel)
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='connection_status_menu')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return SETTINGS_MENU # We can reuse the state or make a new one, SETTINGS_MENU works if we include usage there.

# ...

async def receive_sstp_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ... existing ...
    # At end:
    # return await manage_connection_info(update, context) # Or back to admin start? Original led to admin_start
    # Let's keep original flow or redirect to menu
    
    # ... (impl)
    if update.message.text != '/skip':
        context.user_data['sstp_ip'] = update.message.text
    
    # Save connection info
    server_id = context.user_data.get('conn_server_id')
    conn_info = await get_admin_setting('connection_info', {})
    
    if str(server_id) not in conn_info:
        conn_info[str(server_id)] = {
            'l2tp': {'ip': '', 'port': 1701, 'secret': '123456'},
            'sstp': {'ip': '', 'port': 443}
        }
    
    if 'l2tp_ip' in context.user_data:
        conn_info[str(server_id)]['l2tp']['ip'] = context.user_data['l2tp_ip']
    if 'l2tp_secret' in context.user_data:
        conn_info[str(server_id)]['l2tp']['secret'] = context.user_data['l2tp_secret']
    if 'sstp_ip' in context.user_data:
        conn_info[str(server_id)]['sstp']['ip'] = context.user_data['sstp_ip']
    
    await set_admin_setting('connection_info', conn_info)
    
    await update.message.reply_text("✅ Connection info updated successfully!")
    
    context.user_data.clear()
    # Go back to list
    return await manage_connection_info(update, context)

# --- Ticket Subjects ---

async def manage_ticket_subjects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # ...
    # Back button -> bot_config_menu
    # ...
    query = update.callback_query
    await query.answer()
    
    subjects = await get_admin_setting('ticket_subjects', [])
    # ... (fetching logic)
    
    # ... (text building)
    text = "🎫 **Ticket Subjects**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    
    if subjects:
         for idx, subj in enumerate(subjects):
            text += f"{idx + 1}. {subj}\n"
            keyboard.append([
                InlineKeyboardButton(f"🗑 Delete: {subj[:20]}...", callback_data=f'subj_delete_{idx}')
            ])
    else:
         text += "No custom subjects.\n"
         
    keyboard.append([InlineKeyboardButton("➕ Add Subject", callback_data='subj_add')])
    keyboard.append([InlineKeyboardButton("🔄 Reset", callback_data='subj_reset')])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='bot_config_menu')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return SETTINGS_MENU

# ...

# --- Conversation Handlers ---



# --- Payment Cards Management ---

async def manage_payment_cards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display and manage payment cards."""
    query = update.callback_query
    await query.answer()
    
    cards = await get_admin_setting('payment_cards', [])
    
    text = "💳 **Payment Cards**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    
    if cards:
        for idx, card in enumerate(cards):
            masked = card.get('number', '')[:4] + '-****-****-' + card.get('number', '')[-4:]
            bank = card.get('bank', 'Unknown')
            text += f"{idx + 1}. `{masked}` ({bank})\n"
            keyboard.append([
                InlineKeyboardButton(f"🗑 Delete Card {idx + 1}", callback_data=f'card_delete_{idx}')
            ])
        text += "\n"
    else:
        text += "No cards configured.\n\n"
    
    keyboard.append([InlineKeyboardButton("➕ Add New Card", callback_data='card_add')])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='bot_config_menu')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return SETTINGS_MENU

async def add_card_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start adding new card."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "💳 **Add Payment Card**\n\n"
        "Enter card number (16 digits):\n"
        "Example: 6037997412341234\n\n"
        "Or /cancel to abort",
        parse_mode='Markdown'
    )
    return CARD_NUMBER

async def receive_card_number(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive card number."""
    number = update.message.text.replace('-', '').replace(' ', '')
    
    if not number.isdigit() or len(number) != 16:
        await update.message.reply_text("❌ Invalid card number. Must be 16 digits.\nTry again or /cancel:")
        return CARD_NUMBER
    
    context.user_data['card_number'] = number
    await update.message.reply_text(
        "✅ Card number saved.\n\n"
        "Enter card holder name (or /skip):"
    )
    return CARD_HOLDER

async def receive_card_holder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive card holder name."""
    if update.message.text != '/skip':
        context.user_data['card_holder'] = update.message.text
    else:
        context.user_data['card_holder'] = ""
    
    await update.message.reply_text(
        "Enter bank name (or /skip):"
    )
    return CARD_BANK

async def receive_card_bank(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive bank name and save card."""
    if update.message.text != '/skip':
        context.user_data['card_bank'] = update.message.text
    else:
        context.user_data['card_bank'] = ""
    
    # Save card
    cards = await get_admin_setting('payment_cards', [])
    cards.append({
        'number': context.user_data['card_number'],
        'holder': context.user_data.get('card_holder', ''),
        'bank': context.user_data.get('card_bank', '')
    })
    await set_admin_setting('payment_cards', cards)
    
    # Clear context after using data for message
    masked = context.user_data.get('card_number', '')[:4] + '-****-****-' + context.user_data.get('card_number', '')[-4:]
    card_holder = context.user_data.get('card_holder', 'N/A')
    card_bank = context.user_data.get('card_bank', 'N/A')
    context.user_data.clear()
    
    await update.message.reply_text(
        f"✅ Card added successfully!\n\n"
        f"Card: `{masked}`\n"
        f"Holder: {card_holder}\n"
        f"Bank: {card_bank}",
        parse_mode='Markdown'
    )
    
    # Return to cards menu
    from admin_panel import admin_start
    await admin_start(update, context)
    return ConversationHandler.END

async def delete_card(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a payment card."""
    query = update.callback_query
    idx = int(query.data.split('_')[2])
    
    cards = await get_admin_setting('payment_cards', [])
    if 0 <= idx < len(cards):
        deleted = cards.pop(idx)
        await set_admin_setting('payment_cards', cards)
        await query.answer(f"Card deleted: {deleted.get('bank', 'Unknown')}", show_alert=True)
    else:
        await query.answer("Card not found", show_alert=True)
    
    # Refresh display
    await manage_payment_cards(update, context)
    return SETTINGS_MENU

# --- Custom Messages Management ---

MESSAGE_TEMPLATES = {
    'welcome_message': 'Welcome Message',
    'buy_service_text': 'Buy Service Text',
    'wallet_info_text': 'Wallet Info Text',
    'empty_wallet_text': 'Empty Wallet Text',
    'insufficient_balance_text': 'Insufficient Balance Text',
    'payment_success_text': 'Payment Success Text',
    'payment_rejected_text': 'Payment Rejected Text',
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
}

async def manage_custom_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display custom messages menu."""
    query = update.callback_query
    await query.answer()
    
    text = "📝 **Custom Messages**\n━━━━━━━━━━━━━━━━━━━━\n\nSelect message to edit:"
    keyboard = []
    
    for key, label in MESSAGE_TEMPLATES.items():
        keyboard.append([InlineKeyboardButton(label, callback_data=f'msg_edit_{key}')])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='bot_config_menu')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return SETTINGS_MENU

async def edit_message_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start editing a message."""
    query = update.callback_query
    key = query.data.split('_')[2]
    
    context.user_data['message_key'] = key
    current = await get_admin_setting(key, "Not set")
    
    await query.answer()
    await query.edit_message_text(
        f"📝 **Edit {MESSAGE_TEMPLATES.get(key, key)}**\n\n"
        f"**Current text:**\n{current}\n\n"
        f"Send new text or /skip to keep current:",
        parse_mode='Markdown'
    )
    return MESSAGE_VALUE

async def receive_message_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive new message value."""
    if update.message.text != '/skip':
        key = context.user_data.get('message_key')
        await set_admin_setting(key, update.message.text)
        await update.message.reply_text(f"✅ {MESSAGE_TEMPLATES.get(key, key)} updated successfully!")
    else:
        await update.message.reply_text("Message unchanged.")
    
    context.user_data.clear()
    from admin_panel import admin_start
    await admin_start(update, context)
    return ConversationHandler.END

# --- Wallet Presets Management ---

async def manage_wallet_presets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display and manage wallet presets."""
    query = update.callback_query
    await query.answer()
    
    presets = await get_admin_setting('wallet_presets', [5, 10, 20])
    
    text = "💰 **Wallet Charge Presets**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    
    for idx, amount in enumerate(presets):
        text += f"{idx + 1}. ${amount:.2f}\n"
        keyboard.append([InlineKeyboardButton(f"🗑 Delete ${amount}", callback_data=f'preset_delete_{idx}')])
    
    text += "\n"
    keyboard.append([InlineKeyboardButton("➕ Add New Preset", callback_data='preset_add')])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='bot_config_menu')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return SETTINGS_MENU

async def add_preset_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start adding preset."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "💰 **Add Wallet Preset**\n\n"
        "Enter amount in $ (e.g., 15):\n\n"
        "Or /cancel to abort",
        parse_mode='Markdown'
    )
    return PRESET_AMOUNT

async def receive_preset_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive preset amount."""
    try:
        amount = float(update.message.text)
        if amount < 1:
            raise ValueError()
        
        presets = await get_admin_setting('wallet_presets', [5, 10, 20])
        if amount not in presets:
            presets.append(amount)
            presets.sort()
            await set_admin_setting('wallet_presets', presets)
            await update.message.reply_text(f"✅ Preset ${amount:.2f} added successfully!")
        else:
            await update.message.reply_text(f"❌ Preset ${amount:.2f} already exists.")
    except ValueError:
        await update.message.reply_text("❌ Invalid amount. Try again or /cancel:")
        return PRESET_AMOUNT
    
    from admin_panel import admin_start
    await admin_start(update, context)
    return ConversationHandler.END

async def delete_preset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a preset."""
    query = update.callback_query
    idx = int(query.data.split('_')[2])
    
    presets = await get_admin_setting('wallet_presets', [5, 10, 20])
    if 0 <= idx < len(presets):
        deleted = presets.pop(idx)
        await set_admin_setting('wallet_presets', presets)
        await query.answer(f"Preset ${deleted} deleted", show_alert=True)
    else:
        await query.answer("Preset not found", show_alert=True)
    
    await manage_wallet_presets(update, context)
    return SETTINGS_MENU

# --- Connection Info Management ---

async def manage_connection_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manage L2TP/SSTP connection info per server."""
    query = update.callback_query
    await query.answer()
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Server))
        servers = result.scalars().all()
    
    text = "🔐 **Connection Information**\n━━━━━━━━━━━━━━━━━━━━\n\nSelect server to configure:"
    keyboard = []
    
    for server in servers:
        keyboard.append([InlineKeyboardButton(f"📡 {server.name}", callback_data=f'conn_server_{server.id}')])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='connection_status_menu')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return SETTINGS_MENU

async def edit_connection_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit connection info for a server."""
    query = update.callback_query
    server_id = int(query.data.split('_')[2])
    
    context.user_data['conn_server_id'] = server_id
    
    conn_info = await get_admin_setting('connection_info', {})
    server_info = conn_info.get(str(server_id), {
        'l2tp': {'ip': '', 'port': 1701, 'secret': '123456'},
        'sstp': {'ip': '', 'port': 443}
    })
    
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
    
    text = (
        f"🔐 **Connection Info: {server.name}**\n\n"
        f"**Current L2TP:**\n"
        f"IP: {server_info['l2tp'].get('ip', 'Not set')}\n"
        f"Port: {server_info['l2tp'].get('port', 1701)}\n"
        f"Secret: {server_info['l2tp'].get('secret', '123456')}\n\n"
        f"**Current SSTP:**\n"
        f"IP: {server_info['sstp'].get('ip', 'Not set')}\n"
        f"Port: {server_info['sstp'].get('port', 443)}\n\n"
        f"Enter L2TP IP address (or /skip):"
    )
    
    await query.answer()
    await query.edit_message_text(text, parse_mode='Markdown')
    return CONN_L2TP_IP

async def receive_l2tp_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive L2TP IP."""
    if update.message.text != '/skip':
        context.user_data['l2tp_ip'] = update.message.text
    
    await update.message.reply_text("Enter L2TP IPSec secret (or /skip):")
    return CONN_L2TP_SECRET

async def receive_l2tp_secret(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive L2TP secret."""
    if update.message.text != '/skip':
        context.user_data['l2tp_secret'] = update.message.text
    
    await update.message.reply_text("Enter SSTP IP address (or /skip):")
    return CONN_SSTP_IP

async def receive_sstp_ip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive SSTP IP and save."""
    if update.message.text != '/skip':
        context.user_data['sstp_ip'] = update.message.text
    
    # Save connection info
    server_id = context.user_data.get('conn_server_id')
    conn_info = await get_admin_setting('connection_info', {})
    
    if str(server_id) not in conn_info:
        conn_info[str(server_id)] = {
            'l2tp': {'ip': '', 'port': 1701, 'secret': '123456'},
            'sstp': {'ip': '', 'port': 443}
        }
    
    if 'l2tp_ip' in context.user_data:
        conn_info[str(server_id)]['l2tp']['ip'] = context.user_data['l2tp_ip']
    if 'l2tp_secret' in context.user_data:
        conn_info[str(server_id)]['l2tp']['secret'] = context.user_data['l2tp_secret']
    if 'sstp_ip' in context.user_data:
        conn_info[str(server_id)]['sstp']['ip'] = context.user_data['sstp_ip']
    
    await set_admin_setting('connection_info', conn_info)
    
    await update.message.reply_text("✅ Connection info updated successfully!")
    
    context.user_data.clear()
    from admin_panel import admin_start
    await admin_start(update, context)
    return ConversationHandler.END

# --- Ticket Subjects Management ---

SUBJECT_VALUE = 12  # New state for subject input

async def manage_ticket_subjects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display and manage ticket subjects."""
    query = update.callback_query
    await query.answer()
    
    subjects = await get_admin_setting('ticket_subjects', [])
    
    # Default subjects for reference
    default_subjects = [
        "🔌 Connection Issues",
        "💰 Payment Problem",
        "📱 App Help",
        "🔄 Renewal Request",
        "❓ General Question"
    ]
    
    text = "🎫 **Ticket Subjects**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    
    if subjects:
        text += "**Custom Subjects:**\n"
        for idx, subj in enumerate(subjects):
            text += f"{idx + 1}. {subj}\n"
            keyboard.append([
                InlineKeyboardButton(f"🗑 Delete: {subj[:20]}...", callback_data=f'subj_delete_{idx}')
            ])
        text += "\n"
    else:
        text += "(Using default subjects)\n\n"
        text += "**Default Subjects:**\n"
        for subj in default_subjects:
            text += f"• {subj}\n"
        text += "\n"
    
    keyboard.append([InlineKeyboardButton("➕ Add Subject", callback_data='subj_add')])
    keyboard.append([InlineKeyboardButton("🔄 Reset to Defaults", callback_data='subj_reset')])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='bot_config_menu')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return SETTINGS_MENU

async def add_subject_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start adding a subject."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "🎫 **Add Ticket Subject**\n\n"
        "Enter the new subject (with emoji if desired):\n\n"
        "Example: 🔧 Technical Issue\n\n"
        "Or /cancel to abort",
        parse_mode='Markdown'
    )
    return SUBJECT_VALUE

async def receive_subject_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive new subject value."""
    subject = update.message.text.strip()
    
    if len(subject) < 3:
        await update.message.reply_text("❌ Subject too short. Try again or /cancel:")
        return SUBJECT_VALUE
    
    subjects = await get_admin_setting('ticket_subjects', [])
    if subject not in subjects:
        subjects.append(subject)
        await set_admin_setting('ticket_subjects', subjects)
        await update.message.reply_text(f"✅ Subject '{subject}' added!")
    else:
        await update.message.reply_text(f"❌ Subject already exists.")
    
    from admin_panel import admin_start
    await admin_start(update, context)
    return ConversationHandler.END

async def delete_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a subject."""
    query = update.callback_query
    idx = int(query.data.split('_')[2])
    
    subjects = await get_admin_setting('ticket_subjects', [])
    if 0 <= idx < len(subjects):
        deleted = subjects.pop(idx)
        await set_admin_setting('ticket_subjects', subjects)
        await query.answer(f"Deleted: {deleted[:20]}...", show_alert=True)
    else:
        await query.answer("Subject not found", show_alert=True)
    
    await manage_ticket_subjects(update, context)
    return SETTINGS_MENU

async def reset_subjects(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reset to default subjects."""
    query = update.callback_query
    await set_admin_setting('ticket_subjects', [])
    await query.answer("Reset to default subjects!", show_alert=True)
    await manage_ticket_subjects(update, context)
    return SETTINGS_MENU

# --- Conversation Handlers ---

admin_settings_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(bot_config_menu, pattern='^bot_config_menu$'),
        CallbackQueryHandler(manage_payment_cards, pattern='^settings_cards$'),
        CallbackQueryHandler(manage_custom_messages, pattern='^settings_messages$'),
        CallbackQueryHandler(manage_wallet_presets, pattern='^settings_presets$'),
        CallbackQueryHandler(manage_connection_info, pattern='^settings_connection$'),
        CallbackQueryHandler(manage_ticket_subjects, pattern='^settings_subjects$'),
    ],
    states={
        SETTINGS_MENU: [
            CallbackQueryHandler(manage_payment_cards, pattern='^settings_cards$'),
            CallbackQueryHandler(manage_custom_messages, pattern='^settings_messages$'),
            CallbackQueryHandler(manage_wallet_presets, pattern='^settings_presets$'),
            CallbackQueryHandler(manage_connection_info, pattern='^settings_connection$'),
            CallbackQueryHandler(manage_ticket_subjects, pattern='^settings_subjects$'),
            CallbackQueryHandler(add_card_start, pattern='^card_add$'),
            CallbackQueryHandler(delete_card, pattern='^card_delete_'),
            CallbackQueryHandler(edit_message_start, pattern='^msg_edit_'),
            CallbackQueryHandler(add_preset_start, pattern='^preset_add$'),
            CallbackQueryHandler(delete_preset, pattern='^preset_delete_'),
            CallbackQueryHandler(edit_connection_info, pattern='^conn_server_'),
            CallbackQueryHandler(add_subject_start, pattern='^subj_add$'),
            CallbackQueryHandler(delete_subject, pattern='^subj_delete_'),
            CallbackQueryHandler(reset_subjects, pattern='^subj_reset$'),
            CallbackQueryHandler(bot_config_menu, pattern='^bot_config_menu$')
        ],
        CARD_NUMBER: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_card_number)],
        CARD_HOLDER: [MessageHandler(filters.TEXT, receive_card_holder)],
        CARD_BANK: [MessageHandler(filters.TEXT, receive_card_bank)],
        MESSAGE_VALUE: [MessageHandler(filters.TEXT, receive_message_value)],
        PRESET_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_preset_amount)],
        CONN_L2TP_IP: [MessageHandler(filters.TEXT, receive_l2tp_ip)],
        CONN_L2TP_SECRET: [MessageHandler(filters.TEXT, receive_l2tp_secret)],
        CONN_SSTP_IP: [MessageHandler(filters.TEXT, receive_sstp_ip)],
        SUBJECT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_subject_value)],
    },
    fallbacks=[
        CommandHandler('cancel', lambda u, c: ConversationHandler.END),
        CallbackQueryHandler(bot_config_menu, pattern='^bot_config_menu$'),
    ]
)
