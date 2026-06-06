"""Purchase/service terms acceptance gate before buy and renewal flows."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, update
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from vpn_bot.admin_settings_service import (
    PURCHASE_TERMS_MODE_EVERY,
    PURCHASE_TERMS_MODE_ONCE,
    get_purchase_terms_mode,
    get_purchase_terms_text,
    get_purchase_terms_version,
    is_purchase_terms_enabled,
)
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import User
from vpn_bot.utils import LanguageManager

TERMS_ACCEPT = 45
TERMS_SESSION_KEY = "terms_accepted_session"
TERMS_RESUME_KEY = "terms_resume"
TERMS_RECENT_MINUTES = 60
TELEGRAM_MESSAGE_SAFE_MAX = 4000

TERMS_ACCEPT_CALLBACK = "terms_accept"
TERMS_DECLINE_CALLBACK = "terms_decline"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _is_recent_acceptance(accepted_at: datetime | None) -> bool:
    if not accepted_at:
        return False
    if accepted_at.tzinfo is None:
        accepted_at = accepted_at.replace(tzinfo=timezone.utc)
    return (_utcnow() - accepted_at) <= timedelta(minutes=TERMS_RECENT_MINUTES)


async def user_needs_terms(user: User | None, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """Return True if the user must see the terms prompt before continuing."""
    if not await is_purchase_terms_enabled():
        return False
    if not user:
        return True

    mode = await get_purchase_terms_mode()
    if mode == PURCHASE_TERMS_MODE_EVERY:
        if context.user_data.get(TERMS_SESSION_KEY):
            return False
        return True

    current_version = await get_purchase_terms_version()
    if not user.purchase_terms_accepted_at:
        return True
    if str(user.purchase_terms_version or "") != current_version:
        return True
    return False


def _split_terms_chunks(body: str) -> list[str]:
    title = LanguageManager.get("buy.terms_title")
    header = f"{title}\n\n"
    max_body = TELEGRAM_MESSAGE_SAFE_MAX - len(header) - 50
    if len(body) <= max_body:
        return [header + body]

    chunks: list[str] = []
    remaining = body
    first = True
    while remaining:
        piece = remaining[:max_body]
        if len(remaining) > max_body:
            cut = piece.rfind("\n")
            if cut > max_body // 2:
                piece = remaining[:cut]
        prefix = header if first else ""
        chunks.append(prefix + piece)
        remaining = remaining[len(piece):].lstrip("\n")
        first = False
    return chunks


def _terms_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LanguageManager.get("buy.terms_accept_btn"),
                    callback_data=TERMS_ACCEPT_CALLBACK,
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("buy.terms_decline_btn"),
                    callback_data=TERMS_DECLINE_CALLBACK,
                )
            ],
        ]
    )


async def show_terms_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    resume: dict,
) -> int:
    """Display terms text and store resume action for after acceptance."""
    context.user_data[TERMS_RESUME_KEY] = resume
    body = await get_purchase_terms_text()
    chunks = _split_terms_chunks(body)
    keyboard = _terms_keyboard()
    query = update.callback_query

    if query:
        await query.answer()
        await query.edit_message_text(chunks[0], reply_markup=keyboard, parse_mode="Markdown")
        for extra in chunks[1:]:
            await query.message.reply_text(extra, parse_mode="Markdown")
    else:
        msg = update.message
        if msg:
            await msg.reply_text(chunks[0], reply_markup=keyboard, parse_mode="Markdown")
            for extra in chunks[1:]:
                await msg.reply_text(extra, parse_mode="Markdown")

    return TERMS_ACCEPT


async def record_terms_acceptance(telegram_id: int) -> None:
    version = await get_purchase_terms_version()
    now = _utcnow()
    async with AsyncSessionLocal() as session:
        await session.execute(
            update(User)
            .where(User.telegram_id == telegram_id)
            .values(
                purchase_terms_accepted_at=now,
                purchase_terms_version=version,
            )
        )
        await session.commit()


async def assert_terms_accepted_for_checkout(user: User | None) -> tuple[bool, str | None]:
    """Backend gate: returns (ok, error_message)."""
    if not await is_purchase_terms_enabled():
        return True, None
    if not user:
        return False, LanguageManager.get("buy.terms_required")

    mode = await get_purchase_terms_mode()
    current_version = await get_purchase_terms_version()

    if mode == PURCHASE_TERMS_MODE_ONCE:
        if not user.purchase_terms_accepted_at:
            return False, LanguageManager.get("buy.terms_required")
        if str(user.purchase_terms_version or "") != current_version:
            return False, LanguageManager.get("buy.terms_required")
        return True, None

    if not _is_recent_acceptance(user.purchase_terms_accepted_at):
        return False, LanguageManager.get("buy.terms_required")
    return True, None


async def handle_terms_accept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Record acceptance and resume the interrupted flow."""
    query = update.callback_query
    if query:
        await query.answer()

    telegram_id = update.effective_user.id
    await record_terms_acceptance(telegram_id)
    context.user_data[TERMS_SESSION_KEY] = True

    return await resume_after_terms_accept(update, context)


async def handle_terms_decline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel terms flow and return to main menu."""
    query = update.callback_query
    if query:
        await query.answer()

    context.user_data.pop(TERMS_RESUME_KEY, None)
    context.user_data.pop(TERMS_SESSION_KEY, None)

    from vpn_bot.bot_handler import start

    await start(update, context)
    return ConversationHandler.END


async def resume_after_terms_accept(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispatch to the appropriate handler based on stored resume action."""
    resume = context.user_data.pop(TERMS_RESUME_KEY, None) or {}
    flow_type = resume.get("type", "")

    if flow_type == "buy_ovpn":
        from vpn_bot.bot_handler import buy_service

        context.user_data["_from_terms_resume"] = True
        return await buy_service(update, context)

    if flow_type == "buy_wg":
        from vpn_bot.bot_handler import buy_wg_service

        context.user_data["_from_terms_resume"] = True
        return await buy_wg_service(update, context)

    if flow_type == "buy_ovpn_confirm":
        from vpn_bot.bot_handler import confirm_purchase

        context.user_data["_from_terms_resume"] = True
        return await confirm_purchase(update, context, profile_id=resume.get("profile_id"))

    if flow_type == "buy_wg_confirm":
        from vpn_bot.bot_handler import confirm_purchase_wg

        context.user_data["_from_terms_resume"] = True
        return await confirm_purchase_wg(update, context, plan_id=resume.get("profile_id"))

    if flow_type == "renew_ovpn":
        from vpn_bot.user_features import show_renew_ovpn_confirm

        sub_id = resume.get("sub_id")
        if sub_id is None:
            return ConversationHandler.END
        context.user_data["_from_terms_resume"] = True
        context.user_data["_renew_sub_id"] = sub_id
        context.user_data["_coupon_scope"] = "renew_ovpn"
        context.user_data["_coupon_resume"] = "renew_ovpn_confirm"
        return await show_renew_ovpn_confirm(update, context, int(sub_id))

    if flow_type == "renew_wg":
        from vpn_bot.user_features import show_renew_wg_confirm

        sub_id = resume.get("sub_id")
        if sub_id is None:
            return ConversationHandler.END
        context.user_data["_from_terms_resume"] = True
        context.user_data["_renew_sub_id"] = sub_id
        context.user_data["_coupon_scope"] = "renew_wg"
        context.user_data["_coupon_resume"] = "renew_wg_confirm"
        return await show_renew_wg_confirm(update, context, int(sub_id))

    return ConversationHandler.END


async def maybe_gate_terms(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    user: User | None,
    resume: dict,
):
    """If terms are required, show prompt; otherwise return None to proceed."""
    if await user_needs_terms(user, context):
        return await show_terms_prompt(update, context, resume)
    return None


async def fetch_user_by_telegram(telegram_id: int) -> User | None:
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.telegram_id == telegram_id))
        return res.scalars().first()
