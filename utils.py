import logging
import sys
from logging.handlers import RotatingFileHandler

def setup_logger(name: str = "vpn_bot", log_file: str = "bot.log", level: int = logging.INFO) -> logging.Logger:
    """
    Setup a comprehensive logger with console and file output.
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Formatter
    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    # File Handler
    file_handler = RotatingFileHandler(
        log_file, maxBytes=10*1024*1024, backupCount=5
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    return logger

# Global logger instance
logger = setup_logger()

# --- Security: Rate Limiting ---
from functools import wraps
import time

user_last_action = {}

def rate_limit(seconds=2):
    def decorator(func):
        @wraps(func)
        async def wrapper(update, context, *args, **kwargs):
            user_id = update.effective_user.id
            current_time = time.time()
            if user_id in user_last_action:
                elapsed = current_time - user_last_action[user_id]
                if elapsed < seconds:
                    if update.callback_query:
                        await update.callback_query.answer("⚠️ Too fast! Wait a moment.", show_alert=True)
                    else:
                        await update.message.reply_text("⚠️ Please wait a moment before trying again.")
                    return
            user_last_action[user_id] = current_time
            return await func(update, context, *args, **kwargs)
        return wrapper
    return decorator

# --- Security: Input Validation ---
import re

def validate_amount(amount_str: str) -> float:
    try:
        val = float(amount_str)
        if 5.0 <= val <= 1000.0:
            return val
        return 0.0
    except ValueError:
        return 0.0

def sanitize_username(username: str) -> str:
    # Allow a-z, 0-9, and _
    return re.sub(r'[^a-zA-Z0-9_]', '', username)
