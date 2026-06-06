"""Global user-facing middleware (ban gate, etc.)."""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import ContextTypes

logger = logging.getLogger("vpn_bot.middleware")

# Callback prefixes that remain allowed for banned users (read-only / exit).
_BANNED_CALLBACK_ALLOW = frozenset(
    {
        "main_menu",
        "terms_decline",
    }
)

# Text matching main-menu back is handled separately via LanguageManager in bot_handler.


async def global_banned_user_gate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Block banned users before other handlers (group -6, user flows only).

    Admins and group chats are not checked here — admin RBAC uses its own gate.
    """
    if not update or not update.effective_user:
        return

    from vpn_bot.admin_management import is_user_admin

    if await is_user_admin(update.effective_user.id):
        return

    chat = update.effective_chat
    if chat and chat.type in ("group", "supergroup", "channel"):
        return

    query = update.callback_query
    if query and query.data:
        data = query.data
        if data in _BANNED_CALLBACK_ALLOW or data.startswith("terms_decline"):
            return

    from vpn_bot.bot_handler import block_if_current_user_banned

    if await block_if_current_user_banned(update):
        from telegram.ext import ApplicationHandlerStop

        raise ApplicationHandlerStop
