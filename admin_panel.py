import logging
import os
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from sqlalchemy import select, update
from datetime import datetime, timedelta

from database import AsyncSessionLocal
from models import Server, Subscription, Profile, User as DBUser, Transaction, AdminSetting, Admin, PaymentReceipt, OvpnConfig
from mikrotik_manager import MikroTikManager
from admin_management import is_user_admin, is_super_admin, add_admin, remove_admin, list_admins
from notification_manager import NotificationManager
from wallet_manager import WalletManager
from config import config
from utils import logger

# States
SEARCH_USERNAME, USER_ACTION, RESET_PASS, ADD_DATA, EXTEND_TIME, DELETE_CONFIRM, EDIT_BALANCE = range(7)
WAIT_OVPN_FILE = 7
WAIT_IMPORT_FILE = 8
ADMIN_MGMT_ID = 9
ADD_ADMIN_USERNAME, REMOVE_ADMIN_USERNAME = range(10, 12)
BROADCAST_MSG, TARGETED_USER_ID, TARGETED_MSG = range(20, 23)

# --- Helpers ---
def get_mikrotik_manager(server: Server = None):
    # For now, using default config or first server. 
    # In full server management, we'd pick based on subscription.server_id
    if server:
        return MikroTikManager(host=server.host, username=server.username, password=server.password, port=server.port)
    return MikroTikManager() # Use defaults from config

async def admin_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin main menu."""
    user_id = update.effective_user.id
    if not await is_user_admin(user_id):
        return # Silent fail for security

    keyboard = [
        [InlineKeyboardButton("🔍 Search User", callback_data='search_user')],
        [InlineKeyboardButton("📡 Server List", callback_data='list_servers'),
         InlineKeyboardButton("📦 Profiles", callback_data='list_profiles')],
        [InlineKeyboardButton("📋 Pending Receipts", callback_data='pending_receipts')],
        [InlineKeyboardButton("💬 Support Tickets", callback_data='admin_tickets'),
         InlineKeyboardButton("📊 Sales Reports", callback_data='admin_reports')],
        [InlineKeyboardButton("🔐 Connection Status", callback_data='connection_status_menu'),
         InlineKeyboardButton("📂 Backup & Migration", callback_data='backup_menu')],
        [InlineKeyboardButton("⚙️ Bot Configs", callback_data='bot_config_menu')],
        [InlineKeyboardButton("📢 Notifications", callback_data='notification_menu')],
    ]
    
    # Only Super Admins see Admin Management
    if await is_super_admin(user_id):
        keyboard.append([InlineKeyboardButton("👮‍♂️ Admin Management", callback_data='admin_mgmt_menu')])
        
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    text = "👮‍♂️ **Admin Panel**\nSelect an action:"
    
    if update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    return ConversationHandler.END

# --- Server Management ---

async def list_servers(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all servers with status."""
    query = update.callback_query
    await query.answer()
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Server))
        servers = result.scalars().all()
        
    text = "📡 **Server List**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    
    if not servers:
        text += "No servers configured."
    
    for s in servers:
        status = "🟢 Active" if s.is_active else "🔴 Disabled"
        
        text += (
            f"**{s.name}** {status}\n"
            f"Host: `{s.host}:{s.port}`\n"
            f"-------------------\n"
        )
        
        keyboard.append([
            InlineKeyboardButton(f"🔄 Test", callback_data=f"server_test_{s.id}"),
            InlineKeyboardButton(f"✏️ Edit", callback_data=f"server_edit_{s.id}"),
            InlineKeyboardButton(f"🗑 Delete", callback_data=f"server_delete_{s.id}")
        ])
        
    keyboard.append([InlineKeyboardButton("➕ Add Server", callback_data='server_add')])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='admin_start')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def test_server_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Test connection to specific server."""
    query = update.callback_query
    server_id = int(query.data.split('_')[2])
    
    await query.answer("Testing connection...", cache_time=0)
    
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        
    if not server:
        await query.edit_message_text("❌ Server not found.")
        return
        
    start_time = datetime.now()
    try:
        mgr = get_mikrotik_manager(server)
        mgr.connect()
        
        # Fetch basic stats
        resource = mgr._get_resource('/system/resource')
        res_data = resource.get()[0]
        
        active_sessions = len(mgr._get_resource('/user-manager/session').get(active='true'))
        
        latency = (datetime.now() - start_time).microseconds / 1000
        
        mgr.close()
        
        info = (
            f"✅ **Connection Successful!**\n"
            f"Server: {server.name}\n"
            f"Latency: {latency:.1f}ms\n"
            f"Cpu: {res_data.get('cpu-load')}\u0025\n"
            f"Version: {res_data.get('version')}\n"
            f"Uptime: {res_data.get('uptime')}\n"
            f"Active Sessions: {active_sessions}"
        )
        
        keyboard = [[InlineKeyboardButton("🔙 Back to List", callback_data='list_servers')]]
        await query.edit_message_text(info, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Test failed for {server.name}: {e}")
        await query.edit_message_text(
            f"❌ **Connection Failed!**\n"
            f"Server: {server.name}\n"
            f"Error: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 Back", callback_data='list_servers')]])
        )


# --- User Search Workflow ---

async def search_user_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("🔍 Enter username to search:")
    return SEARCH_USERNAME

async def display_user_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    username = update.message.text.strip()
    
    async with AsyncSessionLocal() as session:
        # 1. Get Subscription from DB
        stmt = select(Subscription).where(Subscription.mikrotik_username == username)
        result = await session.execute(stmt)
        sub = result.scalars().first()
        
        if not sub:
            await update.message.reply_text(f"❌ User '{username}' not found in Database.")
            return SEARCH_USERNAME

        # 2. Get Live Info from MikroTik
        # Need server info to connect
        # stmt_server = select(Server).where(Server.id == sub.server_id)
        # server_res = await session.execute(stmt_server)
        # server = server_res.scalars().first()
        
        # For now, assume default server or stub
        mgr = get_mikrotik_manager() 
        mt_info = mgr.get_user_info(username)
        
        if not mt_info:
             await update.message.reply_text(
                 f"⚠️ User found in DB but NOT in MikroTik (Server: {mgr.host}).\n"
                 "They might have been deleted from the router."
             )
             # Still show DB info?
        
        # Merge Data
        context.user_data['current_sub_id'] = sub.id
        context.user_data['current_username'] = username
        
        db_expiry = sub.expiry_date.strftime('%Y-%m-%d') if sub.expiry_date else "N/A"
        mt_active = "🟢 Active" if mt_info and mt_info.get('status') == 'active' else "🔴 Offline/Disabled"
        
        # Calculate Usage
        total_gb = sub.total_limit_bytes / (1024**3) if sub.total_limit_bytes else 0
        used_gb = mt_info.get('used_bytes', 0) / (1024**3) if mt_info else 0
        usage_percent = (used_gb / total_gb * 100) if total_gb > 0 else 0
        progress_bar = "█" * int(usage_percent / 10) + "░" * (10 - int(usage_percent / 10))
        
        info_text = (
            f"👤 **User Information**\n"
            f"━━━━━━━━━━━━━━━━━━━━\n"
            f"Username: `{username}`\n"
            f"Password: ||{sub.mikrotik_password}||\n" # Spoiler logic if supported
            f"Status: {mt_active}\n\n"
            
            f"📊 **Usage**:\n"
            f"Data: {used_gb:.2f}GB / {total_gb:.0f}GB ({usage_percent:.1f}%)\n"
            f"[{progress_bar}]\n"
            f"Expires: {db_expiry}\n\n"
            
            f"🔌 **Connections**:\n"
            f"Active: {mt_info.get('connected_devices', 0) if mt_info else '?'}\n"
            f"IP: {mt_info.get('current_ip', 'N/A') if mt_info else 'N/A'}\n"
        )
        
        keyboard = [
            [InlineKeyboardButton("🔄 Reset Pass", callback_data=f"reset_pass_{username}"),
             InlineKeyboardButton("➕ Add Data", callback_data=f"add_data_{username}")],
            [InlineKeyboardButton("⏳ Extend Time", callback_data=f"extend_time_{username}"),
             InlineKeyboardButton("💰 Edit Balance", callback_data=f"edit_balance_{username}")],
            [InlineKeyboardButton("🚫 Disable", callback_data=f"disable_user_{username}"),
             InlineKeyboardButton("🗑 Delete", callback_data=f"delete_user_{username}")],
            [InlineKeyboardButton("🔙 Back", callback_data="admin_start")]
        ]
        
        await update.message.reply_text(info_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return USER_ACTION

async def reset_password_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    username = query.data.split('_')[2]
    context.user_data['target_user'] = username
    await query.edit_message_text(f"🔑 Enter new password for {username}:")
    return RESET_PASS

async def process_reset_password(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_pass = update.message.text.strip()
    username = context.user_data.get('target_user')
    
    mgr = get_mikrotik_manager()
    success = mgr.reset_password(username, new_pass)
    
    if success:
        # Update DB
        async with AsyncSessionLocal() as session:
            stmt = update(Subscription).where(Subscription.mikrotik_username == username).values(mikrotik_password=new_pass)
            await session.execute(stmt)
            await session.commit()
            
        await update.message.reply_text(f"✅ Password for `{username}` reset to `{new_pass}`.", parse_mode='Markdown')
    else:
        await update.message.reply_text("❌ Failed to reset password in MikroTik.")
        
    return USER_ACTION

async def add_data_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    username = query.data.split('_')[2]
    context.user_data['target_user'] = username
    await query.edit_message_text(f"➕ Enter GB to add for {username} (e.g., 5):")
    return ADD_DATA

async def process_add_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        gb = int(update.message.text.strip())
        username = context.user_data.get('target_user')
        
        mgr = get_mikrotik_manager()
        # Note: add_data_to_user in stub just returns True/log. 
        # Real impl needs to adjust DB total and MT queue.
        
        async with AsyncSessionLocal() as session:
            stmt = select(Subscription).where(Subscription.mikrotik_username == username)
            result = await session.execute(stmt)
            sub = result.scalars().first()
            if sub:
                sub.total_limit_bytes += gb * 1024**3
                await session.commit()
                
                # Update MT
                mgr.add_data_to_user(username, gb) # This needs to set NEW TOTAL in MT usually
                
                await update.message.reply_text(f"✅ Added {gb}GB to {username}.")
            else:
                await update.message.reply_text("❌ User not found in DB.")
                
    except ValueError:
        await update.message.reply_text("❌ Invalid number.")
        
    return USER_ACTION

async def extend_time_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    username = query.data.split('_')[2]
    context.user_data['target_user'] = username
    await query.edit_message_text(f"⏳ Enter days to extend for {username} (e.g., 30):")
    return EXTEND_TIME

async def process_extend_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        days = int(update.message.text.strip())
        username = context.user_data.get('target_user')
        
        async with AsyncSessionLocal() as session:
            stmt = select(Subscription).where(Subscription.mikrotik_username == username)
            result = await session.execute(stmt)
            sub = result.scalars().first()
            
            if sub:
                sub.expiry_date += timedelta(days=days)
                await session.commit()
                
                # Check if we need to re-enable in MT
                mgr = get_mikrotik_manager()
                mgr.extend_validity(username, days)
                
                new_date = sub.expiry_date.strftime('%Y-%m-%d')
                await update.message.reply_text(f"✅ Extended {username} by {days} days.\nNew Expiry: {new_date}")
            else:
                 await update.message.reply_text("❌ User not found in DB.")
                 
    except ValueError:
        await update.message.reply_text("❌ Invalid number.")
        
    return USER_ACTION

async def edit_balance_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    username = query.data.split('_')[2]
    context.user_data['target_user'] = username
    
    async with AsyncSessionLocal() as session:
        # Get current balance
        stmt = select(User).join(Subscription).where(Subscription.mikrotik_username == username)
        res = await session.execute(stmt)
        user = res.scalars().first()
        current_balance = user.wallet_balance if user else 0.0
    
    await query.edit_message_text(
        f"💰 **Edit Balance: {username}**\n\n"
        f"Current Balance: ${current_balance:.2f}\n\n"
        f"Enter new balance amount (e.g., 50.0):"
    )
    return EDIT_BALANCE

async def process_edit_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        new_balance = float(update.message.text.strip())
        username = context.user_data.get('target_user')
        
        async with AsyncSessionLocal() as session:
            # 1. Get user via subscription
            stmt = select(User).join(Subscription).where(Subscription.mikrotik_username == username)
            res = await session.execute(stmt)
            user = res.scalars().first()
            
            if user:
                old_balance = user.wallet_balance
                user.wallet_balance = new_balance
                
                # 2. Record Transaction
                from models import Transaction
                txn = Transaction(
                    user_id=user.id,
                    amount=new_balance - old_balance,
                    type='manual_adjustment',
                    description=f"Admin manual adjustment from ${old_balance:.2f} to ${new_balance:.2f}"
                )
                session.add(txn)
                await session.commit()
                
                await update.message.reply_text(f"✅ Balance for {username} updated to ${new_balance:.2f}.")
            else:
                await update.message.reply_text("❌ User not found.")
                
    except ValueError:
        await update.message.reply_text("❌ Invalid amount.")
        
    return USER_ACTION

async def disable_user_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    username = query.data.split('_')[2]
    
    mgr = get_mikrotik_manager()
    success = mgr.disable_user(username)
    
    if success:
        # Update DB status (optional, if we track 'banned' state)
        async with AsyncSessionLocal() as session:
             # Assuming we use a status field or similar, or just trust MT
             pass
             
        await query.answer("✅ User disabled & disconnected.", show_alert=True)
        # Refresh view?
        # For now just update text
        await query.edit_message_text(f"🚫 User {username} has been disabled.")
    else:
        await query.answer("❌ Failed to disable user.", show_alert=True)
        
    return USER_ACTION

# Delete Confirmation State
DELETE_CONFIRM = range(6, 7)

async def delete_user_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    username = query.data.split('_')[2]
    context.user_data['target_user'] = username
    
    await query.edit_message_text(
        f"⚠️ **PERMANENT DELETION** ⚠️\n\n"
        f"Are you sure you want to delete `{username}`?\n"
        f"This will remove them from Database and MikroTik.\n\n"
        f"Type `DELETE` to confirm:",
        parse_mode='Markdown'
    )
    return DELETE_CONFIRM

async def process_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    username = context.user_data.get('target_user')
    
    if text == 'DELETE':
        mgr = get_mikrotik_manager()
        mt_success = mgr.delete_user(username)
        
        if mt_success:
            # Delete from DB
            async with AsyncSessionLocal() as session:
                stmt = select(Subscription).where(Subscription.mikrotik_username == username)
                res = await session.execute(stmt)
                sub = res.scalars().first()
                if sub:
                    await session.delete(sub)
                    await session.commit()
            
            await update.message.reply_text(f"✅ User `{username}` deleted successfully.", parse_mode='Markdown')
        else:
             await update.message.reply_text("❌ Failed to delete from MikroTik (DB not touched).")
             
        return ConversationHandler.END # Exit convo
    else:
        await update.message.reply_text("❌ Delete cancelled (text did not match).")
        return USER_ACTION

# --- Receipt Approval Workflow ---

async def pending_receipts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List pending receipts."""
    query = update.callback_query
    await query.answer()
    
    async with AsyncSessionLocal() as session:
        # Fetch pending receipts
        stmt = select(PaymentReceipt, User).join(User, PaymentReceipt.user_id == User.id).where(PaymentReceipt.status == 'pending')
        result = await session.execute(stmt)
        receipts = result.all() # list of (PaymentReceipt, User)
        
    if not receipts:
        await query.edit_message_text("✅ No pending receipts.")
        return ConversationHandler.END

    text = "📋 **Pending Receipts**:\n"
    keyboard = []
    
    for r, u in receipts:
        text += f"ID: `{r.id}` - User: {u.full_name} (@{u.username}) - ${r.amount}\n"
        # Add buttons for each? Or a way to select?
        # Telegram limits buttons. Let's list basic info and provide buttons below for "Next Pending" or specific IDs.
        # Simple approach: Buttons for Approve/Reject ID
        keyboard.append([
            InlineKeyboardButton(f"✅ Approve #{r.id}", callback_data=f"receipt_approve_{r.id}"),
            InlineKeyboardButton(f"❌ Reject #{r.id}", callback_data=f"receipt_reject_{r.id}")
        ])
    
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='admin_start')])
    
    # Send photos? That's hard in one update.
    # Ideally: "receipt_view_{id}" -> Shows photo + buttons.
    # For now: Just listing.
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def handle_receipt_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    action, receipt_id = query.data.split('_')[1], int(query.data.split('_')[2])
    admin_id = update.effective_user.id
    
    # Get custom messages from admin settings
    from admin_settings import get_admin_setting
    approve_msg = await get_admin_setting('receipt_approve_msg', 
        "✅ Your receipt has been approved!\n\nYour wallet has been credited. Thank you for your payment.")
    reject_msg = await get_admin_setting('receipt_deny_msg',
        "❌ Your receipt was rejected.\n\nPlease contact support if you believe this is an error.")
    
    # Get receipt info for notification
    async with AsyncSessionLocal() as session:
        receipt = await session.get(PaymentReceipt, receipt_id)
        if not receipt:
            await query.edit_message_text(f"❌ Receipt #{receipt_id} not found.")
            return
        
        user = await session.get(DBUser, receipt.user_id)
        user_telegram_id = user.telegram_id if user else None
        receipt_amount = receipt.amount
        receipt_file_id = receipt.receipt_file_id
    
    if action == 'approve':
        success = await WalletManager.approve_receipt(receipt_id, admin_id)
        if success:
            # Try to delete the receipt image from admin's chat (if it was a file)
            try:
                # The message we're editing likely contains the receipt
                if query.message and query.message.photo:
                    await query.message.delete()
                elif query.message and query.message.document:
                    await query.message.delete()
                else:
                    await query.edit_message_text(f"✅ Receipt #{receipt_id} approved. Amount: ${receipt_amount:.2f}")
            except:
                await query.edit_message_text(f"✅ Receipt #{receipt_id} approved. Amount: ${receipt_amount:.2f}")
            
            # Notify user with custom message
            if user_telegram_id:
                try:
                    await context.bot.send_message(
                        user_telegram_id,
                        f"{approve_msg}\n\n"
                        f"💵 **Amount:** ${receipt_amount:.2f}\n"
                        f"📋 **Receipt ID:** `RCP-{receipt_id}`",
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Failed to notify user {user_telegram_id}: {e}")
        else:
            await query.edit_message_text(f"❌ Failed to approve receipt #{receipt_id}.")
            
    elif action == 'reject':
        success = await WalletManager.reject_receipt(receipt_id, admin_id)
        if success:
            await query.edit_message_text(f"❌ Receipt #{receipt_id} rejected.")
            
            # Notify user with custom message
            if user_telegram_id:
                try:
                    await context.bot.send_message(
                        user_telegram_id,
                        f"{reject_msg}\n\n"
                        f"📋 **Receipt ID:** `RCP-{receipt_id}`",
                        parse_mode='Markdown'
                    )
                except Exception as e:
                    logger.error(f"Failed to notify user {user_telegram_id}: {e}")
        else:
            await query.edit_message_text(f"❌ Failed to reject receipt #{receipt_id}.")
    
    # Re-show list?
    # await pending_receipts(update, context) 
    return ConversationHandler.END

# --- Settings & File Management ---

async def connection_status_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = "🔐 **Connection Status**\nSelect a configuration type:"
    
    keyboard = [
        [InlineKeyboardButton("📂 OVPN Files", callback_data='manage_ovpn')],
        [InlineKeyboardButton("🔐 L2TP / SSTP Credentials", callback_data='settings_connection')],
        [InlineKeyboardButton("🔙 Back", callback_data='admin_start')]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    # If using ConversationHandler, return appropriate state or END if new convo starter
    return ConversationHandler.END

async def manage_ovpn_files(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    # List current files
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(OvpnConfig))
        configs = result.scalars().all()
        
    text = "📁 **OVPN Configurations**\n\n"
    if not configs:
        text += "No files uploaded."
    else:
        for c in configs:
            text += f"📄 `{c.filename}` (Server: {c.server_id or 'All'})\n"
            
    text += "\n\nClick below to upload a new file."
    
    keyboard = [
        [InlineKeyboardButton("📤 Upload New File", callback_data='upload_ovpn')],
        [InlineKeyboardButton("🔙 Back", callback_data='connection_status_menu')]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def start_ovpn_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Ask admin to send file."""
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📤 Please send the **.ovpn** file now.\n\nType /cancel to abort.")
    return WAIT_OVPN_FILE

async def process_ovpn_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Check if document
    doc = update.message.document
    if not doc: # Should be guarded by filter
        await update.message.reply_text("❌ Please send a valid file.")
        return WAIT_OVPN_FILE
        
    filename = doc.file_name
    if not filename.endswith('.ovpn'):
        await update.message.reply_text("⚠️ Warning: File doesn't end with .ovpn. Saving anyway.")
        
    # Download
    f = await doc.get_file()
    content_byte = await f.download_as_bytearray()
    content_str = content_byte.decode('utf-8') # Assume utf-8
    
    # Save to DB
    async with AsyncSessionLocal() as session:
        # Check duplicate?
        # Just create new
        new_conf = OvpnConfig(
            server_id=None, # Default or prompt for server? Simplification: Global or Default
            filename=filename,
            config_content=content_str
        )
        session.add(new_conf)
        await session.commit()
        
    await update.message.reply_text(f"✅ Saved `{filename}` successfully!", parse_mode='Markdown')
    return ConversationHandler.END

async def cancel_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Action cancelled.")
    return ConversationHandler.END

# Admin Settings Handler (Separate conversation or manual?)
# For simplicity, let's attach 'upload_ovpn' flow to a new ConversationHandler or merge?
# Merging into admin_search is messy. Let's make a new one or use patterns.
# Let's define a new ConversationHandler for OVPN Upload.

admin_ovpn_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(connection_status_menu, pattern='^connection_status_menu$'),
        CallbackQueryHandler(manage_ovpn_files, pattern='^manage_ovpn$')
    ],
    states={
        ConversationHandler.TIMEOUT: [CallbackQueryHandler(admin_start, pattern='^admin_start$')],
        WAIT_OVPN_FILE: [MessageHandler(filters.Document.ALL, process_ovpn_upload)],
    },
    fallbacks=[
        CommandHandler('cancel', admin_start),
        CallbackQueryHandler(start_ovpn_upload, pattern='^upload_ovpn$'), # Entry to state
        CallbackQueryHandler(connection_status_menu, pattern='^connection_status_menu$'),
        CallbackQueryHandler(admin_start, pattern='^admin_start$')
    ],
    per_message=False
)

# Handler Definition
admin_search_user_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(search_user_start, pattern='^search_user$')],
    states={
        SEARCH_USERNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, display_user_info)],
        USER_ACTION: [
            CallbackQueryHandler(reset_password_flow, pattern='^reset_pass_'),
            CallbackQueryHandler(add_data_flow, pattern='^add_data_'),
            CallbackQueryHandler(extend_time_flow, pattern='^extend_time_'),
            CallbackQueryHandler(edit_balance_flow, pattern='^edit_balance_'),
            CallbackQueryHandler(disable_user_flow, pattern='^disable_user_'),
            CallbackQueryHandler(delete_user_flow, pattern='^delete_user_'),
            CallbackQueryHandler(admin_start, pattern='^admin_start$'),
        ],
        RESET_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_reset_password)],
        ADD_DATA: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_add_data)],
        EXTEND_TIME: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_extend_time)],
        EDIT_BALANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_edit_balance)],
        DELETE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, process_delete_confirm)]
    },
    fallbacks=[CommandHandler('cancel', admin_start), CallbackQueryHandler(admin_start, pattern='^admin_start$')]
)

# --- Profile Management ---

PROFILE_NAME, PROFILE_DATA, PROFILE_DAYS, PROFILE_PRICE, PROFILE_SERVER = range(7, 12)

async def list_profiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all profiles."""
    query = update.callback_query
    await query.answer()
    
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Profile).where(Profile.is_active == True))
        profiles = result.scalars().all()
        
        # Count active users per profile
        # Use simple count query
        # stmt = select(Subscription.profile_id, func.count(Subscription.id)).group_by(Subscription.profile_id)
        # This requires more complex query. Simple loop for now or optimized later.
        
    text = "📦 **Active Profiles**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    keyboard = []
    
    if not profiles:
        text += "No profiles found."
        
    for p in profiles:
        # Get user count (stubbed or separate query)
        user_count = "?" 
        text += (
            f"**{p.name}** (v{p.version})\n"
            f"💰 ${p.price:.2f} | 📊 {p.data_limit_gb}GB | ⏳ {p.validity_days}d\n"
            f"Users: {user_count}\n"
            f"-------------------\n"
        )
        # Edit/Delete buttons (Delete not implemented yet)
        # keyboard.append([InlineKeyboardButton(f"✏️ Edit {p.name}", callback_data=f"edit_profile_{p.id}")]) 

    keyboard.append([InlineKeyboardButton("➕ Add New Profile", callback_data='add_profile')])
    keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='admin_start')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def add_profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📦 Enter Profile Name (e.g., 'Gold Plan'):")
    return PROFILE_NAME

async def get_profile_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_profile_name'] = update.message.text.strip()
    await update.message.reply_text("📊 Enter Data Limit in GB (e.g., 20):")
    return PROFILE_DATA

async def get_profile_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        gb = int(update.message.text.strip())
        context.user_data['new_profile_data'] = gb
        await update.message.reply_text("⏳ Enter Validity in Days (e.g., 30):")
        return PROFILE_DAYS
    except ValueError:
        await update.message.reply_text("❌ Invalid number. Enter GB:")
        return PROFILE_DATA

async def get_profile_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        days = int(update.message.text.strip())
        context.user_data['new_profile_days'] = days
        await update.message.reply_text("💰 Enter Price in $ (e.g., 9.99):")
        return PROFILE_PRICE
    except ValueError:
         await update.message.reply_text("❌ Invalid number. Enter Days:")
         return PROFILE_DAYS

async def get_profile_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text.strip())
        context.user_data['new_profile_price'] = price
        
        async with AsyncSessionLocal() as session:
            res = await session.execute(select(Server))
            servers = res.scalars().all()
            
        if not servers:
            await update.message.reply_text("❌ No servers found. Please add a server first.")
            return ConversationHandler.END
            
        if len(servers) == 1:
            # Skip selection if only one server
            context.user_data['new_profile_server_id'] = servers[0].id
            return await save_profile_final(update, context)
            
        # Show selection keyboard
        text = "📡 **Select Server for this Profile:**"
        keyboard = [[InlineKeyboardButton(s.name, callback_data=f"prof_srv_{s.id}")] for s in servers]
        
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return PROFILE_SERVER
        
    except ValueError:
        await update.message.reply_text("❌ Invalid price.")
        return PROFILE_PRICE

async def select_profile_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    server_id = int(query.data.split('_')[2])
    context.user_data['new_profile_server_id'] = server_id
    
    return await save_profile_final(update, context)

async def save_profile_final(update: Update, context: ContextTypes.DEFAULT_TYPE):
    server_id = context.user_data['new_profile_server_id']
    price = context.user_data['new_profile_price']
    name = context.user_data['new_profile_name']
    gb = context.user_data['new_profile_data']
    days = context.user_data['new_profile_days']
    
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        
    if not server:
        msg = "❌ Server not found."
        if update.callback_query: await update.callback_query.edit_message_text(msg)
        else: await update.message.reply_text(msg)
        return ConversationHandler.END

    # Create in MikroTik
    mgr = get_mikrotik_manager(server)
    rate_limit = "10M/10M" 
    
    success = mgr.create_profile_with_limits(name, days, gb, rate_limit)
    
    if success:
        # Save to DB
        async with AsyncSessionLocal() as db:
            new_p = Profile(
                name=name,
                data_limit_gb=gb,
                validity_days=days,
                price=price,
                server_id=server.id,
                version=1
            )
            db.add(new_p)
            await db.commit()
        
        msg = f"✅ Profile `{name}` created successfully on server `{server.name}`!"
    else:
        msg = f"❌ Failed to create profile on server `{server.name}` (MikroTik error)."
        
    if update.callback_query:
        await update.callback_query.edit_message_text(msg, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg, parse_mode='Markdown')
        
    return ConversationHandler.END

admin_profile_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(add_profile_start, pattern='^add_profile$')],
    states={
        PROFILE_NAME: [MessageHandler(filters.TEXT, get_profile_name)],
        PROFILE_DATA: [MessageHandler(filters.TEXT, get_profile_data)],
        PROFILE_DAYS: [MessageHandler(filters.TEXT, get_profile_days)],
        PROFILE_PRICE: [MessageHandler(filters.TEXT, get_profile_price)],
        PROFILE_SERVER: [CallbackQueryHandler(select_profile_server, pattern='^prof_srv_')],
    },
    fallbacks=[CommandHandler('cancel', admin_start), CallbackQueryHandler(admin_start, pattern='^admin_start$')]
)

# --- Server Add/Edit Flows ---# --- Server Adding Flow ---
SERVER_NAME, SERVER_HOST, SERVER_USER, SERVER_PASS, SERVER_PORT = range(30, 35)

async def server_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text("📡 Enter Server Name (e.g., 'Germany-01'):")
    return SERVER_NAME

async def server_name_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_server_name'] = update.message.text.strip()
    await update.message.reply_text("🌐 Enter Server IP/Host:")
    return SERVER_HOST

async def server_host_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_server_host'] = update.message.text.strip()
    await update.message.reply_text("👤 Enter MikroTik Username:")
    return SERVER_USER

async def server_user_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_server_user'] = update.message.text.strip()
    await update.message.reply_text("🔑 Enter MikroTik Password:")
    return SERVER_PASS

async def server_pass_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['new_server_pass'] = update.message.text.strip()
    await update.message.reply_text("🔌 Enter Port (default 8728):")
    return SERVER_PORT

async def server_port_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        port = int(update.message.text.strip())
    except ValueError:
        port = 8728
    
    name = context.user_data['new_server_name']
    host = context.user_data['new_server_host']
    user = context.user_data['new_server_user']
    pw = context.user_data['new_server_pass']
    
    async with AsyncSessionLocal() as session:
        new_server = Server(name=name, host=host, username=user, password=pw, port=port)
        session.add(new_server)
        await session.commit()
    
    await update.message.reply_text(f"✅ Server `{name}` added successfully!", parse_mode='Markdown')
    return ConversationHandler.END

# Server Edit and Delete states
SERVER_EDIT_SELECT, SERVER_EDIT_VALUE = range(40, 42)
SERVER_DELETE_CONFIRM = 42

async def server_edit_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start editing a server."""
    query = update.callback_query
    await query.answer()
    
    server_id = int(query.data.split('_')[2])
    context.user_data['edit_server_id'] = server_id
    
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            await query.edit_message_text("❌ Server not found.")
            return ConversationHandler.END
        
        context.user_data['edit_server_name'] = server.name
    
    keyboard = [
        [InlineKeyboardButton("📝 Name", callback_data='edit_srv_name'),
         InlineKeyboardButton("🌐 Host", callback_data='edit_srv_host')],
        [InlineKeyboardButton("👤 Username", callback_data='edit_srv_user'),
         InlineKeyboardButton("🔑 Password", callback_data='edit_srv_pass')],
        [InlineKeyboardButton("🔌 Port", callback_data='edit_srv_port'),
         InlineKeyboardButton("⚡ Toggle Active", callback_data='edit_srv_toggle')],
        [InlineKeyboardButton("🔙 Back", callback_data='list_servers')]
    ]
    
    await query.edit_message_text(
        f"✏️ **Edit Server: {server.name}**\n\nSelect what to edit:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return SERVER_EDIT_SELECT

async def server_edit_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle field selection for editing."""
    query = update.callback_query
    await query.answer()
    
    field = query.data.replace('edit_srv_', '')
    server_id = context.user_data.get('edit_server_id')
    
    if field == 'toggle':
        # Toggle active status immediately
        async with AsyncSessionLocal() as session:
            server = await session.get(Server, server_id)
            if server:
                server.is_active = not server.is_active
                await session.commit()
                status = "enabled" if server.is_active else "disabled"
                await query.edit_message_text(f"✅ Server {status}.")
        return ConversationHandler.END
    
    context.user_data['edit_field'] = field
    
    prompts = {
        'name': "Enter new server name:",
        'host': "Enter new host/IP:",
        'user': "Enter new MikroTik username:",
        'pass': "Enter new MikroTik password:",
        'port': "Enter new port (default 8728):"
    }
    
    await query.edit_message_text(f"📝 {prompts.get(field, 'Enter new value:')}")
    return SERVER_EDIT_VALUE

async def server_edit_save(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Save the edited field."""
    value = update.message.text.strip()
    field = context.user_data.get('edit_field')
    server_id = context.user_data.get('edit_server_id')
    
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            await update.message.reply_text("❌ Server not found.")
            return ConversationHandler.END
        
        if field == 'name':
            server.name = value
        elif field == 'host':
            server.host = value
        elif field == 'user':
            server.username = value
        elif field == 'pass':
            server.password = value
        elif field == 'port':
            try:
                server.port = int(value)
            except ValueError:
                await update.message.reply_text("❌ Invalid port number.")
                return SERVER_EDIT_VALUE
        
        await session.commit()
    
    await update.message.reply_text(f"✅ Server updated successfully!")
    return ConversationHandler.END

async def server_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start server deletion with confirmation."""
    query = update.callback_query
    await query.answer()
    
    server_id = int(query.data.split('_')[2])
    context.user_data['delete_server_id'] = server_id
    
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if not server:
            await query.edit_message_text("❌ Server not found.")
            return ConversationHandler.END
        
        server_name = server.name
    
    await query.edit_message_text(
        f"⚠️ **Delete Server: {server_name}?**\n\n"
        "This will remove the server from the database.\n"
        "Associated profiles and subscriptions may be affected.\n\n"
        "Type `DELETE` to confirm:",
        parse_mode='Markdown'
    )
    return SERVER_DELETE_CONFIRM

async def server_delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and execute server deletion."""
    text = update.message.text.strip()
    server_id = context.user_data.get('delete_server_id')
    
    if text != 'DELETE':
        await update.message.reply_text("❌ Deletion cancelled.")
        return ConversationHandler.END
    
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, server_id)
        if server:
            server_name = server.name
            await session.delete(server)
            await session.commit()
            await update.message.reply_text(f"✅ Server `{server_name}` deleted.", parse_mode='Markdown')
        else:
            await update.message.reply_text("❌ Server not found.")
    
    return ConversationHandler.END

admin_server_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(server_add_start, pattern='^server_add$'),
        CallbackQueryHandler(server_edit_start, pattern='^server_edit_'),
        CallbackQueryHandler(server_delete_start, pattern='^server_delete_')
    ],
    states={
        SERVER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, server_name_received)],
        SERVER_HOST: [MessageHandler(filters.TEXT & ~filters.COMMAND, server_host_received)],
        SERVER_USER: [MessageHandler(filters.TEXT & ~filters.COMMAND, server_user_received)],
        SERVER_PASS: [MessageHandler(filters.TEXT & ~filters.COMMAND, server_pass_received)],
        SERVER_PORT: [MessageHandler(filters.TEXT & ~filters.COMMAND, server_port_received)],
        SERVER_EDIT_SELECT: [
            CallbackQueryHandler(server_edit_field, pattern='^edit_srv_'),
            CallbackQueryHandler(list_servers, pattern='^list_servers$')
        ],
        SERVER_EDIT_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, server_edit_save)],
        SERVER_DELETE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, server_delete_confirm)],
    },
    fallbacks=[CommandHandler('cancel', admin_start), CallbackQueryHandler(admin_start, pattern='^admin_start$')]
)

# --- Backup & Migration ---

async def backup_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    group_id = config.BACKUP_GROUP_ID or "Not Set"
    
    text = (
        "📂 **Backup & Migration**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        f"📍 **Auto-Backup Group:** `{group_id}`\n"
        "✨ *Automated backups run every 6 hours.*\n\n"
        "Select an action:"
    )
    
    keyboard = [
        [InlineKeyboardButton("📤 Export Manual Backup", callback_data='backup_export')],
        [InlineKeyboardButton("📥 Import Database (Migrate)", callback_data='backup_import')],
        [InlineKeyboardButton("🔙 Admin Panel", callback_data='admin_start')]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def manual_export(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer("Preparing backup...")
    
    from backup_manager import BackupManager
    mgr = BackupManager(context.bot)
    
    # Send to the admin who requested it
    await mgr.send_backup_to_telegram(update.effective_chat.id, is_auto=False)
    
    await query.message.reply_text("✅ Manual backup file sent to your private chat.")
    return ConversationHandler.END

async def start_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "⚠️ **DATABASE IMPORT (DANGER ZONE)** ⚠️\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Uploading a database will **REPLACE** all current data (users, servers, tokens).\n\n"
        "1. Current DB will be backed up as `.bak`\n"
        "2. New DB will be active immediately.\n\n"
        "👉 **Please send the `.db` file now.**\n\n"
        "Or type /cancel to abort."
    )
    
    await query.edit_message_text(text, parse_mode='Markdown')
    return WAIT_IMPORT_FILE

async def process_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    doc = update.message.document
    if not doc or not doc.file_name.endswith('.db'):
        await update.message.reply_text("❌ Please send a valid `.db` file.")
        return WAIT_IMPORT_FILE
    
    await update.message.reply_text("⏳ Processing database restoration...")
    
    # Download
    f = await doc.get_file()
    temp_path = f"temp_restore_{datetime.now().timestamp()}.db"
    await f.download_to_drive(temp_path)
    
    # Restore
    from backup_manager import BackupManager
    success = await BackupManager.restore_database(temp_path)
    
    if os.path.exists(temp_path):
        os.remove(temp_path)
        
    if success:
        await update.message.reply_text(
            "✅ **Database Restored Successfully!**\n\n"
            "The bot is now running with the new data.\n"
            "Please restart the bot service if you notice any issues.",
            parse_mode='Markdown'
        )
    else:
        await update.message.reply_text("❌ Restore failed. Reverting to backup.")
        
    return ConversationHandler.END

admin_backup_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(backup_menu, pattern='^backup_menu$'),
        CallbackQueryHandler(manual_export, pattern='^backup_export$'),
        CallbackQueryHandler(start_import, pattern='^backup_import$')
    ],
    states={
        WAIT_IMPORT_FILE: [MessageHandler(filters.Document.ALL, process_import)],
    },
    fallbacks=[
        CommandHandler('cancel', admin_start),
        CallbackQueryHandler(admin_start, pattern='^admin_start$')
    ]
)

# --- Admin Management ---

async def admin_mgmt_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    if not await is_super_admin(user_id):
        await query.edit_message_text("❌ Permission denied. Super Admin only.")
        return ConversationHandler.END
        
    admins = await list_admins()
    
    text = "👮‍♂️ **Admin Management**\n━━━━━━━━━━━━━━━━━━━━\n\n"
    text += f"🏠 **Super Admins (.env):**\n"
    for sa in config.ADMIN_IDS:
        text += f"- `{sa}` (Permanent)\n"
        
    text += "\n👥 **Database Admins:**\n"
    if not admins:
        text += "No additional admins found."
    else:
        for a in admins:
            text += f"- `{a.telegram_id}` (@{a.username or 'N/A'})\n"
            
    keyboard = [
        [InlineKeyboardButton("➕ Add Admin", callback_data='admin_add_start')],
        [InlineKeyboardButton("➖ Remove Admin", callback_data='admin_remove_start')],
        [InlineKeyboardButton("🔙 Admin Panel", callback_data='admin_start')]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def admin_add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📝 **Add New Admin**\n\n"
        "Please send the numerical **Telegram ID** of the user.\n"
        "Example: `12345678`"
    )
    context.user_data['admin_mgmt_action'] = 'add'
    return ADMIN_MGMT_ID

async def admin_remove_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📝 **Remove Admin**\n\n"
        "Please send the numerical **Telegram ID** of the admin you want to remove."
    )
    context.user_data['admin_mgmt_action'] = 'remove'
    return ADMIN_MGMT_ID

async def admin_mgmt_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    action = context.user_data.get('admin_mgmt_action')
    try:
        target_id = int(update.message.text.strip())
    except ValueError:
        await update.message.reply_text("❌ Invalid ID. Please send a numerical ID.")
        return ADMIN_MGMT_ID
        
    if action == 'add':
        success = await add_admin(target_id, added_by=update.effective_user.id)
        msg = f"✅ Admin `{target_id}` added." if success else "❌ Failed (Already an admin?)."
    else:
        if target_id in config.ADMIN_IDS:
            msg = "❌ Cannot remove Super Admin from DB."
        else:
            success = await remove_admin(target_id)
            msg = f"✅ Admin `{target_id}` removed." if success else "❌ Error removing admin."
            
    keyboard = [[InlineKeyboardButton("🔙 Admin Management", callback_data='admin_mgmt_menu')]]
    await update.message.reply_text(msg, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

admin_mgmt_handler = ConversationHandler(
    entry_points=[CallbackQueryHandler(admin_mgmt_menu, pattern='^admin_mgmt_menu$')],
    states={
        ADMIN_MGMT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_mgmt_received)],
    },
    fallbacks=[
        CommandHandler('cancel', admin_start),
        CallbackQueryHandler(admin_start, pattern='^admin_start$')
    ]
)

# --- Notification System ---

async def notification_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    text = (
        "📢 **Notification Center**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "Select the type of notification you want to send:"
    )
    
    keyboard = [
        [InlineKeyboardButton("📣 Broadcast to All Users", callback_data='notify_broadcast')],
        [InlineKeyboardButton("🎯 Send to Specific User ID", callback_data='notify_targeted')],
        [InlineKeyboardButton("🔙 Admin Panel", callback_data='admin_start')]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def broadcast_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "📝 **Send Broadcast**\n\n"
        "Please send the message you want to broadcast to **ALL active users**.\n\n"
        "Use Markdown for formatting. Type /cancel to abort."
    )
    return BROADCAST_MSG

async def targeted_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    await query.edit_message_text(
        "🎯 **Targeted Message**\n\n"
        "Please enter the **Telegram User ID** of the recipient.\n\n"
        "Type /cancel to abort."
    )
    return TARGETED_USER_ID

async def receive_targeted_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    target_id = update.message.text.strip()
    if not target_id.isdigit():
        await update.message.reply_text("❌ Invalid ID. Please enter a numerical Telegram ID.")
        return TARGETED_USER_ID
        
    context.user_data['notify_target_id'] = int(target_id)
    await update.message.reply_text(f"✅ Recipient ID: `{target_id}`\n\nNow send the message text:")
    return TARGETED_MSG

async def process_notification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_text = update.message.text
    is_broadcast = context.user_data.get('notify_target_id') is None
    
    status_msg = await update.message.reply_text("⏳ Processing notification...")
    
    from notification_manager import NotificationManager
    mgr = NotificationManager(context.bot)
    
    if is_broadcast:
        stats = await mgr.broadcast_to_all(msg_text)
        result_text = (
            "✅ **Broadcast Complete!**\n\n"
            f"📊 **Stats:**\n"
            f"- Total: {stats['total']}\n"
            f"- Success: {stats['success']}\n"
            f"- Blocked: {stats['blocked']}\n"
            f"- Failed: {stats['failed']}"
        )
    else:
        target_id = context.user_data.pop('notify_target_id')
        success = await mgr.send_to_user(target_id, msg_text)
        result_text = f"✅ Message sent to `{target_id}`." if success else f"❌ Failed to send to `{target_id}`."
        
    await status_msg.edit_text(result_text, parse_mode='Markdown')
    from admin_panel import admin_start
    await admin_start(update, context)
    return ConversationHandler.END

admin_notification_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(notification_menu, pattern='^notification_menu$'),
        CallbackQueryHandler(broadcast_start, pattern='^notify_broadcast$'),
        CallbackQueryHandler(targeted_start, pattern='^notify_targeted$')
    ],
    states={
        BROADCAST_MSG: [MessageHandler(filters.TEXT & (~filters.COMMAND), process_notification)],
        TARGETED_USER_ID: [MessageHandler(filters.TEXT & (~filters.COMMAND), receive_targeted_id)],
        TARGETED_MSG: [MessageHandler(filters.TEXT & (~filters.COMMAND), process_notification)]
    },
    fallbacks=[
        CommandHandler('cancel', admin_start),
        CallbackQueryHandler(admin_start, pattern='^admin_start$')
    ]
)
