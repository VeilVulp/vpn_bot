"""User-facing coupon prompt and session helpers."""

from __future__ import annotations

from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes, ConversationHandler

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.discount_service import (
    active_coupon_from_session,
    check_preview_rate_limit,
    record_preview_failure,
    store_active_coupon,
    validate_coupon_eligibility,
)
from vpn_bot.utils import LanguageManager, send_localized_text

COUPON_PROMPT = 46
COUPON_ENTRY = 47

SCOPE_TO_CONTEXT = {
    "buy_ovpn": "purchase_ovpn",
    "buy_wg": "purchase_wg",
    "renew_ovpn": "renew_ovpn",
    "renew_wg": "renew_wg",
}

INLINE_COUPON_CALLBACKS = {
    "coupon_enter_inline_buy_ovpn": ("buy_ovpn", "buy_service_plans"),
    "coupon_enter_inline_buy_wg": ("buy_wg", "buy_wg_plans"),
    "coupon_enter_inline_renew_ovpn": ("renew_ovpn", "renew_ovpn_confirm"),
    "coupon_enter_inline_renew_wg": ("renew_wg", "renew_wg_confirm"),
}


def clear_active_coupon(user_data: dict) -> None:
    """Drop applied coupon session data; keeps flow scope/resume intact."""
    user_data.pop("active_coupon", None)
    user_data.pop("pending_coupon_id", None)
    user_data.pop("_coupon_waiting_code", None)


def clear_coupon_flow(user_data: dict) -> None:
    """Fully reset coupon prompt state (e.g. on /start)."""
    clear_active_coupon(user_data)
    user_data.pop("_coupon_scope", None)
    user_data.pop("_coupon_resume", None)
    user_data.pop("_coupon_handled_msg_id", None)
    user_data.pop("_coupon_last_next_state", None)
    user_data.pop("_coupon_fail", None)


def split_currency_formatted(formatted: str) -> tuple[str, str]:
    """Split a formatted price into (amount, currency_unit)."""
    s = (formatted or "").strip()
    if not s:
        return "", ""
    if s.startswith("$"):
        return s[1:].strip(), "$"
    if " " in s:
        amount, unit = s.rsplit(" ", 1)
        return amount.strip(), unit.strip()
    return s, ""


def format_strikethrough_currency_unit(formatted: str) -> str:
    """Strikethrough the full price (amount + unit) as one unit."""
    return f"<s>{escape(formatted.strip())}</s>"


def format_bold_currency_unit(formatted: str) -> str:
    """Bold only the currency unit for discounted final price."""
    amount, unit = split_currency_formatted(formatted)
    if unit == "$":
        return f"<b>{escape(unit)}</b>{escape(amount)}"
    if unit:
        return f"{escape(amount)} <b>{escape(unit)}</b>"
    return f"<b>{escape(formatted)}</b>"


def coupon_id_from_context(context) -> int | None:
    coupon = active_coupon_from_session(context.user_data)
    if not coupon:
        return None
    scope = context.user_data.get("_coupon_scope")
    if scope and coupon.get("scope") != scope:
        return None
    return coupon.get("code_id")


async def prompt_coupon(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    scope: str,
    resume_callback: str,
) -> int:
    """Show optional coupon prompt. Sets _coupon_scope and _coupon_resume."""
    context.user_data["_coupon_scope"] = scope
    context.user_data["_coupon_resume"] = resume_callback
    clear_active_coupon(context.user_data)

    text = LanguageManager.get("coupon.prompt")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(LanguageManager.get("coupon.btn_enter"), callback_data="coupon_enter")],
        [InlineKeyboardButton(LanguageManager.get("coupon.btn_skip"), callback_data="coupon_skip")],
    ])
    await send_localized_text(update, text, reply_markup=keyboard, query=update.callback_query)
    return COUPON_PROMPT


async def coupon_enter_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start coupon entry from an inline button on plan/renew screens."""
    query = update.callback_query
    if query:
        await query.answer()
    mapping = INLINE_COUPON_CALLBACKS.get(query.data if query else "")
    if not mapping:
        return ConversationHandler.END
    scope, resume = mapping
    context.user_data["_coupon_scope"] = scope
    context.user_data["_coupon_resume"] = resume
    return await coupon_enter_start(update, context)


async def global_coupon_enter_inline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.callback_query:
        return
    if update.callback_query.data not in INLINE_COUPON_CALLBACKS:
        return
    return await coupon_enter_inline(update, context)


async def coupon_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    context.user_data.pop("_coupon_waiting_code", None)
    clear_active_coupon(context.user_data)
    return await _resume_after_coupon(update, context)


async def coupon_enter_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    context.user_data["_coupon_waiting_code"] = True
    text = LanguageManager.get("coupon.enter_code")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(LanguageManager.get("common.cancel"), callback_data="coupon_skip")],
    ])
    if query:
        await query.edit_message_text(text, reply_markup=keyboard, parse_mode="Markdown")
    else:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return COUPON_ENTRY


async def receive_coupon_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg_id = getattr(update.message, "message_id", None)
    if msg_id is not None:
        if context.user_data.get("_coupon_handled_msg_id") == msg_id:
            return context.user_data.get("_coupon_last_next_state", COUPON_ENTRY)
        context.user_data["_coupon_handled_msg_id"] = msg_id

    if check_preview_rate_limit(context.user_data):
        await update.message.reply_text(LanguageManager.get("coupon.rate_limited"))
        return COUPON_ENTRY

    code_str = (update.message.text or "").strip()
    scope = context.user_data.get("_coupon_scope", "buy_ovpn")
    SCOPE_TO_CONTEXT.get(scope, "purchase_ovpn")
    user = update.effective_user

    async with AsyncSessionLocal() as session:
        from vpn_bot.models import User
        from sqlalchemy import select

        u_res = await session.execute(select(User).where(User.telegram_id == user.id))
        db_user = u_res.scalars().first()
        if not db_user:
            record_preview_failure(context.user_data)
            await update.message.reply_text(LanguageManager.get("coupon.invalid"))
            return COUPON_ENTRY

        result = await validate_coupon_eligibility(
            session,
            code_str=code_str,
            user_id=db_user.id,
        )
        if not result.ok:
            record_preview_failure(context.user_data)
            await update.message.reply_text(LanguageManager.get(result.error_key))
            return COUPON_ENTRY

    store_active_coupon(
        context.user_data,
        code_id=result.code_id,
        code=result.code,
        scope=scope,
    )
    context.user_data.pop("_coupon_waiting_code", None)
    await update.message.reply_text(
        LanguageManager.get("coupon.success", code=result.code),
        parse_mode="Markdown",
    )
    next_state = await _resume_after_coupon(update, context)
    context.user_data["_coupon_last_next_state"] = next_state
    return next_state


async def _resume_after_coupon(update: Update, context: ContextTypes.DEFAULT_TYPE):
    resume = context.user_data.pop("_coupon_resume", None)
    context.user_data.get("_coupon_scope")

    if resume == "buy_service_plans":
        from vpn_bot.bot_handler import buy_service_show_plans
        return await buy_service_show_plans(update, context)
    if resume == "buy_wg_plans":
        from vpn_bot.bot_handler import buy_wg_service_show_plans
        return await buy_wg_service_show_plans(update, context)
    if resume == "renew_ovpn_confirm":
        sub_id = context.user_data.get("_renew_sub_id")
        from vpn_bot.user_features import show_renew_ovpn_confirm
        return await show_renew_ovpn_confirm(update, context, int(sub_id))
    if resume == "renew_wg_confirm":
        sub_id = context.user_data.get("_renew_sub_id")
        from vpn_bot.user_features import show_renew_wg_confirm
        return await show_renew_wg_confirm(update, context, int(sub_id))

    return ConversationHandler.END


async def global_coupon_skip(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("_coupon_scope"):
        return
    return await coupon_skip(update, context)


async def global_coupon_enter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("_coupon_scope"):
        return
    return await coupon_enter_start(update, context)


async def global_receive_coupon_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.user_data.get("_coupon_waiting_code"):
        return
    if not context.user_data.get("_coupon_scope"):
        context.user_data.pop("_coupon_waiting_code", None)
        return
    text = (update.message.text or "").strip() if update.message else ""
    if text.startswith("/"):
        clear_coupon_flow(context.user_data)
        return
    return await receive_coupon_code(update, context)


def format_discounted_price_display(original: str, final: str) -> str:
    """Telegram HTML: strikethrough original unit, bold final unit (parse_mode=HTML)."""
    return f"{format_strikethrough_currency_unit(original)} | {format_bold_currency_unit(final)}"


def format_plain_price_html(price: str) -> str:
    return escape(price)


def format_confirm_discount_line_html(original: str, discount: str, final: str) -> str:
    return LanguageManager.get(
        "buy.confirm_discount_line",
        original=format_strikethrough_currency_unit(original),
        final=format_bold_currency_unit(final),
        discount=escape(discount),
    )


def format_discount_line(pricing) -> str:
    if not pricing or pricing.discount_amount <= 0:
        return ""
    return format_confirm_discount_line_html(
        f"{pricing.base_amount:,.0f}",
        f"{pricing.discount_amount:,.0f}",
        f"{pricing.final_amount:,.0f}",
    )
