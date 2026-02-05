"""
Additional User Panel Features:
- Subscription Renewal
- Purchase History
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler
from sqlalchemy import select, desc
from datetime import datetime, timedelta

from database import AsyncSessionLocal
from models import User, Subscription, Profile, Transaction
from wallet_manager import WalletManager
from mikrotik_manager import MikroTikManager

# --- Subscription Renewal ---

async def renew_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle subscription renewal."""
    query = update.callback_query
    sub_id = int(query.data.split('_')[1])
    
    await query.answer()
    
    user_id = update.effective_user.id
    
    async with AsyncSessionLocal() as session:
        # Get user
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        if not user:
            await query.edit_message_text("❌ User not found.")
            return ConversationHandler.END
        
        # Get subscription
        subscription = await session.get(Subscription, sub_id)
        if not subscription or subscription.user_id != user.id:
            await query.edit_message_text("❌ Subscription not found.")
            return ConversationHandler.END
        
        # Get profile (check for latest version)
        profile = await session.get(Profile, subscription.profile_id)
        if not profile:
            await query.edit_message_text("❌ Plan no longer available.")
            return ConversationHandler.END
        
        # Check if there's a newer version
        p_res = await session.execute(
            select(Profile).where(
                Profile.name.like(f"{profile.name.split('_v')[0]}%"),
                Profile.server_id == profile.server_id
            ).order_by(desc(Profile.version))
        )
        latest_profile = p_res.scalars().first()
        
        if latest_profile and latest_profile.id != profile.id:
            profile = latest_profile  # Use latest version
        
        # Check balance
        if user.wallet_balance < profile.price:
            needed = profile.price - user.wallet_balance
            text = (
                f"💰 **Insufficient Balance**\n\n"
                f"Plan: {profile.name}\n"
                f"Price: ${profile.price:.2f}\n"
                f"Your Balance: ${user.wallet_balance:.2f}\n\n"
                f"❌ You need ${needed:.2f} more.\n\n"
            )
            keyboard = [
                [InlineKeyboardButton("💰 Add Funds", callback_data='wallet_menu')],
                [InlineKeyboardButton("🔙 Back", callback_data='my_subs')]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            return ConversationHandler.END
        
        # Show confirmation
        new_expiry = subscription.expiry_date + timedelta(days=profile.validity_days)
        if subscription.expiry_date < datetime.now():
            # If expired, start from now
            new_expiry = datetime.now() + timedelta(days=profile.validity_days)
        
        text = (
            f"🔄 **Renew Subscription**\n\n"
            f"Plan: {profile.name}\n"
            f"Duration: {profile.validity_days} days\n"
            f"Data: {profile.data_limit_gb}GB\n\n"
            f"💰 Payment:\n"
            f"Price: ${profile.price:.2f}\n"
            f"Current Balance: ${user.wallet_balance:.2f}\n"
            f"After Renewal: ${user.wallet_balance - profile.price:.2f}\n\n"
            f"📅 New Expiry: {new_expiry.strftime('%Y-%m-%d')}\n\n"
            f"Confirm renewal?"
        )
        
        keyboard = [
            [InlineKeyboardButton("✅ Confirm Renewal", callback_data=f'renew_confirm_{sub_id}')],
            [InlineKeyboardButton("❌ Cancel", callback_data='my_subs')]
        ]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return ConversationHandler.END

async def confirm_renewal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process renewal confirmation."""
    query = update.callback_query
    sub_id = int(query.data.split('_')[2])
    
    await query.answer("Processing renewal...")
    
    user_id = update.effective_user.id
    
    async with AsyncSessionLocal() as session:
        # Get user
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        # Get subscription
        subscription = await session.get(Subscription, sub_id)
        profile = await session.get(Profile, subscription.profile_id)
        
        # Deduct balance
        wallet_mgr = WalletManager()
        success = await wallet_mgr.deduct(user.id, profile.price, f"Renewal: {profile.name}")
        
        if not success:
            await query.edit_message_text("❌ Payment failed. Please try again.")
            return ConversationHandler.END
        
        # Extend expiry
        was_expired = subscription.expiry_date < datetime.now()
        if was_expired:
            subscription.expiry_date = datetime.now() + timedelta(days=profile.validity_days)
        else:
            subscription.expiry_date += timedelta(days=profile.validity_days)
        
        # Get server for MikroTik operations
        from models import Server
        server = await session.get(Server, subscription.server_id)
        
        # Re-enable in MikroTik and reset data limit
        if server:
            mgr = MikroTikManager(host=server.host, username=server.username, password=server.password, port=server.port)
            
            # Get user info to check if disabled
            user_info = mgr.get_user_info(subscription.mikrotik_username)
            
            if user_info:
                # If user was expired/disabled, re-enable them
                if was_expired or not user_info.get('is_active', True):
                    mgr.enable_user(subscription.mikrotik_username)
                    
                    # Update expiry in MikroTik (this will re-enable if disabled)
                    mgr.extend_validity(
                        subscription.mikrotik_username,
                        profile.validity_days
                    )

                
                # Reset data usage by updating queue limit
                # This gives them a fresh data allowance
                mgr.add_data_to_user(
                    subscription.mikrotik_username,
                    profile.data_limit_gb
                )
        
        await session.commit()
        
        text = (
            f"✅ **Renewal Successful!**\n\n"
            f"Plan: {profile.name}\n"
            f"New Expiry: {subscription.expiry_date.strftime('%Y-%m-%d')}\n"
            f"Data Limit: {profile.data_limit_gb}GB (reset)\n\n"
            f"Your VPN account has been renewed and is now active!"
        )
        
        keyboard = [[InlineKeyboardButton("📱 My Subscriptions", callback_data='my_subs')]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return ConversationHandler.END

# --- Purchase History ---

# --- Purchase History ---

async def purchase_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display purchase history."""
    query = update.callback_query
    if query:
        await query.answer()
        user_id = update.effective_user.id
    else:
        user_id = update.effective_user.id
        # Reset page if called from menu
        context.user_data['history_page'] = 0
    
    page = context.user_data.get('history_page', 0)
    per_page = 10
    
    async with AsyncSessionLocal() as session:
        # Get user
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        if not user:
            msg = "❌ User not found."
            if query:
                await query.edit_message_text(msg)
            else:
                await update.message.reply_text(msg)
            return ConversationHandler.END
        
        # Get transactions
        t_res = await session.execute(
            select(Transaction)
            .where(Transaction.user_id == user.id)
            .order_by(desc(Transaction.created_at))
            .limit(per_page)
            .offset(page * per_page)
        )
        transactions = t_res.scalars().all()
        
        # Count total
        count_res = await session.execute(
            select(Transaction).where(Transaction.user_id == user.id)
        )
        total = len(count_res.scalars().all())
    
    text = (
        f"📜 **Purchase History**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"💰 Current Balance: ${user.wallet_balance:.2f}\n\n"
        f"Recent Transactions:\n\n"
    )
    
    if not transactions:
        text += "No transactions yet."
    else:
        for txn in transactions:
            icon = "✅" if txn.type == "deposit" else "🛒"
            sign = "+" if txn.type == "deposit" else "-"
            text += (
                f"{icon} **{txn.type.title()}**\n"
                f"{sign}${txn.amount:.2f}\n"
                f"{txn.created_at.strftime('%Y-%m-%d %H:%M')}\n"
                f"{txn.description}\n"
                f"━━━━━━━━━━━━━━━━━━━━\n"
            )
    
    keyboard = []
    
    # Pagination
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton("⬅️ Previous", callback_data='history_prev'))
    if (page + 1) * per_page < total:
        nav_row.append(InlineKeyboardButton("➡️ Next", callback_data='history_next'))
    
    if nav_row:
        keyboard.append(nav_row)
    
    # Only show 'Back to Main Menu' if inside a flow, but here it's fine always
    keyboard.append([InlineKeyboardButton("🔙 Main Menu", callback_data='main_menu')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
        
    return ConversationHandler.END

# ... existing history_navigate ...

# --- Tutorials & Resources ---

async def tutorials_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display platform selection for tutorials."""
    query = update.callback_query
    if query:
        await query.answer()
    
    from admin_settings import get_admin_setting
    custom_text = await get_admin_setting('tutorial_text', 
        "📚 **VPN Setup Tutorials**\nSelect your platform to see step-by-step instructions:")
    
    keyboard = [
        [InlineKeyboardButton("📱 Android", callback_data='tutorial_android'),
         InlineKeyboardButton("🍎 iOS", callback_data='tutorial_ios')],
        [InlineKeyboardButton("💻 Windows", callback_data='tutorial_windows'),
         InlineKeyboardButton("🖥 macOS", callback_data='tutorial_mac')],
        [InlineKeyboardButton("🔗 Download Apps", callback_data='download_apps')],
        [InlineKeyboardButton("🔙 Main Menu", callback_data='main_menu')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await query.edit_message_text(custom_text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(custom_text, reply_markup=reply_markup, parse_mode='Markdown')

async def show_tutorial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show specific platform tutorial from admin settings."""
    query = update.callback_query
    platform = query.data.split('_')[1]
    await query.answer()
    
    from admin_settings import get_admin_setting
    
    # Default tutorials (used if not customized)
    default_tutorials = {
        'android': "🤖 **Android Setup**\n\n1. Download **OpenVPN Connect** from Play Store.\n2. Open the .ovpn file received after purchase.\n3. Import and connect using your credentials.",
        'ios': "🍎 **iOS Setup**\n\n1. Install **OpenVPN Connect** from App Store.\n2. Send the .ovpn file to your phone.\n3. Open file with OpenVPN app and connect.",
        'windows': "💻 **Windows Setup**\n\n1. Download **OpenVPN GUI**.\n2. Place .ovpn file in `C:\\Program Files\\OpenVPN\\config`.\n3. Right-click OpenVPN icon and select Connect.",
        'mac': "🖥 **macOS Setup**\n\n1. Download **Tunnelblick**.\n2. Double-click the .ovpn file to import.\n3. Connect from the menu bar icon."
    }
    
    # Try to get customized tutorial from admin settings
    setting_key = f'tutorial_{platform}'
    text = await get_admin_setting(setting_key, default_tutorials.get(platform, "Tutorial not found."))
    
    keyboard = [[InlineKeyboardButton("🔙 Back to Tutorials", callback_data='tutorials')]]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def download_apps_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display download links for VPN apps."""
    query = update.callback_query
    await query.answer()
    
    from admin_settings import get_admin_setting
    custom_text = await get_admin_setting('download_apps_text', 
        "💾 **Download VPN Apps**\nChoose the app for your device:")
    
    keyboard = [
        [InlineKeyboardButton("🤖 OpenVPN (Android)", url='https://play.google.com/store/apps/details?id=net.openvpn.openvpn')],
        [InlineKeyboardButton("🍎 OpenVPN (iOS)", url='https://apps.apple.com/app/openvpn-connect/id590379981')],
        [InlineKeyboardButton("💻 OpenVPN (Windows)", url='https://openvpn.net/community-downloads/')],
        [InlineKeyboardButton("🔙 Tutorials", callback_data='tutorials')]
    ]
    
    await query.edit_message_text(custom_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
