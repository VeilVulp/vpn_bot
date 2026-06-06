import logging
import asyncio
from telegram import Update
from telegram.ext import ApplicationBuilder
from vpn_bot.config import config
from vpn_bot.database import init_db

# Setup logger
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger("vpn_bot")
from vpn_bot.backup_manager import BackupManager
from vpn_bot.handler_registry import register_all_handlers

async def post_init(application):
    """Start background tasks."""
    from vpn_bot.utils import LanguageManager
    await LanguageManager.refresh_language()

    from vpn_bot.admin_management import ensure_admin_secret_keyword
    await ensure_admin_secret_keyword()

    if config.SENTRY_DSN:
        try:
            import sentry_sdk
            sentry_sdk.init(dsn=config.SENTRY_DSN, traces_sample_rate=0.1)
            logger.info("Sentry error monitoring enabled.")
        except Exception as exc:
            logger.warning("Sentry init failed: %s", exc)

    from vpn_bot.server_secrets import validate_all_server_passwords

    bad_servers = await validate_all_server_passwords()
    if bad_servers:
        logger.error(
            "Cannot decrypt MikroTik password for %s server(s): %s. "
            "Run: python scripts/fix_server_encryption.py (loads .env + optional .env.test)",
            len(bad_servers),
            ", ".join(f"{s['name']}@{s['host']}" for s in bad_servers),
        )
    else:
        logger.info("Server credential encryption check passed.")

    mgr = BackupManager(application.bot)
    asyncio.create_task(mgr.run_periodic_backup())
    logger.info("Backup background task started.")
    
    from vpn_bot.sync_manager import SyncManager
    asyncio.create_task(SyncManager.run_periodic_sync())
    logger.info("Synchronization background task started.")

    from vpn_bot.admin_cleanup import run_periodic_cleanup
    asyncio.create_task(run_periodic_cleanup(application.bot))
    logger.info("Periodic cleanup background task started.")

async def error_handler(update: object, context) -> None:
    """Log the error and inform the user if possible."""
    from telegram.error import BadRequest, Forbidden, NetworkError, TimedOut, Conflict

    err = context.error
    if isinstance(err, Forbidden):
        uid = chat_id = None
        if isinstance(update, Update):
            if update.effective_user:
                uid = update.effective_user.id
            if update.effective_chat:
                chat_id = update.effective_chat.id
        logger.warning(
            "Telegram Forbidden (user blocked the bot or chat unavailable): user_id=%s chat_id=%s",
            uid,
            chat_id,
        )
        return

    if isinstance(err, BadRequest) and (
        "message text is empty" in str(err).lower()
        or "message_too_long" in str(err).lower()
        or "message is too long" in str(err).lower()
    ):
        detail = ""
        if isinstance(update, Update):
            if update.callback_query:
                detail = f" callback={update.callback_query.data!r}"
            elif update.message and update.message.text is not None:
                detail = f" message_text={update.message.text!r}"
        logger.warning("Telegram BadRequest (empty message text): %s%s", err, detail)
        return

    if isinstance(err, BadRequest) and "parse entities" in str(err).lower():
        detail = ""
        if isinstance(update, Update):
            if update.callback_query:
                detail = f" callback={update.callback_query.data!r}"
            elif update.message and update.message.text is not None:
                detail = f" message_text={update.message.text!r}"
        logger.warning("Telegram BadRequest (markdown parse entities): %s%s", err, detail)
        return

    # Check for common network errors we want to log more cleanly
    if isinstance(err, (NetworkError, TimedOut)):
        logger.warning(f"Network glitch occurred: {err}. Bot will retry automatically.")
        return
    
    if isinstance(context.error, Conflict):
        logger.error("Conflict error: Another bot instance is running with the same token!")
        return

    # For other errors, log full traceback
    logger.error("Exception while handling an update:", exc_info=context.error)
    if config.SENTRY_DSN:
        try:
            import sentry_sdk
            sentry_sdk.capture_exception(context.error)
        except Exception:
            pass

def main():
    if not config.BOT_TOKEN:
        logger.error("BOT_TOKEN is not set in .env")
        return

    logger.info("Starting VPN Bot...")
    
    # Initialize LanguageManager
    from vpn_bot.utils import LanguageManager
    LanguageManager.load_locales()
    
    # Initialize DB
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(init_db())
    
    from telegram.request import HTTPXRequest
    # Increase connect timeout to 30s to handle slow network/proxies
    request = HTTPXRequest(connect_timeout=30.0, read_timeout=30.0)
    
    app_builder = ApplicationBuilder().token(config.BOT_TOKEN).post_init(post_init).request(request)
    if config.PROXY_URL:
        # Re-initialize request with proxy if provided
        request = HTTPXRequest(proxy=config.PROXY_URL, connect_timeout=30.0, read_timeout=30.0)
        app_builder.request(request)
        logger.info(f"Using proxy: {config.PROXY_URL}")
        
    app = app_builder.build()
    app.add_error_handler(error_handler)
    register_all_handlers(app, include_user_handlers=True)

    logger.info("Bot is polling...")
    app.run_polling()

if __name__ == '__main__':
    main()
