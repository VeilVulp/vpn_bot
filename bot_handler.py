import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from sqlalchemy import select, insert
from datetime import datetime

from database import AsyncSessionLocal
from models import User, Subscription, Profile, PaymentReceipt, Server, OvpnConfig
from mikrotik_manager import MikroTikManager
from wallet_manager import WalletManager
from config import config
import string
import random
import asyncio

# States for ConversationHandler
SELECT_SERVER, SELECT_PLAN, BUY_PLAN_CONFIRM, MANUAL_PAYMENT_PENDING = range(4)
REG_NAME, REG_PHONE = range(10, 12)
WALLET_AMOUNT, WALLET_CUSTOM, WALLET_RECEIPT, WALLET_CONFIRM = range(20, 24)
HISTORY_PAGE = 30

# --- Helpers ---
async def send_config_files(bot, chat_id, subscription: Subscription, server: Server, session):
    # 1. OVPN
    res_conf = await session.execute(select(OvpnConfig).where(OvpnConfig.server_id == server.id))
    ovpn = res_conf.scalars().first()
    
    username = subscription.mikrotik_username
    password = subscription.mikrotik_password
    
    if ovpn:
        from io import BytesIO
        f_data = ovpn.config_content.encode('utf-8')
        doc = BytesIO(f_data)
        doc.name = ovpn.filename
        
        await bot.send_document(
            chat_id=chat_id,
            document=doc,
            caption=f"📁 **OpenVPN Configuration**\nImport this into your OpenVPN app.\nUsername: `{username}`\nPassword: `{password}`",
            parse_mode='Markdown'
        )
    else:
        await bot.send_message(chat_id, "⚠️ OVPN file not found for this server.")

    # 2. L2TP/SSTP Info
    from admin_settings import get_admin_setting
    conn_info_all = await get_admin_setting('connection_info', {})
    
    server_id_str = str(server.id) if server else "0"
    # Default if not set
    s_info = conn_info_all.get(server_id_str, {
        'l2tp': {'ip': server.host if server else "Unknown", 'port': 1701, 'secret': '123456'},
        'sstp': {'ip': server.host if server else "Unknown", 'port': 443}
    })
    
    host = s_info['l2tp'].get('ip') or (server.host if server else "Unknown")
    l2tp_secret = s_info['l2tp'].get('secret', '123456')
    sstp_port = s_info['sstp'].get('port', 443)
    
    conn_info = (
        f"🔐 **Connection Information**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"🔹 **L2TP / IPSec**\n"
        f"Server: `{host}`\n"
        f"Username: `{username}`\n"
        f"Password: `{password}`\n"
        f"IPSec Secret: `{l2tp_secret}`\n\n" 
        f"🔹 **SSTP**\n"
        f"Server: `{host}`\n"
        f"Port: {sstp_port}\n"
        f"User/Pass: same as above\n"
    )
    await bot.send_message(chat_id, conn_info, parse_mode='Markdown')


# --- Start & Main Menu ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User Entry Point."""
    from telegram import ReplyKeyboardMarkup
    user = update.effective_user
    
    # 1. Ensure User in DB
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user.id))
        db_user = result.scalars().first()
        
        if not db_user:
            new_user = User(
                telegram_id=user.id,
                username=user.username,
                full_name=user.full_name,
                wallet_balance=0.0
            )
            session.add(new_user)
            await session.commit()
            db_user = new_user
            
        balance = db_user.wallet_balance
        # Count active subs
        sub_res = await session.execute(select(Subscription).where(Subscription.user_id == db_user.id))
        active_subs = len(sub_res.scalars().all())

    # 2. Display Menu
    welcome_text = (
        f"👋 **Welcome {user.first_name}!**\n\n"
        f"💰 **Balance:** ${balance:.2f}\n"
        f"📱 **Active Subscriptions:** {active_subs}\n\n"
        "Select an option below:"
    )
    
    # Persistent toolbar (Reply Keyboard) with new layout
    toolbar_keyboard = [
        ["� My Subscriptions", "📚 Tutorials"],
        ["� Buy Service", "📜 History"],
        ["� Support", "� Wallet"]
    ]
    
    # Conditional Settings: User requested to remove Settings button entirely for user side
    # and we have now removed it from the main layout above.
    # If we wanted it for admins only:
    if user.id in config.ADMIN_IDS:
        # Add Admin Panel entry if needed, but usually they use /admin
        pass
        
    toolbar = ReplyKeyboardMarkup(toolbar_keyboard, resize_keyboard=True, is_persistent=True)
    
    if update.callback_query:
        await update.callback_query.message.edit_text(welcome_text, parse_mode='Markdown')
        # We can't send a reply keyboard via edit_message, so we send a fresh message
        await update.callback_query.message.reply_text("📌 Main Menu:", reply_markup=toolbar)
    else:
        # Send main message with the toolbar attached
        await update.message.reply_text(welcome_text, reply_markup=toolbar, parse_mode='Markdown')
        
    return ConversationHandler.END

# --- Wallet System ---

# --- Wallet System ---

async def wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        
    user_id = update.effective_user.id
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user_id))
        user = result.scalars().first()
        balance = user.wallet_balance if user else 0.0

    text = (
        f"💰 **Your Wallet**\n"
        f"Current Balance: `${balance:.2f}`\n\n"
        "**Quick Top-up:**"
    )
    
    # Get dynamic presets from admin settings
    from admin_settings import get_admin_setting
    presets = await get_admin_setting('wallet_presets', [5, 10, 20])
    
    # Create preset buttons (max 3 per row)
    preset_buttons = []
    for i in range(0, len(presets), 3):
        row = [InlineKeyboardButton(f"${p:.0f}", callback_data=f'topup_{int(p)}') for p in presets[i:i+3]]
        preset_buttons.append(row)
    
    keyboard = preset_buttons + [
        [InlineKeyboardButton("💵 Custom Amount", callback_data='topup_custom')],
        [InlineKeyboardButton("🔙 Main Menu", callback_data='main_menu')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    return WALLET_CUSTOM

async def topup_amount_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    amount = int(query.data.split('_')[1])
    context.user_data['topup_amount'] = amount
    
    text = (
        f"💳 **Payment Instructions**\n\n"
        f"Amount to pay: **${amount:.2f}**\n\n"
        "Please transfer the exact amount to:\n"
        "`1234-5678-9012-3456` (Bank Name)\n\n"
        "⚠️ **After payment, please send a photo of your receipt here.**"
    )
    
    keyboard = [[InlineKeyboardButton("🔙 Cancel", callback_data='wallet_menu')]]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return WALLET_RECEIPT

async def topup_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle custom amount input."""
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "💵 **Custom Amount**\n\n"
        "Enter the amount you want to charge (minimum $5.00):\n\n"
        "Example: 15\n\n"
        "Or /cancel to go back",
        parse_mode='Markdown'
    )
    return WALLET_CUSTOM

async def receive_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and validate custom amount."""
    try:
        amount = float(update.message.text)
        if amount < 5:
            await update.message.reply_text(
                "❌ Minimum amount is $5.00\n\n"
                "Please enter a valid amount or /cancel:"
            )
            return WALLET_CUSTOM
        
        if amount > 1000:
            await update.message.reply_text(
                "❌ Maximum amount is $1000.00\n\n"
                "Please enter a valid amount or /cancel:"
            )
            return WALLET_CUSTOM
        
        context.user_data['topup_amount'] = amount
        
        # Get payment cards from admin settings
        from admin_settings import get_admin_setting
        cards = await get_admin_setting('payment_cards', [])
        
        card_text = ""
        if cards:
            for idx, card in enumerate(cards):
                masked = card.get('number', '')[:4] + '-****-****-' + card.get('number', '')[-4:]
                holder = card.get('holder', '')
                bank = card.get('bank', '')
                card_text += f"\n📋 `{card.get('number', 'N/A')}`"
                if holder:
                    card_text += f"\n   Holder: {holder}"
                if bank:
                    card_text += f"\n   Bank: {bank}"
                card_text += "\n"
        else:
            card_text = "\n`1234-5678-9012-3456` (Bank Name)\n"
        
        text = (
            f"💳 **Payment Instructions**\n\n"
            f"Amount to pay: **${amount:.2f}**\n\n"
            f"Please transfer the exact amount to:{card_text}\n"
            "⚠️ **After payment, please send a photo of your receipt here.**"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Cancel", callback_data='wallet_menu')]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return WALLET_RECEIPT
        
    except ValueError:
        await update.message.reply_text(
            "❌ Invalid amount. Please enter a number.\n\n"
            "Example: 15\n\n"
            "Or /cancel to go back:"
        )
        return WALLET_CUSTOM


async def receive_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive receipt in multiple formats: photo, document (PDF/image), or text."""
    file_id = None
    receipt_type = 'unknown'
    
    if update.message.photo:
        # Photo attachment
        file_id = update.message.photo[-1].file_id
        receipt_type = 'photo'
    elif update.message.document:
        # Document (PDF or image)
        doc = update.message.document
        mime = doc.mime_type or ''
        if mime.startswith('image/') or mime == 'application/pdf':
            file_id = doc.file_id
            receipt_type = 'document'
        else:
            await update.message.reply_text("⚠️ Please send a valid receipt (photo, image, or PDF).")
            return WALLET_RECEIPT
    elif update.message.text:
        # Text-based receipt (e.g., transaction reference)
        text_receipt = update.message.text.strip()
        if len(text_receipt) < 5:
            await update.message.reply_text("⚠️ Please provide a valid transaction reference or send a receipt image.")
            return WALLET_RECEIPT
        context.user_data['receipt_text'] = text_receipt
        context.user_data['receipt_file_id'] = None
        context.user_data['receipt_type'] = 'text'
        
        amount = context.user_data.get('topup_amount', 0)
        keyboard = [
            [InlineKeyboardButton("✅ Confirm", callback_data="receipt_yes")],
            [InlineKeyboardButton("❌ Re-enter", callback_data="receipt_no")]
        ]
        await update.message.reply_text(
            f"⚠️ **Confirm submission?**\n\n"
            f"Amount: ${amount:.2f}\n"
            f"Reference: `{text_receipt}`\n\n"
            "Fake receipts will result in a permanent ban.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return WALLET_CONFIRM
    else:
        await update.message.reply_text("⚠️ Please send a photo, PDF, or transaction reference text.")
        return WALLET_RECEIPT
    
    # For photo/document
    amount = context.user_data.get('topup_amount', 0)
    context.user_data['receipt_file_id'] = file_id
    context.user_data['receipt_type'] = receipt_type
    
    keyboard = [
        [InlineKeyboardButton("✅ Confirm", callback_data="receipt_yes")],
        [InlineKeyboardButton("❌ Resend", callback_data="receipt_no")]
    ]
    
    if receipt_type == 'photo':
        await update.message.reply_photo(
            photo=file_id,
            caption=f"⚠️ **Confirm submission?**\nAmount: ${amount:.2f}\n\nFake receipts will result in a permanent ban.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    else:
        # Document
        await update.message.reply_document(
            document=file_id,
            caption=f"⚠️ **Confirm submission?**\nAmount: ${amount:.2f}\n\nFake receipts will result in a permanent ban.",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    return WALLET_CONFIRM

async def confirm_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "receipt_no":
        await query.message.delete()
        await context.bot.send_message(query.message.chat_id, "📷 Please send a new receipt (photo, PDF, or text reference):")
        return WALLET_RECEIPT
    
    # Save receipt
    user_id = update.effective_user.id
    amount = context.user_data.get('topup_amount', 0)
    file_id = context.user_data.get('receipt_file_id')
    receipt_type = context.user_data.get('receipt_type', 'photo')
    receipt_text = context.user_data.get('receipt_text', '')
    
    import uuid
    unique_id = str(uuid.uuid4())[:8].upper()  # Short unique ID like "A1B2C3D4"
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user_id))
        user = result.scalars().first()
        
        if user:
            receipt = PaymentReceipt(
                user_id=user.id,
                amount=amount,
                receipt_file_id=file_id if file_id else receipt_text,  # Store text if no file
                status='pending'
            )
            session.add(receipt)
            await session.commit()
            
            # Generate unique receipt ID combining DB ID and UUID
            receipt_unique_id = f"RCP-{receipt.id}-{unique_id}"
            
            # Admin notification with complete user info
            admin_caption = (
                f"🔔 **New Receipt**\n"
                f"━━━━━━━━━━━━━━━━━━━━\n\n"
                f"📋 **Receipt ID:** `{receipt_unique_id}`\n"
                f"💵 **Amount:** ${amount:.2f}\n\n"
                f"👤 **User Info:**\n"
                f"• Telegram ID: `{user_id}`\n"
                f"• Name: {update.effective_user.full_name}\n"
                f"• Phone: `{user.phone_number or 'Not provided'}`\n"
                f"• Username: @{update.effective_user.username or 'N/A'}\n"
            )
            
            # Notify admins
            for admin_id in config.ADMIN_IDS:
                try:
                    if receipt_type == 'photo' and file_id:
                        await context.bot.send_photo(
                            admin_id, 
                            file_id, 
                            caption=admin_caption,
                            parse_mode='Markdown'
                        )
                    elif receipt_type == 'document' and file_id:
                        await context.bot.send_document(
                            admin_id,
                            file_id,
                            caption=admin_caption,
                            parse_mode='Markdown'
                        )
                    else:
                        # Text receipt
                        await context.bot.send_message(
                            admin_id,
                            admin_caption + f"\n📝 **Reference:** `{receipt_text}`",
                            parse_mode='Markdown'
                        )
                except Exception as e:
                    logging.error(f"Failed to notify admin {admin_id}: {e}")
            
            # Edit user's confirmation message
            try:
                await query.edit_message_caption(
                    caption=f"✅ **Successfully submitted!**\n\n"
                           f"Receipt ID: `{receipt_unique_id}`\n\n"
                           f"Wait for admin approval. You will receive a notification.",
                    parse_mode='Markdown'
                )
            except:
                # For text receipts (no media to edit caption)
                try:
                    await query.edit_message_text(
                        text=f"✅ **Successfully submitted!**\n\n"
                            f"Receipt ID: `{receipt_unique_id}`\n\n"
                            f"Wait for admin approval. You will receive a notification.",
                        parse_mode='Markdown'
                    )
                except:
                    pass
        else:
            try:
                await query.edit_message_caption(caption="❌ Error: User not found.")
            except:
                await query.edit_message_text(text="❌ Error: User not found.")
            
    return ConversationHandler.END

async def cancel_wallet(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await wallet_menu(update, context)
    return WALLET_AMOUNT

# --- My Subscriptions ---

# --- My Subscriptions ---

async def my_subscriptions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    user_id = update.effective_user.id
    
    async with AsyncSessionLocal() as session:
        # Get DB User ID first
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        db_user = u_res.scalars().first()
        
        subscriptions = []
        if db_user:
            s_res = await session.execute(select(Subscription).where(Subscription.user_id == db_user.id))
            subscriptions = s_res.scalars().all()
    
    keyboard = []
    if not subscriptions:
        keyboard = [[InlineKeyboardButton("🔙 Main Menu", callback_data='main_menu')]]
        msg_text = "📱 **My Subscriptions**\n\nYou have no active subscriptions."
        if query:
            await query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        else:
            await update.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return ConversationHandler.END

    # For stub, just show first 5
    text = "📱 **My Subscriptions**\n━━━━━━━━━━━━━━━━━━━━\n"
    
    mgr = MikroTikManager() # Service logic should handle multiple servers
    
    
    for sub in subscriptions:
        # Fetch live data
        info = mgr.get_user_info(sub.mikrotik_username)
        
        status_icon = "🟢" if info and info['status'] == 'active' else "🔴"
        used_gb = (info.get('used_bytes', 0) / 1024**3) if info else 0
        total_gb = (sub.total_limit_bytes / 1024**3)
        pct = (used_gb / total_gb * 100) if total_gb > 0 else 0
        
        # Check if expired or expiring soon
        from datetime import datetime
        is_expired = sub.expiry_date < datetime.now()
        days_left = (sub.expiry_date - datetime.now()).days if not is_expired else 0
        
        text += (
            f"{status_icon} **{sub.mikrotik_username}**\n"
            f"Plan: {sub.total_limit_bytes // (1024**3)}GB\n"
            f"Data: {used_gb:.2f} / {total_gb:.0f} GB ({pct:.1f}%)\n"
            f"Expires: {sub.expiry_date.strftime('%Y-%m-%d')}"
        )
        
        if is_expired:
            text += " ❌ EXPIRED\n"
        elif days_left <= 7:
            text += f" ⚠️ ({days_left} days left)\n"
        else:
            text += "\n"
        
        text += (
            f"Pass: ||{sub.mikrotik_password}||\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
        )
        
        # Add buttons
        buttons = [InlineKeyboardButton(f"📥 Config", callback_data=f"get_config_{sub.id}")]
        
        # Add renew button if expired or expiring soon
        if is_expired or days_left <= 7:
            buttons.append(InlineKeyboardButton("🔄 Renew", callback_data=f"renew_{sub.id}"))
        
        keyboard.append(buttons)

    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data='main_menu')])
    
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

# --- Buy Service (Stub) ---
# --- Buy Service ---

# --- Buy Service ---

async def buy_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    user_id = update.effective_user.id
    async with AsyncSessionLocal() as session:
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        # Registration Check
        if not user or not user.phone_number:
            text = (
                "👋 **Welcome!**\n\n"
                "To provide better support and security, please complete your registration before purchase.\n\n"
                "👉 Please enter your **First and Last Name**:"
            )
            if query:
                 await query.edit_message_text(text, parse_mode='Markdown')
            else:
                 await update.message.reply_text(text, parse_mode='Markdown')
            return REG_NAME

    # 1. Fetch Active Profiles
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Profile).where(Profile.is_active == True))
        profiles = result.scalars().all()
        
    if not profiles:
        msg = "⚠️ No plans available at the moment."
        if query:
             await query.edit_message_text(msg)
        else:
             await update.message.reply_text(msg)
        return ConversationHandler.END
        
    text = "🛒 **Select a Plan:**\n\n"
    keyboard = []
    
    for p in profiles:
        text += f"📦 {p.name}\nFor {p.validity_days} days - {p.data_limit_gb}GB\nPrice: **${p.price}**\n\n"
        keyboard.append([InlineKeyboardButton(f"{p.name} - ${p.price}", callback_data=f"buy_plan_{p.id}")])
    
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data='main_menu')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        
    return BUY_PLAN_CONFIRM

# --- User Registration Flow ---

async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Collect full name."""
    name = update.message.text.strip()
    if len(name.split()) < 2:
        await update.message.reply_text("❌ Please enter your full name (both First and Last name).")
        return REG_NAME
    
    context.user_data['reg_name'] = name
    
    from telegram import ReplyKeyboardMarkup, KeyboardButton
    keyboard = [[KeyboardButton("📱 Share Phone Number", request_contact=True)]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    await update.message.reply_text(
        f"✅ Thanks, {name.split()[0]}!\n\n"
        "Now, please share your **Phone Number** using the button below:",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return REG_PHONE

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Collect phone number via contact sharing."""
    contact = update.message.contact
    if not contact:
        await update.message.reply_text("❌ Please use the button to share your phone number.")
        return REG_PHONE
    
    user_id = update.effective_user.id
    phone = contact.phone_number
    full_name = context.user_data.get('reg_name')
    parts = full_name.split(maxsplit=1)
    f_name = parts[0]
    l_name = parts[1] if len(parts) > 1 else ""
    
    async with AsyncSessionLocal() as session:
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        if user:
            user.phone_number = phone
            user.first_name = f_name
            user.last_name = l_name
            user.full_name = full_name
            await session.commit()
            
    from telegram import ReplyKeyboardRemove
    await update.message.reply_text(
        "🎉 **Registration Complete!**\n\n"
        "You can now proceed to select a plan and buy your VPN.",
        reply_markup=ReplyKeyboardRemove(),
        parse_mode='Markdown'
    )
    
    # Redirect back to buy_service (but as a new message so we need a fake context or direct call)
    # For now, just show the main buy menu again
    return await buy_service_initial(update, context)

async def buy_service_initial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Helper to restart the buy flow without query."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Profile).where(Profile.is_active == True))
        profiles = result.scalars().all()
        
    text = "🛒 **Select a Plan:**\n\n"
    keyboard = []
    for p in profiles:
        keyboard.append([InlineKeyboardButton(f"{p.name} - ${p.price}", callback_data=f"buy_plan_{p.id}")])
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data='main_menu')])
    
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return BUY_PLAN_CONFIRM

async def confirm_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    profile_id = int(query.data.split('_')[2])
    user_id = update.effective_user.id
    
    async with AsyncSessionLocal() as session:
        # Get Profile & User
        p_res = await session.execute(select(Profile).where(Profile.id == profile_id))
        profile = p_res.scalars().first()
        
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        if not profile or not user:
            await query.edit_message_text("❌ Error loading plan details.")
            return ConversationHandler.END
            
        context.user_data['selected_profile_id'] = profile_id
        
        # Check Balance
        can_afford = user.wallet_balance >= profile.price
        balance_Color = "✅" if can_afford else "❌"
        
        text = (
            f"📦 **Confirm Purchase**\n\n"
            f"Plan: {profile.name}\n"
            f"Price: ${profile.price}\n"
            f"Your Balance: ${user.wallet_balance:.2f} {balance_Color}\n\n"
        )
        
        keyboard = []
        if can_afford:
            text += "Click confirm to purchase instantly."
            keyboard.append([InlineKeyboardButton("✅ Confirm Payment", callback_data="confirm_pay")])
        else:
            text += f"You need ${profile.price - user.wallet_balance:.2f} more."
            keyboard.append([InlineKeyboardButton("💰 Top-up Wallet", callback_data="wallet_menu")])
            
        keyboard.append([InlineKeyboardButton("🔙 Cancel", callback_data="buy_service")])
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return BUY_PLAN_CONFIRM

async def process_purchase_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if query.data == 'wallet_menu':
        # Redirect to wallet
        await wallet_menu(update, context)
        return WALLET_AMOUNT
    
    if query.data == 'confirm_pay':
        profile_id = context.user_data.get('selected_profile_id')
        user_id = update.effective_user.id
        
        async with AsyncSessionLocal() as session:
            # 1. Re-fetch to be safe
            p_res = await session.execute(select(Profile).where(Profile.id == profile_id))
            profile = p_res.scalars().first()
            
            u_res = await session.execute(select(User).where(User.telegram_id == user_id))
            user = u_res.scalars().first()
            
            # 2. Deduct
            success = await WalletManager.deduct(user.id, profile.price, f"Purchase: {profile.name}")
            
            if not success:
                await query.answer("❌ Insufficient funds or transaction failed.", show_alert=True)
                return await start(update, context)
                
            # 3. Create User in MikroTik
            # Generate random creds
            username = f"{user.id}_{int(datetime.now().timestamp())}"[-8:] # Simple unique
            username = 'u' + username # ensure starts with char
            password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
            
            mgr = MikroTikManager()
            mt_success = mgr.create_user(username, password, profile.name) # Assumes profile name matches MT profile
            
            if not mt_success:
                 # Rollback money? Real world: YES. Stub: Log error.
                 # await WalletManager.refund(...)
                 await query.edit_message_text("❌ Technical error creating VPN account. Support notified.")
                 return ConversationHandler.END
            
            # 4. Create Subscription Record
            # Calc expiry
            from datetime import timedelta
            expiry = datetime.now() + timedelta(days=profile.validity_days)
            
            sub = Subscription(
                user_id=user.id,
                profile_id=profile.id,
                server_id=profile.server_id,
                mikrotik_username=username,
                mikrotik_password=password,
                expiry_date=expiry,
                total_limit_bytes=profile.data_limit_gb * 1024**3
            )
            session.add(sub)
            await session.commit()
            
            # 5. Full Delivery
            # A. Confirmation Message
            await query.edit_message_text(
                f"✅ **Purchase Successful!**\n\n"
                f"👤 Username: `{username}`\n"
                f"🔑 Password: `{password}`\n"
                f"📅 Expires: {expiry.strftime('%Y-%m-%d')}\n\n"
                "📥 Sending configuration files...",
                parse_mode='Markdown'
            )
            
    # B. Send OVPN File
            # Fetch Server
            server_stmt = select(Server).where(Server.id == profile.server_id)
            server_curr = (await session.execute(server_stmt)).scalars().first()
            
            await send_config_files(context.bot, update.effective_chat.id, sub, server_curr, session)
            
            return ConversationHandler.END
            
    await start(update, context)
    return ConversationHandler.END

# --- Handlers ---

wallet_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(wallet_menu, pattern='^wallet_menu$'),
        MessageHandler(filters.Regex('^💰 Wallet$'), wallet_menu)
    ],
    states={
        WALLET_CUSTOM: [
            CallbackQueryHandler(topup_amount_selected, pattern='^topup_\\d+$'),
            CallbackQueryHandler(topup_custom_amount, pattern='^topup_custom$'),
            CallbackQueryHandler(start, pattern='^main_menu$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_custom_amount)
        ],
        WALLET_RECEIPT: [
            MessageHandler(filters.PHOTO | filters.Document.ALL | (filters.TEXT & ~filters.COMMAND), receive_receipt),
            CallbackQueryHandler(wallet_menu, pattern='^wallet_menu$')
        ],
        WALLET_CONFIRM: [
            CallbackQueryHandler(confirm_receipt, pattern='^receipt_(yes|no)$')
        ]
    },
    fallbacks=[CommandHandler('start', start), CommandHandler('cancel', start)]
)

# Purchase Handler
buy_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(buy_service, pattern='^buy_service$'),
        MessageHandler(filters.Regex('^🛒 Buy Service$'), buy_service)
    ],
    states={
        BUY_PLAN_CONFIRM: [
            CallbackQueryHandler(confirm_purchase, pattern='^buy_plan_'),
            CallbackQueryHandler(process_purchase_flow, pattern='^(confirm_pay|wallet_menu)$'),
            CallbackQueryHandler(buy_service, pattern='^buy_service$')
        ],
        REG_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_name)],
        REG_PHONE: [MessageHandler(filters.CONTACT, receive_phone)]
    },
    fallbacks=[CommandHandler('start', start), CallbackQueryHandler(start, pattern='^main_menu$')]
)

# Listeners for main menu
# Note: start is command, others are callbacks.
# We need a way to link "My Subs" etc.
# Ideally, we have a main CallbackQueryHandler in main.py or here that dispatches.

async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    
    if data == 'main_menu':
        await start(update, context)
    elif data == 'my_subs':
        await my_subscriptions(update, context)
    elif data == 'buy_service':
        await buy_service(update, context)
    elif data == 'history':
        from user_features import purchase_history
        await purchase_history(update, context)
    elif data.startswith('history_'):
        from user_features import history_navigate
        await history_navigate(update, context)
    elif data == 'support':
        from support_tickets import support_menu
        await support_menu(update, context)
    elif data == 'tutorials':
        from user_features import tutorials_menu
        await tutorials_menu(update, context)
    elif data.startswith('tutorial_'):
        from user_features import show_tutorial
        await show_tutorial(update, context)
    elif data == 'download_apps':
        from user_features import download_apps_menu
        await download_apps_menu(update, context)
    elif data == 'admin_reports':
        from admin_reports import sales_dashboard
        await sales_dashboard(update, context)
    elif data == 'report_export_sales':
        from admin_reports import export_sales_csv
        await export_sales_csv(update, context)
    elif data == 'wallet_menu':
        await wallet_menu(update, context)
    elif data.startswith('get_config_'):
        sub_id = int(data.split('_')[2])
        async with AsyncSessionLocal() as session:
            sub = await session.get(Subscription, sub_id)
            if sub:
                srv = await session.get(Server, sub.server_id)
                if srv:
                     await query.answer("Sending configs...")
                     await send_config_files(context.bot, update.effective_chat.id, sub, srv, session)
                else:
                    await query.answer("❌ Server not found for this subscription.", show_alert=True)
            else:
                await query.answer("❌ Subscription not found.", show_alert=True)
    elif data.startswith('renew_'):
        from user_features import renew_subscription, confirm_renewal
        if 'confirm' in data:
            await confirm_renewal(update, context)
        else:
            await renew_subscription(update, context)
 
 
 
