"""
Shared cancel/skip UX for multi-step Telegram conversations.

Prefer inline buttons (conv_cancel / conv_skip) over /cancel and /skip commands.
"""

from __future__ import annotations

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackQueryHandler, CommandHandler, ContextTypes

from vpn_bot.utils import LanguageManager

CANCEL_CALLBACK = "conv_cancel"
SKIP_CALLBACK = "conv_skip"


def cancel_button_row() -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            LanguageManager.get("common.cancel_operation"),
            callback_data=CANCEL_CALLBACK,
        )
    ]


def skip_button_row() -> list[InlineKeyboardButton]:
    return [
        InlineKeyboardButton(
            LanguageManager.get("common.skip_step"),
            callback_data=SKIP_CALLBACK,
        )
    ]


def conv_markup(*, with_skip: bool = False) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if with_skip:
        rows.append(skip_button_row())
    rows.append(cancel_button_row())
    return InlineKeyboardMarkup(rows)


def merge_markup(
    base: InlineKeyboardMarkup | None,
    *,
    with_cancel: bool = False,
    with_skip: bool = False,
) -> InlineKeyboardMarkup | None:
    if not with_cancel and not with_skip:
        return base
    extra_rows: list[list[InlineKeyboardButton]] = []
    if with_skip:
        extra_rows.append(skip_button_row())
    if with_cancel:
        extra_rows.append(cancel_button_row())
    if base and base.inline_keyboard:
        return InlineKeyboardMarkup([*base.inline_keyboard, *extra_rows])
    return InlineKeyboardMarkup(extra_rows)


def append_conv_footer(text: str, *, with_skip: bool = False) -> str:
    key = "common.conv_footer_cancel_skip" if with_skip else "common.conv_footer_cancel"
    footer = LanguageManager.get(key)
    if footer and footer not in text:
        return f"{text}{footer}"
    return text


def is_conv_cancel(update: Update) -> bool:
    if update.callback_query and update.callback_query.data == CANCEL_CALLBACK:
        return True
    msg = update.message
    if not msg or not msg.text:
        return False
    t = msg.text.strip()
    if t == "/cancel":
        return True
    try:
        if t in LanguageManager.get_all_translations_raw("common.cancel_operation"):
            return True
    except Exception:
        pass
    return False


def is_conv_skip(update: Update) -> bool:
    if update.callback_query and update.callback_query.data == SKIP_CALLBACK:
        return True
    msg = update.message
    if not msg or not msg.text:
        return False
    t = msg.text.strip()
    if t == "/skip":
        return True
    try:
        if t in LanguageManager.get_all_translations_raw("common.skip_step"):
            return True
    except Exception:
        pass
    return False


def conv_cancel_handler(cancel_fn):
    """CallbackQueryHandler factory for cancel button."""

    async def _on_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query:
            await update.callback_query.answer()
        return await cancel_fn(update, context)

    return CallbackQueryHandler(_on_cancel, pattern=f"^{CANCEL_CALLBACK}$")


def conv_skip_handler(skip_fn):
    """CallbackQueryHandler factory for skip button."""

    async def _on_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.callback_query:
            await update.callback_query.answer()
        return await skip_fn(update, context)

    return CallbackQueryHandler(_on_skip, pattern=f"^{SKIP_CALLBACK}$")


def conv_control_handlers(cancel_fn, *, skip_fn=None) -> list:
    handlers = [conv_cancel_handler(cancel_fn)]
    if skip_fn:
        handlers.append(conv_skip_handler(skip_fn))
    return handlers


def legacy_cancel_handlers(cancel_fn) -> list:
    """Keep /cancel working alongside the inline button."""
    return [CommandHandler("cancel", cancel_fn), conv_cancel_handler(cancel_fn)]


async def reply_conv_prompt(
    update: Update,
    text: str,
    *,
    with_skip: bool = False,
    parse_mode: str = "Markdown",
    reply_markup: InlineKeyboardMarkup | None = None,
) -> None:
    """Send/edit a prompt with footer hint and cancel/skip buttons."""
    from vpn_bot.utils import ensure_telegram_text

    body = ensure_telegram_text(append_conv_footer(text, with_skip=with_skip))
    markup = merge_markup(reply_markup, with_cancel=True, with_skip=with_skip)
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.edit_message_text(body, reply_markup=markup, parse_mode=parse_mode)
    elif update.message:
        await update.message.reply_text(body, reply_markup=markup, parse_mode=parse_mode)
