import logging
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from config import config
from database import init_db

# Setup logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("vpn_bot")
from admin_panel import (
    admin_start, 
    admin_search_user_handler, 
    admin_ovpn_handler,
    admin_profile_handler,
    admin_server_handler,
    list_servers,
    test_server_connection,
    pending_receipts,
    handle_receipt_action,
    list_profiles,
    admin_backup_handler,
    admin_mgmt_handler,
    admin_notification_handler
)
from admin_settings import admin_settings_handler as admin_settings_conv_handler
from support_tickets import support_ticket_handler
from admin_tickets import admin_ticket_handler
from bot_handler import start, wallet_handler, buy_handler, main_menu_callback, wallet_menu, my_subscriptions, buy_service
from backup_manager import BackupManager

async def post_init(application):
    """Start background tasks."""
    mgr = BackupManager(application.bot)
    asyncio.create_task(mgr.run_periodic_backup())
    logger.info("Backup background task started.")

def main():
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN is not set in .env")
        return

    logger.info("Starting VPN Bot...")
    
    # Initialize DB
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_db())
    
    app = ApplicationBuilder().token(config.BOT_TOKEN).post_init(post_init).build()
    
    # User Handlers
    app.add_handler(CommandHandler("start", start))
    # wallet/buy handlers moved to High Priority block below
    
    # Admin Conversation Handlers
    app.add_handler(admin_search_user_handler)
    app.add_handler(admin_ovpn_handler)
    app.add_handler(admin_profile_handler)
    app.add_handler(admin_server_handler)         # Server management
    app.add_handler(admin_settings_conv_handler)  # New settings management
    app.add_handler(admin_backup_handler)         # Backup & Migration
    app.add_handler(admin_mgmt_handler)           # Admin management
    app.add_handler(admin_notification_handler)   # Notifications
    
    # Admin Command Handlers
    app.add_handler(CommandHandler("admin", admin_start))
    
    # Admin Callback Handlers (for non-conversation actions)
    app.add_handler(CallbackQueryHandler(list_servers, pattern='^list_servers$'))
    app.add_handler(CallbackQueryHandler(test_server_connection, pattern='^server_test_'))
    app.add_handler(CallbackQueryHandler(list_profiles, pattern='^list_profiles$'))
    app.add_handler(CallbackQueryHandler(pending_receipts, pattern='^pending_receipts$'))
    app.add_handler(CallbackQueryHandler(handle_receipt_action, pattern='^receipt_(approve|reject)_'))
    
    # Toolbar text button handler (for Reply Keyboard)
    # DEPRECATED: toolbar_handler removed.
    # Handlers are now registered as entry_points in their respective modules or below.

    from user_features import tutorials_menu, purchase_history
    from bot_handler import my_subscriptions, wallet_handler, buy_handler
    from support_tickets import support_ticket_handler
    from admin_tickets import admin_ticket_handler

    # Conversation Handlers (High Priority)
    app.add_handler(wallet_handler)
    app.add_handler(buy_handler)
    app.add_handler(support_ticket_handler)
    app.add_handler(admin_ticket_handler)

    # Standalone Message Handlers for Menu Buttons (Non-Conversation)
    app.add_handler(MessageHandler(filters.Regex('^📱 My Subscriptions$'), my_subscriptions))
    app.add_handler(MessageHandler(filters.Regex('^📚 Tutorials$'), tutorials_menu))
    app.add_handler(MessageHandler(filters.Regex('^📜 History$'), purchase_history))
    
    # Settings Handler (Admin Only)
    async def settings_handler_proxy(update: Update, context):
        if update.effective_user.id in config.ADMIN_IDS:
             from admin_panel import admin_start
             await admin_start(update, context)
        else:
             # Ignore or mention it's secured
             pass

    app.add_handler(MessageHandler(filters.Regex('^⚙️ Settings$'), settings_handler_proxy))
    
    # Secret Keyword Listener
    from admin_management import secret_keyword_listener
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), secret_keyword_listener), group=1)
    
    # Generic callback handler for main menu actions (must be last)
    app.add_handler(CallbackQueryHandler(main_menu_callback))
    
    logger.info("Bot is polling...")
    app.run_polling()

if __name__ == '__main__':
    main()
