import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from sqlalchemy import select, and_, func
from sqlalchemy.orm import joinedload
from datetime import datetime
import dotenv
import os

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import (
    User, Subscription, Profile, PaymentReceipt, Server, OvpnConfig, WireGuardSubscription
)
from vpn_bot.mikrotik_manager import MikroTikManager, get_mikrotik_manager
from vpn_bot.config import config
from vpn_bot.utils import (
    logger,
    rate_limit,
    safe_response,
    LanguageManager,
    format_currency,
    get_currency_unit,
    get_profile_price,
    format_datetime,
    ensure_telegram_text,
    format_telegram_rtl,
    send_localized_text,
)
from vpn_bot.settings_utils import get_admin_setting
from vpn_bot.conversation_controls import (
    append_conv_footer,
    conv_control_handlers,
    conv_markup,
    is_conv_cancel,
    legacy_cancel_handlers,
    merge_markup,
)
import asyncio

# States for ConversationHandler
SELECT_SERVER, SELECT_PLAN, BUY_PLAN_CONFIRM, MANUAL_PAYMENT_PENDING = range(4)
REG_NAME, REG_PHONE = range(10, 12)
WALLET_CUSTOM, WALLET_RECEIPT, WALLET_CONFIRM = range(20, 23)
HISTORY_PAGE = 30
SELECT_SERVER_WG, SELECT_PLAN_WG, BUY_PLAN_CONFIRM_WG = range(40, 43)
from vpn_bot.coupon_flow import (
    COUPON_PROMPT,
    COUPON_ENTRY,
    coupon_skip,
    coupon_enter_start,
    coupon_enter_inline,
    receive_coupon_code,
    coupon_id_from_context,
    clear_active_coupon,
)
from vpn_bot.utils import get_wg_profile_price, resolve_checkout_price
from vpn_bot.purchase_terms import (
    TERMS_ACCEPT,
    TERMS_SESSION_KEY,
    maybe_gate_terms,
    fetch_user_by_telegram,
    handle_terms_accept,
    handle_terms_decline,
)
from vpn_bot.test_data import (
    assert_purchasable_plan,
    list_purchasable_ovpn_profiles,
    list_purchasable_wg_profiles,
)

# Filter for main menu buttons to prevent "Invalid amount" errors
# Includes both English and Persian buttons
MENU_BUTTONS_FILTER = filters.Regex(f"^({'|'.join(
    LanguageManager.get_all_translations('menu.wallet') +
    LanguageManager.get_all_translations('menu.buy_service') +
    LanguageManager.get_all_translations('menu.buy_wg') +
    LanguageManager.get_all_translations('menu.my_subs') +
    LanguageManager.get_all_translations('menu.tutorials') +
    LanguageManager.get_all_translations('menu.history') +
    LanguageManager.get_all_translations('menu.support') +
    LanguageManager.get_all_translations('menu.settings')
)})$")

TELEGRAM_MESSAGE_SAFE_MAX = 4000
INLINE_BUTTON_LABEL_MAX = 64


def truncate_telegram_button(text: str, max_len: int = INLINE_BUTTON_LABEL_MAX) -> str:
    """Inline keyboard button labels must fit Telegram's 64-char limit."""
    text = (text or "").strip()
    if len(text) <= max_len:
        return text
    return text[: max_len - 1].rstrip() + "…"


def format_plan_button_label(*, name: str, gb) -> str:
    """Short inline label: volume + admin plan name (RTL-friendly for fa)."""
    gb_val = gb if gb is not None else 0
    raw = LanguageManager.get("buy.plan_button", name=name, gb=gb_val)
    raw = truncate_telegram_button(raw)
    return format_telegram_rtl(raw)


async def build_plan_picker_message(
    header_key: str,
    profiles,
    *,
    days_attr: str,
    gb_attr: str,
    price_for_profile,
    html: bool = False,
) -> str:
    """Build plan picker body with full plan_item blocks; guard against 4096 overflow."""
    from html import escape as html_escape

    from vpn_bot.utils import markdown_bold_to_html

    header = LanguageManager.get(header_key)
    hint = LanguageManager.get("buy.plan_picker_hint")
    if html:
        header = markdown_bold_to_html(header)
    text = header + "\n\n" + hint + "\n\n"
    truncated_count = 0
    item_key = "buy.plan_item_html" if html else "buy.plan_item"
    for idx, p in enumerate(profiles):
        days = getattr(p, days_attr)
        gb = getattr(p, gb_attr, None) or 0
        price = await price_for_profile(p)
        remaining = len(profiles) - idx
        suffix = "\n" + LanguageManager.get("buy.plan_list_truncated", count=remaining)
        rtl_reserve = 350 if LanguageManager._current_lang == "fa" else 0
        budget = TELEGRAM_MESSAGE_SAFE_MAX - len(suffix) - rtl_reserve
        display_name = html_escape(p.name) if html else escape_markdown(p.name, version=1)
        item = LanguageManager.get(
            item_key,
            name=display_name,
            days=days,
            gb=gb,
            price=price,
        )
        if len(text) + len(item) <= budget:
            text += item
            continue
        compact = LanguageManager.get("buy.plan_item_compact", name=p.name, gb=gb)
        if len(text) + len(compact) <= budget:
            text += compact
            continue
        truncated_count = remaining
        break
    if truncated_count:
        text += "\n" + LanguageManager.get("buy.plan_list_truncated", count=truncated_count)
    return text


# --- Helpers ---

async def notify_if_banned(update: Update, db_user: User) -> bool:
    """Return True when the user is banned (message/alert already sent)."""
    if not db_user or not db_user.is_banned:
        return False
    msg = LanguageManager.get("user.account_banned")
    if update.callback_query:
        await update.callback_query.answer(msg, show_alert=True)
        try:
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown")
        except Exception:
            pass
    elif update.message:
        await update.message.reply_text(msg)
    return True


async def block_if_current_user_banned(update: Update) -> bool:
    """Load current user from DB and block if banned."""
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(User).where(User.telegram_id == update.effective_user.id)
        )
        db_user = res.scalars().first()
    return await notify_if_banned(update, db_user)


async def check_sales_status(update: Update, context: ContextTypes.DEFAULT_TYPE, protocol: str):
    """
    Check if sales are enabled globally/protocol-specific and check capacity.
    Returns: (bool, str or None) -> (is_blocked, message)
    """
    from vpn_bot.admin_sales_service import coerce_sales_bool

    # 1. Global Sales Toggle
    global_active = coerce_sales_bool(await get_admin_setting('sales_global_active', True))
    if not global_active:
        msg = await get_admin_setting('sales_global_msg', LanguageManager.get('admin.sales.global_disabled_default'))
        return True, msg

    # 2. Protocol Sales Toggle
    proto_key = f'sales_{protocol.lower()}_active'
    proto_active = coerce_sales_bool(await get_admin_setting(proto_key, True))
    if not proto_active:
        fallback_key = f'admin.sales.{protocol.lower()}_disabled_default'
        msg = await get_admin_setting(f'sales_{protocol.lower()}_msg', LanguageManager.get(fallback_key))
        return True, msg

    # 3. Capacity limit (new purchases only — renewals skip check_sales_status)
    from vpn_bot.admin_sales_service import assert_new_purchase_capacity

    proto = "ovpn" if protocol.lower() == "ovpn" else "wg"
    ok_cap, cap_msg = await assert_new_purchase_capacity(proto)
    if not ok_cap:
        return True, cap_msg

    return False, None

async def check_and_refresh_keyboard(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """
    Check if language changed since user's last interaction.
    If yes, send a new keyboard with updated language and return True.
    Otherwise return False.
    """
    from telegram import ReplyKeyboardMarkup
    
    current_lang = LanguageManager._current_lang
    last_lang = context.user_data.get('last_seen_lang')
    
    # First interaction or same language - just update tracker
    if last_lang is None:
        context.user_data['last_seen_lang'] = current_lang
        return False
    
    # Language unchanged
    if last_lang == current_lang:
        return False
    
    # Language changed! Send new keyboard
    context.user_data['last_seen_lang'] = current_lang
    
    toolbar_keyboard = [
        [LanguageManager.get('menu.buy_service'), LanguageManager.get('menu.buy_wg')],
        [LanguageManager.get('menu.my_subs'), LanguageManager.get('menu.wallet')],
        [LanguageManager.get('menu.tutorials'), LanguageManager.get('menu.support')],
        [LanguageManager.get('menu.history')]
    ]
    toolbar = ReplyKeyboardMarkup(
        toolbar_keyboard, 
        resize_keyboard=True, 
        is_persistent=False
    )
    
    refresh_msg = LanguageManager.get('common.menu_refreshed')
    
    if update.message:
        await update.message.reply_text(refresh_msg, reply_markup=toolbar)
    elif update.callback_query:
        await update.callback_query.message.reply_text(refresh_msg, reply_markup=toolbar)
    
    return True


async def _db_user_for_telegram(telegram_id: int) -> User | None:
    async with AsyncSessionLocal() as session:
        res = await session.execute(select(User).where(User.telegram_id == telegram_id))
        return res.scalars().first()


def _ovpn_sub_usable(sub: Subscription | None) -> bool:
    if not sub:
        return False
    return sub.status == 'active' and not (sub.expiry_date and sub.expiry_date < datetime.now())


def _wg_sub_usable(sub: WireGuardSubscription | None, *, require_active: bool = True) -> bool:
    if not sub:
        return False
    from vpn_bot.utils import utc_now as _utc_now

    exp = sub.expiry_date
    if exp and exp.tzinfo is None:
        from datetime import timezone

        exp = exp.replace(tzinfo=timezone.utc)
    if require_active and (sub.status != 'active' or (exp and exp < _utc_now())):
        return False
    return True


async def get_owned_ovpn_sub(
    sub_id: int, telegram_id: int, *, require_active: bool = True
) -> Subscription | None:
    """Return OVPN subscription only when owned by caller."""
    user = await _db_user_for_telegram(telegram_id)
    if not user:
        return None
    async with AsyncSessionLocal() as session:
        sub = await session.get(Subscription, sub_id)
        if not sub or sub.user_id != user.id:
            return None
        if require_active and not _ovpn_sub_usable(sub):
            return None
        return sub


async def get_owned_wg_sub(sub_id: int, telegram_id: int, *, require_active: bool = True) -> WireGuardSubscription | None:
    """Return WG subscription only when owned by caller (optionally active)."""
    user = await _db_user_for_telegram(telegram_id)
    if not user:
        return None
    async with AsyncSessionLocal() as session:
        sub = await session.get(WireGuardSubscription, sub_id)
        if not sub or sub.user_id != user.id or not _wg_sub_usable(sub, require_active=require_active):
            return None
        return sub


async def show_config_submenu(update: Update, context: ContextTypes.DEFAULT_TYPE, sub_id: int):
    query = update.callback_query
    sub = await get_owned_ovpn_sub(sub_id, update.effective_user.id)
    if not sub:
        await query.answer(LanguageManager.get('config.sub_not_found'), show_alert=True)
        return

    async with AsyncSessionLocal() as session:
            
        res_conf = await session.execute(
            select(OvpnConfig).where(
                (OvpnConfig.server_id == sub.server_id) | (OvpnConfig.server_id is None)
            )
        )
        configs = res_conf.scalars().all()

    text = LanguageManager.get('subs.config_menu', username=sub.mikrotik_username)
    
    keyboard = []
    # OVPN Buttons
    for c in configs:
        label = f"📥 {c.display_name or c.filename}"
        keyboard.append([InlineKeyboardButton(label, callback_data=f"dl_ovpn_{sub.id}_{c.id}")])
    
    # Connection Info Button
    keyboard.append([InlineKeyboardButton(f"🔐 {LanguageManager.get('common.na')} Info", callback_data=f"dl_info_{sub.id}")])
    
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data="my_subs")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')


# --- Start & Main Menu ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User Entry Point."""
    from telegram import ReplyKeyboardMarkup
    from vpn_bot.coupon_flow import clear_coupon_flow

    context.user_data.pop(TERMS_SESSION_KEY, None)
    clear_coupon_flow(context.user_data)
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

        if await notify_if_banned(update, db_user):
            return

        balance = db_user.wallet_balance
        # Force registration if missing phone (New Policy)
        # This will only trigger if start() is called as an entry point for a ConversationHandler
        # that includes REG_NAME state.
        if not db_user.phone_number:
            reg_state = await check_user_registration(update, context)
            if reg_state:
                return reg_state
        # Count active subs (both OVPN and WG)
        s_res = await session.execute(
            select(func.count(Subscription.id)).where(
                and_(Subscription.user_id == db_user.id, Subscription.status == 'active')
            )
        )
        active_ovpn = s_res.scalar() or 0
        
        w_res = await session.execute(
            select(func.count(WireGuardSubscription.id)).where(
                and_(WireGuardSubscription.user_id == db_user.id, WireGuardSubscription.status == 'active')
            )
        )
        active_wg = w_res.scalar() or 0
        
        active_subs = active_ovpn + active_wg

    # 2. Display Menu
    from vpn_bot.utils import format_currency
    formatted_balance = await format_currency(balance)
    
    escaped_name = escape_markdown(user.full_name, version=1)
    # Default welcome message if no custom setting exists
    welcome_plain = LanguageManager.get('start.welcome', name=escaped_name, balance=formatted_balance, active_subs=active_subs)
    
    # Check for custom admin welcome message
    admin_welcome = await get_admin_setting('welcome_message')
    if admin_welcome:
        if isinstance(admin_welcome, dict):
             lang = LanguageManager._current_lang
             raw_text = admin_welcome.get(lang, admin_welcome.get('en', str(admin_welcome)))
        else:
             raw_text = str(admin_welcome)
        
        try:
            # Support placeholders in the custom template
            welcome_text = raw_text.format(
                name=escaped_name,
                balance=formatted_balance, 
                active_subs=active_subs
            )
        except Exception as e:
            logger.error(f"Failed to format custom welcome message: {e}")
            welcome_text = welcome_plain
    else:
        welcome_text = welcome_plain

    toolbar_keyboard = [
        [LanguageManager.get('menu.buy_service'), LanguageManager.get('menu.buy_wg')],
        [LanguageManager.get('menu.my_subs'), LanguageManager.get('menu.wallet')],
        [LanguageManager.get('menu.tutorials'), LanguageManager.get('menu.support')],
        [LanguageManager.get('menu.history')]
    ]
    toolbar = ReplyKeyboardMarkup(
        toolbar_keyboard, 
        resize_keyboard=True, 
        is_persistent=False, # Allows user to collapse the menu
        input_field_placeholder=LanguageManager.get('common.select_option')
    )
    
    menu_header = LanguageManager.get('start.menu_header')

    from telegram.error import Forbidden

    welcome_text = ensure_telegram_text(welcome_text)
    menu_header = ensure_telegram_text(menu_header)
    try:
        if update.callback_query:
            await update.callback_query.message.edit_text(welcome_text, parse_mode='Markdown')
            await update.callback_query.message.reply_text(menu_header, reply_markup=toolbar)
        else:
            await update.message.reply_text(welcome_text, reply_markup=toolbar, parse_mode='Markdown')
    except Forbidden:
        uid = update.effective_user.id if update.effective_user else None
        logger.warning("start: cannot message user %s (blocked bot or chat forbidden)", uid)
        return ConversationHandler.END

    return ConversationHandler.END

# --- Wallet System ---

async def wallet_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Registration Check
    context.user_data['reg_next'] = 'wallet'
    reg_state = await check_user_registration(update, context)
    if reg_state: return reg_state
    if 'reg_next' in context.user_data: del context.user_data['reg_next']

    if await block_if_current_user_banned(update):
        return ConversationHandler.END

    return await wallet_menu_show_presets(update, context)


async def wallet_menu_show_presets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()

    user_id = update.effective_user.id
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user_id))
        user = result.scalars().first()
        balance = user.wallet_balance if user else 0.0

    formatted_balance = await format_currency(balance)
    text = LanguageManager.get('wallet.title', balance=formatted_balance)

    presets = await get_admin_setting('wallet_presets', [5, 10, 20])

    preset_buttons = []
    for p in presets:
        btn_label = await format_currency(p)
        preset_buttons.append([InlineKeyboardButton(btn_label, callback_data=f'topup_{int(p)}')])

    from vpn_bot.admin_settings_service import is_wallet_custom_amount_enabled

    keyboard = list(preset_buttons)
    if await is_wallet_custom_amount_enabled():
        keyboard.append(
            [InlineKeyboardButton(LanguageManager.get('wallet.custom_amount_btn'), callback_data='topup_custom')]
        )
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')])

    reply_markup = merge_markup(InlineKeyboardMarkup(keyboard), with_cancel=True)

    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    return WALLET_CUSTOM

async def payment_help_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show help alert for payment and copying."""
    query = update.callback_query
    await query.answer(LanguageManager.get('wallet.copy_instructions'), show_alert=True)
    return WALLET_RECEIPT

async def _wallet_topup_pricing(update, context, credit_amount: float):
    """Return (credit, payable, coupon_id) for wallet top-up."""
    coupon_id = coupon_id_from_context(context)
    if not coupon_id:
        return credit_amount, credit_amount, None
    async with AsyncSessionLocal() as session:
        u_res = await session.execute(
            select(User).where(User.telegram_id == update.effective_user.id)
        )
        user = u_res.scalars().first()
        if not user:
            return credit_amount, credit_amount, None
        from vpn_bot.discount_service import apply_coupon_to_amount
        from vpn_bot.utils import get_currency_unit
        unit = await get_currency_unit()
        try:
            pricing = await apply_coupon_to_amount(
                session,
                user_id=user.id,
                base_amount=credit_amount,
                currency=unit,
                context="wallet_topup",
                coupon_id=coupon_id,
            )
        except ValueError:
            return credit_amount, credit_amount, None
        return credit_amount, pricing.final_amount, pricing.discount_code_id


@rate_limit(seconds=3)
async def topup_amount_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    try:
        credit = int(query.data.split('_')[1])
    except (IndexError, ValueError):
        await query.answer(LanguageManager.get('wallet.invalid_amount'), show_alert=True)
        return

    from vpn_bot.admin_settings_service import get_wallet_presets, get_wallet_custom_limits
    presets = await get_wallet_presets()
    min_amount, max_amount = await get_wallet_custom_limits()
    if credit not in presets and not (min_amount <= credit <= max_amount):
        await query.answer(LanguageManager.get('wallet.invalid_amount'), show_alert=True)
        return

    credit_f, payable, coupon_id = await _wallet_topup_pricing(update, context, float(credit))
    context.user_data['topup_amount'] = credit_f
    context.user_data['topup_payable'] = payable
    context.user_data['pending_coupon_id'] = coupon_id
    
    # Fetch payment cards (supporting multiple)
    cards = await get_admin_setting('payment_cards', [])
    
    # If using old singular settings as fallback (legacy compat)
    if not cards:
        card_number = await get_admin_setting('card_number', '1234-5678-9012-3456')
        card_holder = await get_admin_setting('card_holder', 'Unknown Holder')
        card_bank = await get_admin_setting('card_bank', 'Unknown Bank')
        cards = [{'number': card_number, 'holder': card_holder, 'bank': card_bank}]

    # Build text
    card_text = ""
    keyboard = []
    
    for card in cards:
        number = card.get('number', '')
        holder = card.get('holder', '')
        bank = card.get('bank', '')
        card_text += LanguageManager.get('wallet.card_template', number=number, holder=holder, bank=bank)
    
    keyboard.append([InlineKeyboardButton(LanguageManager.get('wallet.btn_pay_help'), callback_data="payment_help")])
    
    # Construct full message
    # Format amount with currency (payable if discounted)
    display_amount = context.user_data.get('topup_payable', credit_f)
    formatted_amount = await format_currency(display_amount)
    formatted_credit = await format_currency(credit_f)
    
    header = LanguageManager.get('wallet.pay_instruction_header', amount=formatted_amount)
    if payable < credit_f:
        header += LanguageManager.get(
            'wallet.payable_vs_credit',
            credit=formatted_credit,
            payable=formatted_amount,
        )
    copy_prompt = LanguageManager.get('wallet.pay_copy_prompt')
    footer = LanguageManager.get('wallet.pay_instruction_footer')
    
    text = f"{header}{card_text}\n{copy_prompt}{footer}"
    
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.cancel'), callback_data='wallet_menu')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return WALLET_RECEIPT

@rate_limit(seconds=3)
async def topup_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle custom amount input."""
    from vpn_bot.admin_settings_service import (
        get_wallet_custom_limits,
        is_wallet_custom_amount_enabled,
    )

    query = update.callback_query
    if not await is_wallet_custom_amount_enabled():
        await query.answer(LanguageManager.get('wallet.custom_amount_disabled'), show_alert=True)
        return await wallet_menu(update, context)

    await query.answer()

    min_amount, max_amount = await get_wallet_custom_limits()
    formatted_min = await format_currency(min_amount)
    formatted_max = await format_currency(max_amount)
    text = LanguageManager.get(
        'wallet.custom_amount_prompt',
        min=formatted_min,
        max=formatted_max,
    )

    curr_unit = await get_currency_unit()
    if curr_unit in ['TOMAN', 'RIAL']:
         text += LanguageManager.get('wallet.currency_warning', unit=curr_unit)
    
    await query.edit_message_text(
        append_conv_footer(text, with_skip=False),
        reply_markup=conv_markup(),
        parse_mode='Markdown',
    )
    return WALLET_CUSTOM

async def receive_custom_amount(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and validate custom amount."""
    if is_conv_cancel(update):
        return await start(update, context)
    # If menu button clicked, end conversation and let main handlers take over
    if update.message and MENU_BUTTONS_FILTER.filter(update.message):
        return ConversationHandler.END

    try:
        from vpn_bot.admin_settings_service import get_wallet_custom_limits

        amount = float(update.message.text.strip().replace(",", ""))
        min_amount, max_amount = await get_wallet_custom_limits()
            
        if amount < min_amount:
            formatted_min = await format_currency(min_amount)
            await update.message.reply_text(
                LanguageManager.get('wallet.min_amount_error', min=formatted_min),
                reply_markup=conv_markup(),
            )
            return WALLET_CUSTOM
        
        if amount > max_amount:
            formatted_max = await format_currency(max_amount)
            await update.message.reply_text(
                LanguageManager.get('wallet.max_amount_error', max=formatted_max),
                reply_markup=conv_markup(),
            )
            return WALLET_CUSTOM
        
        context.user_data['topup_amount'] = amount
        credit_f, payable, coupon_id = await _wallet_topup_pricing(update, context, amount)
        context.user_data['topup_amount'] = credit_f
        context.user_data['topup_payable'] = payable
        context.user_data['pending_coupon_id'] = coupon_id
        
        # Fetch payment cards
        cards = await get_admin_setting('payment_cards', [])
        
        # Fallback to legacy singular settings if empty list
        if not cards:
            card_number = await get_admin_setting('card_number', '1234-5678-9012-3456')
            card_holder = await get_admin_setting('card_holder', 'Unknown Holder')
            card_bank = await get_admin_setting('card_bank', 'Unknown Bank')
            cards = [{'number': card_number, 'holder': card_holder, 'bank': card_bank}]
        
        # Build text and keyboard
        card_text = ""
        keyboard = []
        
        for card in cards:
            number = card.get('number', '')
            holder = card.get('holder', '')
            bank = card.get('bank', '')
            card_text += LanguageManager.get('wallet.card_template', number=number, holder=holder, bank=bank)
            
        keyboard.append([InlineKeyboardButton(LanguageManager.get('wallet.btn_pay_help'), callback_data="payment_help")])
        
        # Construct full message
        display_amount = context.user_data.get('topup_payable', credit_f)
        formatted_amount = await format_currency(display_amount)
        formatted_credit = await format_currency(credit_f)
        header = LanguageManager.get('wallet.pay_instruction_header', amount=formatted_amount)
        if payable < credit_f:
            header += LanguageManager.get(
                'wallet.payable_vs_credit',
                credit=formatted_credit,
                payable=formatted_amount,
            )
        copy_prompt = LanguageManager.get('wallet.pay_copy_prompt')
        footer = LanguageManager.get('wallet.pay_instruction_footer')
        
        # Add Currency Warning if Toman/Rial
        curr_unit = await get_currency_unit()
        warning = ""
        if curr_unit in ['TOMAN', 'RIAL']:
            warning = LanguageManager.get('wallet.currency_warning', unit=curr_unit)
        
        text = f"{header}{card_text}\n{copy_prompt}{warning}{footer}"
        
        keyboard.append([InlineKeyboardButton(LanguageManager.get('common.cancel'), callback_data='wallet_menu')])
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return WALLET_RECEIPT
        
    except ValueError:
        await update.message.reply_text(
            LanguageManager.get('wallet.invalid_amount'),
            reply_markup=conv_markup(),
        )
        return WALLET_CUSTOM


@rate_limit(seconds=3)
async def receive_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive receipt in multiple formats: photo, document (PDF/image), or text."""
    if is_conv_cancel(update):
        return await start(update, context)
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
            await update.message.reply_text(LanguageManager.get('receipt.invalid_format'))
            return WALLET_RECEIPT
    elif update.message.text:
        # Text-based receipt (e.g., transaction reference)
        text_receipt = update.message.text.strip()
        if len(text_receipt) < 5:
            await update.message.reply_text(LanguageManager.get('receipt.invalid_text'))
            return WALLET_RECEIPT
        context.user_data['receipt_text'] = text_receipt
        context.user_data['receipt_file_id'] = None
        context.user_data['receipt_type'] = 'text'
        
        amount = context.user_data.get('topup_amount', 0)
        formatted_amount = await format_currency(amount)
        keyboard = [
            [InlineKeyboardButton(LanguageManager.get('common.confirm'), callback_data="receipt_yes")],
            [InlineKeyboardButton(LanguageManager.get('common.cancel'), callback_data="receipt_no")]
        ]
        await update.message.reply_text(
            LanguageManager.get('receipt.confirm_text', amount=formatted_amount, reference=text_receipt),
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return WALLET_CONFIRM
    else:
        await update.message.reply_text(LanguageManager.get('receipt.invalid_format'))
        return WALLET_RECEIPT
    
    # For photo/document
    amount = context.user_data.get('topup_amount', 0)
    formatted_amount = await format_currency(amount)
    context.user_data['receipt_file_id'] = file_id
    context.user_data['receipt_type'] = receipt_type
    context.user_data['receipt_caption'] = update.message.caption
    
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('common.confirm'), callback_data="receipt_yes")],
        [InlineKeyboardButton(LanguageManager.get('common.cancel'), callback_data="receipt_no")]
    ]
    
    caption_text = LanguageManager.get('receipt.confirm_caption', amount=formatted_amount)
    
    if receipt_type == 'photo':
        await update.message.reply_photo(
            photo=file_id,
            caption=caption_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    else:
        # Document
        await update.message.reply_document(
            document=file_id,
            caption=caption_text,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
    return WALLET_CONFIRM

async def confirm_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    if query.data == "receipt_no":
        await query.message.delete()
        await context.bot.send_message(query.message.chat_id, LanguageManager.get('receipt.prompt_new'))
        return WALLET_RECEIPT
    
    # Save receipt
    user_id = update.effective_user.id
    amount = context.user_data.get('topup_amount', 0)
    file_id = context.user_data.get('receipt_file_id')
    receipt_type = context.user_data.get('receipt_type', 'photo')
    receipt_text = context.user_data.get('receipt_text', '')
    
    import uuid
    unique_id = str(uuid.uuid4())[:8].upper()  # Short unique ID like "A1B2C3D4"
    
    # Phase 1: Immutable Persistence
    # We save the receipt immediately and commit to ensure it's in the DB no matter what happens next.
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == user_id))
        user = result.scalars().first()
        if not user:
             await query.answer(LanguageManager.get('receipt.user_not_found'), show_alert=True)
             return ConversationHandler.END

        unit = await get_currency_unit()
        
        # Detect if this is a WG plan or regular plan
        pending_plan_id = context.user_data.get('pending_plan_id')
        pending_wg_id = context.user_data.get('pending_wg_plan_id')
        
        target_plan_id = pending_wg_id if pending_wg_id else pending_plan_id
        is_wg = True if pending_wg_id else False
        coupon_id = context.user_data.get('pending_coupon_id') or coupon_id_from_context(context)
        credit_amount = amount
        payable_amount = context.user_data.get('topup_payable', amount)

        receipt = PaymentReceipt(
            user_id=user.id,
            amount=credit_amount,
            receipt_file_id=file_id if file_id else receipt_text,
            status='pending',
            unique_id=unique_id,
            user_caption=context.user_data.get('receipt_caption'),
            receipt_type=receipt_type,
            currency_unit=unit,
            plan_id=target_plan_id,
            is_wireguard=is_wg,
            discount_code_id=coupon_id,
            credit_amount=credit_amount,
            payable_amount=payable_amount,
        )
        session.add(receipt)
        await session.commit() # Fixed entry in DB
        await session.refresh(receipt)
        
    # Clear pending plans from context to avoid re-use
    context.user_data.pop('pending_plan_id', None)
    context.user_data.pop('pending_wg_plan_id', None)
    context.user_data.pop('pending_coupon_id', None)
        
    receipt_id = receipt.id
    receipt_unique_id = f"RCP-{receipt_id}-{unique_id}"

    from vpn_bot.admin_receipt_service import notify_admins_new_receipt

    await notify_admins_new_receipt(
        context.bot,
        receipt,
        user,
        receipt_text=receipt_text,
        tg_display_name=update.effective_user.full_name,
        tg_username=update.effective_user.username,
    )

    # Final step: Notify User
    if receipt.plan_id:
        confirm_text = LanguageManager.get('buy.success_pending', id=receipt_unique_id)
    else:
        confirm_text = LanguageManager.get('receipt.success', id=receipt_unique_id)
    
    keyboard = [[InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')]]
    
    try:
        # Edit message to show success and remove previous buttons
        if receipt_type in ['photo', 'document']:
            await query.edit_message_caption(caption=confirm_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        else:
            await query.edit_message_text(text=confirm_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception:
        # Fallback if edit fails (e.g. message too old)
        await query.message.reply_text(confirm_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        
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
        wg_subscriptions = []
        if db_user:
            # Regular Subs
            visible_statuses = ('active', 'disabled', 'expired')
            s_res = await session.execute(
                select(Subscription)
                .options(joinedload(Subscription.profile))
                .where(
                    and_(
                        Subscription.user_id == db_user.id,
                        Subscription.status.in_(visible_statuses),
                    )
                )
            )
            subscriptions = s_res.scalars().all()
            
            # WireGuard Subs
            wg_res = await session.execute(
                select(WireGuardSubscription)
                .options(joinedload(WireGuardSubscription.profile))
                .where(
                    and_(
                        WireGuardSubscription.user_id == db_user.id,
                        WireGuardSubscription.status.in_(visible_statuses),
                    )
                )
            )
            wg_subscriptions = wg_res.scalars().all()
            logger.info(f"User {user_id} (DB {db_user.id}) has {len(subscriptions)} reg subs and {len(wg_subscriptions)} wg subs")
    
    keyboard = []
    if not subscriptions and not wg_subscriptions:
        keyboard = [[InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')]]
        msg_text = LanguageManager.get('subs.empty')
        if query:
            await query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        else:
            await update.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return ConversationHandler.END

    header_text = LanguageManager.get('subs.header')
    keyboard = []

    from vpn_bot.utils import utc_now as _utc_now

    _now = _utc_now()

    # List Regular Subs
    for sub in subscriptions:
        exp = sub.expiry_date
        if exp and exp.tzinfo is None:
            from datetime import timezone

            exp = exp.replace(tzinfo=timezone.utc)
        is_expired = exp < _now if exp else False
        is_nearly_expired = False
        if not is_expired and exp:
            is_nearly_expired = (exp - _now).total_seconds() < (48 * 3600)
        
        if is_expired:
            status_text = LanguageManager.get('subs.status_expired')
            status_icon = "🔴"
        elif is_nearly_expired:
            status_text = LanguageManager.get('subs.status_nearly_expired')
            status_icon = "🟡"
        elif sub.status == 'active':
            status_text = LanguageManager.get('subs.status_active')
            status_icon = "🟢"
        else:
            status_text = LanguageManager.get('subs.status_disabled')
            status_icon = "⚪"
            
        btn_text = f"{status_icon} {status_text} - {sub.mikrotik_username}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"view_sub_{sub.id}")])

    # List WireGuard Subs
    for sub in wg_subscriptions:
        exp = sub.expiry_date
        if exp and exp.tzinfo is None:
            from datetime import timezone

            exp = exp.replace(tzinfo=timezone.utc)
        is_expired = exp < _now if exp else False
        is_nearly_expired = False
        if not is_expired and exp:
            is_nearly_expired = (exp - _now).total_seconds() < (48 * 3600)

        if is_expired:
            status_text = LanguageManager.get('subs.status_expired')
            status_icon = "🔴"
        elif is_nearly_expired:
            status_text = LanguageManager.get('subs.status_nearly_expired')
            status_icon = "🟡"
        elif sub.status == 'active':
            status_text = LanguageManager.get('subs.status_active')
            status_icon = "🟢"
        else:
            status_text = LanguageManager.get('subs.status_disabled')
            status_icon = "⚪"

        btn_text = f"{status_icon} {status_text} - {sub.unique_identifier}"
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"view_wg_sub_{sub.id}")])

    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')])
    
    if query:
        await query.edit_message_text(header_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(header_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    return ConversationHandler.END

async def view_wg_subscription_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Detailed view for WireGuard subscriptions."""
    query = update.callback_query
    await query.answer()

    sub_id = int(query.data.split('_')[3])

    async with AsyncSessionLocal() as session:
        s_res = await session.execute(
            select(WireGuardSubscription)
            .options(joinedload(WireGuardSubscription.profile), joinedload(WireGuardSubscription.interface))
            .where(WireGuardSubscription.id == sub_id)
        )
        sub = s_res.scalars().first()

    user = await _db_user_for_telegram(update.effective_user.id)
    if not user or not sub or sub.user_id != user.id:
        await query.answer(LanguageManager.get('common.error_not_found'), show_alert=True)
        return await my_subscriptions(update, context)

    from vpn_bot.utils import utc_now as _utc_now

    exp = sub.expiry_date
    if exp and exp.tzinfo is None:
        from datetime import timezone

        exp = exp.replace(tzinfo=timezone.utc)
    _now = _utc_now()
    is_expired = exp < _now if exp else False
    days_left = (exp - _now).days if not is_expired and exp else 0
    total_days = sub.profile.duration_days if sub.profile else 30

    status_text = LanguageManager.get('status.active') if not is_expired and sub.status == 'active' else LanguageManager.get('status.disabled')

    formatted_expiry = await format_datetime(sub.expiry_date, include_time=True)
    
    # Usage
    total_gb = sub.profile.volume_gb if sub.profile else 0
    used_bytes = (sub.total_bytes_rx or 0) + (sub.total_bytes_tx or 0)
    used_gb = used_bytes / (1024**3)
    pct = (used_gb / total_gb * 100) if total_gb > 0 else 0
    
    remaining_days = LanguageManager.get('subs.remaining_days', left=days_left, total=total_days)

    text = LanguageManager.get('subs.wg_item_details').format(
        status=status_text,
        unique_id=sub.unique_identifier,
        ip=sub.assigned_ip,
        total_gb=total_gb,
        used_gb=used_gb,
        pct=pct,
        remaining_ux=remaining_days,
        expiry=formatted_expiry
    )

    keyboard = []
    from vpn_bot.renewal_policy import should_offer_renewal_button

    if await should_offer_renewal_button(sub, "wg", profile=sub.profile):
        keyboard.append(
            [InlineKeyboardButton(LanguageManager.get('subs.btn_renew'), callback_data=f"renew_wg_{sub.id}")]
        )
    keyboard.append(
        [InlineKeyboardButton(LanguageManager.get('subs.btn_resend_config'), callback_data=f"get_wg_conf_{sub.id}")]
    )
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='my_subs')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def send_wg_config_again(update: Update, context: ContextTypes.DEFAULT_TYPE, sub_id: int = None):
    """Resend WG config and QR code to user."""
    query = update.callback_query
    if query:
        await query.answer(LanguageManager.get('wg.resending_config'))
    
    if sub_id is None and query:
        sub_id = int(query.data.split('_')[3])
    
    if not sub_id:
        return

    async with AsyncSessionLocal() as session:
        from vpn_bot.models import Server
        from vpn_bot.wg_delivery import deliver_wg_config

        owned = await get_owned_wg_sub(sub_id, update.effective_user.id)
        if not owned:
            if query:
                await query.answer(LanguageManager.get('config.sub_not_found'), show_alert=True)
            return

        s_res = await session.execute(
            select(WireGuardSubscription)
            .options(joinedload(WireGuardSubscription.interface), joinedload(WireGuardSubscription.profile))
            .where(WireGuardSubscription.id == sub_id)
        )
        sub = s_res.scalars().first()
        if not sub:
            if query:
                await query.answer(LanguageManager.get('config.sub_not_found'), show_alert=True)
            return

        server = await session.get(Server, sub.interface.server_id)
        if not server:
            if query:
                await query.answer(LanguageManager.get('config.sub_not_found'), show_alert=True)
            return

        chat_id = update.effective_user.id
        ok = await deliver_wg_config(context.bot, chat_id, sub, server, show_main_menu=True)
        if not ok and query:
            await query.answer(LanguageManager.get('config.sub_not_found'), show_alert=True)

async def view_subscription_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View detailed info of a single subscription (suggested for better UX)."""
    query = update.callback_query
    await query.answer()

    sub_id = int(query.data.split('_')[2])
    if not await get_owned_ovpn_sub(sub_id, update.effective_user.id, require_active=False):
        await query.answer(LanguageManager.get('common.error_not_found'), show_alert=True)
        return await my_subscriptions(update, context)

    async with AsyncSessionLocal() as session:
        s_res = await session.execute(
            select(Subscription)
            .options(joinedload(Subscription.profile))
            .where(Subscription.id == sub_id)
        )
        sub = s_res.scalars().first()
        server = await session.get(Server, sub.server_id)

    mgr = get_mikrotik_manager(server) if server else MikroTikManager()
    # Fetch live data
    info = await asyncio.to_thread(mgr.get_user_info, sub.mikrotik_username)
    
    # Usage Data
    used_gb = (info.get('used_bytes', 0) / 1024**3) if info else 0
    total_gb = ((sub.total_limit_bytes or 0) / 1024**3)
    pct = (used_gb / total_gb * 100) if total_gb > 0 else 0
    
    from datetime import datetime
    is_expired = sub.expiry_date < datetime.now()
    days_left = (sub.expiry_date - datetime.now()).days if not is_expired else 0
    total_days = sub.profile.validity_days if sub.profile else 30
    
    LanguageManager.get('subs.remaining_days', left=days_left, total=total_days)
    
    
    status_key = 'active' if info and info['status'] == 'active' else ('expired' if is_expired else 'deactive')
    status_text = LanguageManager.get(f'subs.status_{status_key}')
    status_icon = "🟢" if info and info['status'] == 'active' else "🔴"

    formatted_expiry = await format_datetime(sub.expiry_date, include_time=True)

    sub_text = LanguageManager.get('subs.item_details',
        icon=status_icon,
        status=status_text,
        username=escape_markdown(sub.mikrotik_username, version=1),
        password=escape_markdown(sub.mikrotik_password, version=1),
        total_gb=total_gb,
        used_gb=used_gb,
        pct=pct,
        remaining_days=days_left,
        total_days=total_days,
        expiry=formatted_expiry
    )
    
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('subs.btn_get_conn_info'), callback_data=f"get_conn_info_{sub.id}")],
        [InlineKeyboardButton(LanguageManager.get('subs.btn_get_config_file'), callback_data=f"get_ovpn_file_{sub.id}")]
    ]
    
    from vpn_bot.renewal_policy import should_offer_renewal_button

    if await should_offer_renewal_button(sub, "um", profile=sub.profile):
        keyboard.append(
            [InlineKeyboardButton(LanguageManager.get('subs.btn_renew'), callback_data=f"renew_{sub.id}")]
        )
    
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='my_subs')])
    
    try:
        await query.edit_message_text(sub_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    except Exception:
        await query.message.reply_text(sub_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    return ConversationHandler.END

async def view_subscription_connection_info(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show L2TP/SSTP info by editing the message, with a back button."""
    query = update.callback_query
    await query.answer()

    sub_id = int(query.data.split('_')[3])
    sub = await get_owned_ovpn_sub(sub_id, update.effective_user.id)
    if not sub:
        await query.answer(LanguageManager.get('common.error_not_found'), show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        server = await session.get(Server, sub.server_id)

    # Re-use logic to generate connection info text
    conn_info_all = await get_admin_setting('connection_info', {})
    if not isinstance(conn_info_all, dict):
        conn_info_all = {}
        
    s_info = conn_info_all.get(str(server.id) if server else "0", {
        'l2tp': {'ip': server.host if server else "Unknown", 'secret': '123456'},
        'sstp': {'ip': server.host if server else "Unknown"}
    })
    
    l2tp_host = s_info['l2tp'].get('ip') or (server.host if server else "Unknown")
    l2tp_port = s_info['l2tp'].get('port', '1701')
    l2tp_secret = s_info['l2tp'].get('secret', '123456')
    
    sstp_host = s_info['sstp'].get('ip') or (server.host if server else "Unknown")
    sstp_port = s_info['sstp'].get('port', '443')
    
    l2tp_version = s_info['l2tp'].get('version', 'l2tp_v2')
    l2tp_title = LanguageManager.get(f'config.{l2tp_version}_title')
    l2tp_port_line = LanguageManager.get('config.l2tp_port_line', port=l2tp_port) if l2tp_version == 'l2tp_v3' else ""
    
    info_text = LanguageManager.get('config.conn_info',
        l2tp_title=l2tp_title,
        l2tp_host=l2tp_host,
        l2tp_port_line=l2tp_port_line,
        l2tp_secret=l2tp_secret,
        sstp_host=sstp_host,
        sstp_port=sstp_port,
        username=sub.mikrotik_username,
        password=sub.mikrotik_password
    )
    
    keyboard = [[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data=f"view_sub_{sub.id}")]]
    await query.edit_message_text(info_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def view_subscription_ovpn_file(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send OVPN file with guide and back button."""
    query = update.callback_query

    sub_id = int(query.data.split('_')[3])
    sub = await get_owned_ovpn_sub(sub_id, update.effective_user.id)
    if not sub:
        await query.answer(LanguageManager.get('common.error_not_found'), show_alert=True)
        return

    async with AsyncSessionLocal() as session:
        # Determine best OVPN config (pick first available for the server)
        # Assuming OvpnConfig has a server_id field or we pick generic
        # Wait, the logic for finding OVPN files usually depends on folder structure or DB.
        # Let's look at `send_ovpn_file` logic reuse if possible, or query `OvpnConfig`.
        # Previously `dl_ovpn_` used `OvpnConfig` ID passed in URL. Here we don't have it.
        # We need to find one.
        
        from vpn_bot.models import OvpnConfig
        stmt = select(OvpnConfig).where(OvpnConfig.server_id == sub.server_id)
        res = await session.execute(stmt)
        configs = res.scalars().all()
        
        if not configs:
            error_text = LanguageManager.get('config.no_ovpn')
            keyboard = [[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data=f"view_sub_{sub.id}")]]
            await query.edit_message_text(error_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            return
            
        # Pick the first one for simplicity as requested "The button" implication
        ovpn = configs[0]

    # Check file existence before proceeding
    file_path = ovpn.file_path
    if not os.path.isabs(file_path):
        file_path = os.path.join(os.getcwd(), file_path)
    
    if not os.path.exists(file_path):
        error_text = LanguageManager.get('config.error_load')
        keyboard = [[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data=f"view_sub_{sub.id}")]]
        await query.edit_message_text(error_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return
        
    await query.answer(LanguageManager.get('config.sending_ovpn', label=ovpn.label or "Config"))

    caption = LanguageManager.get('subs.ovpn_guide_text')
    
    keyboard = [[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data=f"view_sub_{sub.id}")]]
    
    with open(file_path, 'rb') as f:
        await context.bot.send_document(
            chat_id=update.effective_chat.id,
            document=f,
            caption=caption,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )


# --- Buy Service ---

@safe_response
async def buy_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
    
    # --- Maintenance Status Check ---
    from vpn_bot.utils import check_maintenance_status
    is_maint, maint_msg = await check_maintenance_status()
    if is_maint:
        if query:
            await query.edit_message_text(maint_msg, parse_mode='Markdown')
        else:
            await update.message.reply_text(maint_msg, parse_mode='Markdown')
        return ConversationHandler.END

    # --- Sales Status Check ---
    is_blocked, block_msg = await check_sales_status(update, context, 'ovpn')
    if is_blocked:
        from vpn_bot.utils import ensure_telegram_text

        block_msg = ensure_telegram_text(
            block_msg,
            fallback_key="admin.sales.capacity_full_default",
        )
        if query:
            await query.edit_message_text(block_msg, parse_mode='Markdown')
        else:
            await update.message.reply_text(block_msg, parse_mode='Markdown')
        return ConversationHandler.END

    if await block_if_current_user_banned(update):
        return ConversationHandler.END

    # Registration Check
    context.user_data['reg_next'] = 'buy'
    reg_state = await check_user_registration(update, context)
    if reg_state: return reg_state
    if 'reg_next' in context.user_data: del context.user_data['reg_next']

    if not context.user_data.pop('_from_terms_resume', False):
        context.user_data.pop(TERMS_SESSION_KEY, None)
    db_user = await fetch_user_by_telegram(update.effective_user.id)
    gate = await maybe_gate_terms(update, context, db_user, resume={"type": "buy_ovpn"})
    if gate is not None:
        return gate

    profiles = await list_purchasable_ovpn_profiles()

    if not profiles:
        msg = LanguageManager.get('buy.no_plans')
        if query:
             await query.edit_message_text(msg)
        else:
             await update.message.reply_text(msg)
        return ConversationHandler.END

    context.user_data["_coupon_scope"] = "buy_ovpn"
    context.user_data["_coupon_resume"] = "buy_service_plans"
    return await buy_service_show_plans(update, context)


async def buy_service_show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    context.user_data["_coupon_scope"] = "buy_ovpn"
    context.user_data["_coupon_resume"] = "buy_service_plans"
    coupon_id = coupon_id_from_context(context)

    profiles = await list_purchasable_ovpn_profiles()

    async with AsyncSessionLocal() as session:
        u_res = await session.execute(
            select(User).where(User.telegram_id == update.effective_user.id)
        )
        db_user = u_res.scalars().first()

    if not profiles:
        msg = LanguageManager.get('buy.no_plans')
        await send_localized_text(update, msg, query=query)
        return ConversationHandler.END

    use_html = bool(coupon_id)

    async def _ovpn_price(profile):
        if db_user and coupon_id:
            pricing = await resolve_checkout_price(
                profile, is_wg=False, coupon_id=coupon_id, user_id=db_user.id, context="purchase_ovpn"
            )
            base_f = await format_currency(pricing.base_amount)
            if pricing.discount_amount > 0:
                from vpn_bot.coupon_flow import format_discounted_price_display

                final_f = await format_currency(pricing.final_amount)
                return format_discounted_price_display(base_f, final_f)
            if use_html:
                from vpn_bot.coupon_flow import format_plain_price_html

                return format_plain_price_html(base_f)
            return base_f
        price = await format_currency(await get_profile_price(profile))
        if use_html:
            from vpn_bot.coupon_flow import format_plain_price_html

            return format_plain_price_html(price)
        return price

    text = await build_plan_picker_message(
        "buy.select_plan",
        profiles,
        days_attr="validity_days",
        gb_attr="data_limit_gb",
        price_for_profile=_ovpn_price,
        html=use_html,
    )
    keyboard = []

    for p in profiles:
        label = format_plan_button_label(name=p.name, gb=p.data_limit_gb)
        keyboard.append([InlineKeyboardButton(label, callback_data=f"buy_plan_{p.id}")])

    if not coupon_id:
        keyboard.append([
            InlineKeyboardButton(
                LanguageManager.get('coupon.btn_enter'),
                callback_data='coupon_enter_inline_buy_ovpn',
            )
        ])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('buy.btn_what_is_multi'), callback_data='what_is_multi')])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await send_localized_text(
        update,
        text,
        reply_markup=reply_markup,
        query=query,
        parse_mode="HTML" if use_html else "Markdown",
    )

    return BUY_PLAN_CONFIRM

@safe_response
async def what_is_multi_proto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show detailed explanation about multi-protocol subscription."""
    query = update.callback_query
    if query:
        await query.answer()
        
    text = LanguageManager.get('buy.what_is_multi_desc')
    keyboard = [[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='buy_service')]]
    
    if query:
        await query.message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    
    return BUY_PLAN_CONFIRM

# --- User Registration Flow ---

async def receive_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Collect full name."""
    user = update.effective_user
    logging.info(f"REG_NAME: User {user.id} entered name: {update.message.text}")
    
    name = update.message.text.strip()
    if len(name.split()) < 2:
        logging.info(f"REG_NAME: Invalid name '{name}' from user {user.id}")
        error_msg = await get_admin_setting('reg_name_error', LanguageManager.get('buy.reg_name_error'))
        await update.message.reply_text(error_msg)
        return REG_NAME
    
    context.user_data['reg_name'] = name
    logging.info(f"REG_NAME: Name accepted for user {user.id}. Moving to REG_PHONE.")
    
    from telegram import ReplyKeyboardMarkup, KeyboardButton
    btn_text = await get_admin_setting('reg_phone_btn', LanguageManager.get('buy.reg_phone_btn'))
    keyboard = [[KeyboardButton(btn_text, request_contact=True)]]
    reply_markup = ReplyKeyboardMarkup(keyboard, one_time_keyboard=True, resize_keyboard=True)
    
    prompt_tpl = await get_admin_setting('reg_phone_prompt', LanguageManager.get('buy.reg_phone_prompt'))
    try:
        prompt_text = prompt_tpl.format(name=escape_markdown(name.split()[0], version=1))
    except:
        prompt_text = prompt_tpl

    await update.message.reply_text(
        prompt_text,
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )
    return REG_PHONE

async def receive_phone_manual_warning(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Warn user not to type phone number manually."""
    user = update.effective_user
    logging.info(f"REG_PHONE: User {user.id} typed phone manually: {update.message.text}. Sending warning.")
    await update.message.reply_text(LanguageManager.get('buy.reg_phone_manual_error'), parse_mode='Markdown')
    return REG_PHONE

async def receive_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Collect phone number via contact sharing."""
    user_id = update.effective_user.id
    contact = update.message.contact
    logging.info(f"REG_PHONE: Handling contact for user {user_id}")
    
    if not contact:
        logging.warning(f"REG_PHONE: No contact received for user {user_id}")
        await update.message.reply_text(LanguageManager.get('buy.reg_phone_error'))
        return REG_PHONE
    
    phone = contact.phone_number
    logging.info(f"REG_PHONE: Phone received: {phone}")
    
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
            logging.info(f"REG_PHONE: User {user_id} updated in DB.")
            
    from telegram import ReplyKeyboardRemove
    success_msg = await get_admin_setting('reg_complete_msg', LanguageManager.get('buy.reg_complete'))
    await update.message.reply_text(
        success_msg,
        reply_markup=ReplyKeyboardRemove(),
        parse_mode='Markdown'
    )
    
    # Redirect back based on context
    reg_next = context.user_data.get('reg_next')
    
    if reg_next == 'support':
        # Clear the flag
        del context.user_data['reg_next']
        from vpn_bot.support_tickets import support_menu
        # We need to call support_menu with current update
        # support_menu handles message updates too
        return await support_menu(update, context)
        
    if reg_next == 'wallet':
        del context.user_data['reg_next']
        return await wallet_menu(update, context)
    elif reg_next == 'wg':
        del context.user_data['reg_next']
        return await buy_wg_service(update, context)
        
    # Default to buy service
    if reg_next == 'buy': del context.user_data['reg_next']
    return await buy_service_initial(update, context)

async def buy_service_initial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Helper to restart the buy flow without query (e.g. after registration)."""
    if not context.user_data.pop('_from_terms_resume', False):
        context.user_data.pop(TERMS_SESSION_KEY, None)
    db_user = await fetch_user_by_telegram(update.effective_user.id)
    gate = await maybe_gate_terms(update, context, db_user, resume={"type": "buy_ovpn"})
    if gate is not None:
        return gate

    profiles = await list_purchasable_ovpn_profiles()

    if not profiles:
        await update.message.reply_text(LanguageManager.get('buy.no_plans'))
        return ConversationHandler.END

    context.user_data["_coupon_scope"] = "buy_ovpn"
    context.user_data["_coupon_resume"] = "buy_service_plans"
    return await buy_service_show_plans(update, context)

@safe_response
async def confirm_purchase(update: Update, context: ContextTypes.DEFAULT_TYPE, profile_id: int | None = None):
    query = update.callback_query
    if query:
        await query.answer()

    if profile_id is None:
        profile_id = int(query.data.split('_')[2])
    user_id = update.effective_user.id
    
    async with AsyncSessionLocal() as session:
        # Get Profile & User
        p_res = await session.execute(select(Profile).where(Profile.id == profile_id))
        profile = p_res.scalars().first()
        
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        if not profile or not user:
            if query:
                await query.edit_message_text(LanguageManager.get('buy.error_load'))
            return ConversationHandler.END

        if not await assert_purchasable_plan(session, profile):
            if query:
                await query.edit_message_text(LanguageManager.get('buy.error_load'))
            return ConversationHandler.END

        gate = await maybe_gate_terms(
            update,
            context,
            user,
            resume={"type": "buy_ovpn_confirm", "profile_id": profile_id},
        )
        if gate is not None:
            return gate
            
        context.user_data['selected_profile_id'] = profile_id
        
        coupon_id = coupon_id_from_context(context)
        pricing = await resolve_checkout_price(
            profile,
            is_wg=False,
            coupon_id=coupon_id,
            user_id=user.id,
            context="purchase_ovpn",
        )
        p_price = pricing.final_amount
        
        # Check Balance
        can_afford = user.wallet_balance >= p_price
        balance_Color = "✅" if can_afford else "❌"
        
        formatted_price = await format_currency(p_price)
        formatted_balance = await format_currency(user.wallet_balance)
        
        parse_mode = "Markdown"
        if pricing.discount_amount > 0:
            from html import escape

            from vpn_bot.coupon_flow import format_confirm_discount_line_html
            from vpn_bot.utils import markdown_bold_to_html

            text = markdown_bold_to_html(
                LanguageManager.get(
                    'buy.confirm_title',
                    plan=escape(profile.name),
                    price=formatted_price,
                    balance=formatted_balance,
                    icon=balance_Color,
                )
            )
            text += "\n" + format_confirm_discount_line_html(
                await format_currency(pricing.base_amount),
                await format_currency(pricing.discount_amount),
                formatted_price,
            )
            parse_mode = "HTML"
        else:
            text = LanguageManager.get('buy.confirm_title', 
                plan=escape_markdown(profile.name, version=1), 
                price=formatted_price, 
                balance=formatted_balance, 
                icon=balance_Color
            )
        
        keyboard = []
        if can_afford:
            text += LanguageManager.get('buy.confirm_instant')
            keyboard.append([InlineKeyboardButton(LanguageManager.get('buy.btn_confirm'), callback_data="confirm_pay")])
        else:
            needed = p_price - user.wallet_balance
            formatted_needed = await format_currency(needed)
            insuf = LanguageManager.get('buy.confirm_insufficient', needed=formatted_needed)
            if parse_mode == "HTML":
                insuf = markdown_bold_to_html(insuf).replace(
                    f"`{formatted_needed}`", f"<code>{escape(formatted_needed)}</code>"
                )
            text += insuf
            
            # Fast Pay Button (Pay exact difference)
            btn_fast_text = LanguageManager.get('buy.btn_fast_pay', needed=formatted_needed)
            keyboard.append([InlineKeyboardButton(btn_fast_text, callback_data=f"fast_pay_{needed}")])
            keyboard.append([InlineKeyboardButton(LanguageManager.get('buy.btn_topup'), callback_data="wallet_menu")])
            
        keyboard.append([InlineKeyboardButton(LanguageManager.get('common.cancel'), callback_data="buy_service")])
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=parse_mode)
        return BUY_PLAN_CONFIRM

@safe_response
async def process_purchase_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    
    if query.data.startswith('fast_pay_'):
        await query.answer()
        needed_amount = float(query.data.split('_')[2])
        profile_id = context.user_data.get('selected_profile_id')
        
        # Setup context for receipt
        context.user_data['topup_amount'] = needed_amount
        context.user_data['pending_plan_id'] = profile_id # Track pending plan
        context.user_data['pending_coupon_id'] = coupon_id_from_context(context)
        
        # Get payment cards from admin settings
        cards = await get_admin_setting('payment_cards', [])
        
        if not cards:
            await query.edit_message_text(
                LanguageManager.get('common.payment_info_missing'),
                parse_mode='Markdown'
            )
            return ConversationHandler.END
        
        # Build card display text
        card_text = ""
        keyboard = []
        
        for card in cards:
            number = card.get('number', '')
            holder = card.get('holder', '')
            bank = card.get('bank', '')
            card_text += LanguageManager.get('wallet.card_template', number=number, holder=holder, bank=bank)
        
        keyboard.append([InlineKeyboardButton(LanguageManager.get('wallet.btn_pay_help'), callback_data="payment_help")])
        
        # Format amount with currency
        formatted_amount = await format_currency(needed_amount)
        
        header = LanguageManager.get('wallet.pay_instruction_header', amount=formatted_amount)
        copy_prompt = LanguageManager.get('wallet.pay_copy_prompt')
        footer = LanguageManager.get('wallet.pay_instruction_footer')
        
        # Add Currency Warning if Toman/Rial
        curr_unit = await get_currency_unit()
        warning = ""
        if curr_unit in ['TOMAN', 'RIAL']:
            warning = LanguageManager.get('wallet.currency_warning', unit=curr_unit)
        
        text = f"{header}{card_text}\n{copy_prompt}{warning}{footer}"
        
        keyboard.append([InlineKeyboardButton(LanguageManager.get('common.cancel'), callback_data='buy_service')])
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return WALLET_RECEIPT

    if query.data == 'wallet_menu':
        # Redirect to wallet
        await wallet_menu(update, context)
        return WALLET_CUSTOM
    
    if query.data == 'confirm_pay':
        if await block_if_current_user_banned(update):
            return ConversationHandler.END
        from vpn_bot.utils import enforce_rate_limit
        if await enforce_rate_limit(update, seconds=3):
            return BUY_PLAN_CONFIRM

        profile_id = context.user_data.get('selected_profile_id')
        user_id = update.effective_user.id
        
        # Notify user that we are starting
        await query.edit_message_text(LanguageManager.get('buy.processing_wait'), parse_mode='Markdown')
        
        async def progress_callback(attempt):
            try:
                # Update message to show retry attempt
                msg = LanguageManager.get('buy.processing_retry', attempt=attempt)
                await query.edit_message_text(msg, parse_mode='Markdown')
            except Exception as e:
                logging.warning(f"Failed to update progress message: {e}")

        from vpn_bot.user_features import checkout_subscription
        coupon_id = coupon_id_from_context(context)
        success, sub, error_msg = await checkout_subscription(
            user_id, profile_id, progress_callback=progress_callback, coupon_id=coupon_id
        )
        
        if success and sub:
            clear_active_coupon(context.user_data)
            try:
                # Delivery logic
                from vpn_bot.user_features import send_config_files
                
                async with AsyncSessionLocal() as session:
                    # We need to fetch server and other details for delivery message
                    server = (await session.execute(select(Server).where(Server.id == sub.server_id))).scalars().first()
                    expiry = sub.expiry_date
                    username = sub.mikrotik_username
                    password = sub.mikrotik_password
                    
                    conn_info_all = await get_admin_setting('connection_info', {})
                    server_id_str = str(server.id)
                    s_info = conn_info_all.get(server_id_str, {
                        'l2tp': {'ip': server.host, 'secret': '123456', 'version': 'l2tp_v2'},
                        'sstp': {'ip': server.host, 'port': '443'}
                    })
                    
                    l2tp_host = s_info['l2tp'].get('ip') or server.host
                    l2tp_port = s_info['l2tp'].get('port', '1701')
                    l2tp_secret = s_info['l2tp'].get('secret', '123456')
                    l2tp_version = s_info['l2tp'].get('version', 'l2tp_v2')
                    l2tp_title = LanguageManager.get(f'config.{l2tp_version}_title')
                    l2tp_port_line = LanguageManager.get('config.l2tp_port_line', port=l2tp_port) if l2tp_version == 'l2tp_v3' else ""
                    
                    sstp_host = s_info['sstp'].get('ip') or server.host
                    sstp_port = s_info['sstp'].get('port', '443')
                    
                    delivery_text = LanguageManager.get('buy.success',
                        username=username,
                        password=password,
                        expiry=await format_datetime(expiry, include_time=False),
                        l2tp_title=l2tp_title,
                        l2tp_host=l2tp_host,
                        l2tp_port_line=l2tp_port_line,
                        l2tp_secret=l2tp_secret,
                        sstp_host=sstp_host,
                        sstp_port=sstp_port
                    )
                    
                    await query.edit_message_text(delivery_text, parse_mode='Markdown')
                    
                    # B. Send OVPN File
                    await send_config_files(context.bot, update.effective_chat.id, sub, server, session)
                    
                    # C. Send Main Menu Button after delivery
                    menu_keyboard = [[InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')]]
                    await context.bot.send_message(
                        update.effective_chat.id,
                        LanguageManager.get('buy.delivery_complete'),
                        reply_markup=InlineKeyboardMarkup(menu_keyboard),
                        parse_mode='Markdown'
                    )
                    context.user_data.pop(TERMS_SESSION_KEY, None)
            except Exception as del_err:
                logging.error(f"Delivery failed but account was created: {del_err}")
                menu_keyboard = [[InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')]]
                await query.message.reply_text(
                    LanguageManager.get('buy.delivery_fail'),
                    reply_markup=InlineKeyboardMarkup(menu_keyboard)
                )
            return ConversationHandler.END
        else:
            # Failure
            menu_keyboard = [[InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')]]
            await query.edit_message_text(error_msg or LanguageManager.get('buy.mt_error'), reply_markup=InlineKeyboardMarkup(menu_keyboard))
            return ConversationHandler.END
            
    await start(update, context)
    return ConversationHandler.END

# --- Buy WireGuard ---

@safe_response
async def buy_wg_service(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Initial step for buying WireGuard service."""
    # --- Maintenance Status Check ---
    from vpn_bot.utils import check_maintenance_status
    is_maint, maint_msg = await check_maintenance_status()
    if is_maint:
        if update.callback_query:
            await update.callback_query.message.edit_text(maint_msg, parse_mode='Markdown')
        else:
            await update.message.reply_text(maint_msg, parse_mode='Markdown')
        return ConversationHandler.END

    # --- Sales Status Check ---
    is_blocked, block_msg = await check_sales_status(update, context, 'wg')
    if is_blocked:
        from vpn_bot.utils import ensure_telegram_text

        block_msg = ensure_telegram_text(
            block_msg,
            fallback_key="admin.sales.capacity_full_default",
        )
        if update.callback_query:
            await update.callback_query.message.edit_text(block_msg, parse_mode='Markdown')
        else:
            await update.message.reply_text(block_msg, parse_mode='Markdown')
        return ConversationHandler.END

    if await block_if_current_user_banned(update):
        return ConversationHandler.END

    if await check_and_refresh_keyboard(update, context):
        return ConversationHandler.END

    # Registration Check
    context.user_data['reg_next'] = 'wg'
    reg_state = await check_user_registration(update, context)
    if reg_state:
        return reg_state
    if 'reg_next' in context.user_data:
        del context.user_data['reg_next']

    if not context.user_data.pop('_from_terms_resume', False):
        context.user_data.pop(TERMS_SESSION_KEY, None)
    db_user = await fetch_user_by_telegram(update.effective_user.id)
    gate = await maybe_gate_terms(update, context, db_user, resume={"type": "buy_wg"})
    if gate is not None:
        return gate

    profiles = await list_purchasable_wg_profiles()

    if not profiles:
        msg = LanguageManager.get('buy.no_plans')
        if update.callback_query:
            await update.callback_query.message.edit_text(msg, parse_mode='Markdown')
        else:
            await update.message.reply_text(msg, parse_mode='Markdown')
        return ConversationHandler.END

    context.user_data["_coupon_scope"] = "buy_wg"
    context.user_data["_coupon_resume"] = "buy_wg_plans"
    return await buy_wg_service_show_plans(update, context)


async def buy_wg_service_show_plans(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["_coupon_scope"] = "buy_wg"
    context.user_data["_coupon_resume"] = "buy_wg_plans"
    coupon_id = coupon_id_from_context(context)

    profiles = await list_purchasable_wg_profiles()

    async with AsyncSessionLocal() as session:
        u_res = await session.execute(
            select(User).where(User.telegram_id == update.effective_user.id)
        )
        db_user = u_res.scalars().first()

    if not profiles:
        msg = LanguageManager.get('buy.no_plans')
        await send_localized_text(update, msg, query=update.callback_query)
        return ConversationHandler.END

    use_html = bool(coupon_id)

    async def _wg_price(profile):
        if db_user and coupon_id:
            pricing = await resolve_checkout_price(
                profile, is_wg=True, coupon_id=coupon_id, user_id=db_user.id, context="purchase_wg"
            )
            base_f = await format_currency(pricing.base_amount)
            if pricing.discount_amount > 0:
                from vpn_bot.coupon_flow import format_discounted_price_display

                final_f = await format_currency(pricing.final_amount)
                return format_discounted_price_display(base_f, final_f)
            if use_html:
                from vpn_bot.coupon_flow import format_plain_price_html

                return format_plain_price_html(base_f)
            return base_f
        price = await format_currency(await get_wg_profile_price(profile))
        if use_html:
            from vpn_bot.coupon_flow import format_plain_price_html

            return format_plain_price_html(price)
        return price

    keyboard = []
    text = await build_plan_picker_message(
        "wg.buy_title",
        profiles,
        days_attr="duration_days",
        gb_attr="volume_gb",
        price_for_profile=_wg_price,
        html=use_html,
    )

    for p in profiles:
        label = format_plan_button_label(name=p.name, gb=p.volume_gb or 0)
        keyboard.append([InlineKeyboardButton(label, callback_data=f"buy_wg_plan_{p.id}")])

    if not coupon_id:
        keyboard.append([
            InlineKeyboardButton(
                LanguageManager.get('coupon.btn_enter'),
                callback_data='coupon_enter_inline_buy_wg',
            )
        ])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')])

    if not (text and text.strip()):
        text = LanguageManager.get('buy.no_plans')

    await send_localized_text(
        update,
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        query=update.callback_query,
        parse_mode="HTML" if use_html else "Markdown",
    )

    return BUY_PLAN_CONFIRM_WG

@safe_response
async def confirm_purchase_wg(update: Update, context: ContextTypes.DEFAULT_TYPE, plan_id: int | None = None):
    """Show details of the selected WireGuard plan and ask for confirmation."""
    query = update.callback_query
    if query:
        await query.answer()

    if plan_id is None:
        plan_id = int(query.data.split('_')[3])
    context.user_data['selected_wg_profile_id'] = plan_id
    
    async with AsyncSessionLocal() as session:
        from vpn_bot.models import WireGuardProfile
        p_res = await session.execute(select(WireGuardProfile).where(WireGuardProfile.id == plan_id))
        profile = p_res.scalars().first()
        
        if not profile:
            return ConversationHandler.END

        # --- Maintenance Status Check ---
        from vpn_bot.utils import check_maintenance_status
        is_maint, maint_msg = await check_maintenance_status(server_id=profile.server_id)
        if is_maint:
            await query.edit_message_text(maint_msg, parse_mode='Markdown')
            return ConversationHandler.END
        
        # Check user balance
        u_res = await session.execute(select(User).where(User.telegram_id == update.effective_user.id))
        user = u_res.scalars().first()

        if not profile or not user:
            await query.edit_message_text(LanguageManager.get('buy.error_load'))
            return ConversationHandler.END

        if not await assert_purchasable_plan(session, profile):
            await query.edit_message_text(LanguageManager.get('buy.error_load'))
            return ConversationHandler.END

        gate = await maybe_gate_terms(
            update,
            context,
            user,
            resume={"type": "buy_wg_confirm", "profile_id": plan_id},
        )
        if gate is not None:
            return gate
        
    if not profile or not user:
        await query.edit_message_text(LanguageManager.get('buy.error_load'))
        return ConversationHandler.END

    coupon_id = coupon_id_from_context(context)
    async with AsyncSessionLocal() as session:
        pricing = await resolve_checkout_price(
            profile,
            is_wg=True,
            coupon_id=coupon_id,
            user_id=user.id,
            context="purchase_wg",
        )
    price = pricing.final_amount
    needed = max(0, price - user.wallet_balance)
    can_afford = user.wallet_balance >= price
    
    formatted_price = await format_currency(price)
    formatted_balance = await format_currency(user.wallet_balance)
    formatted_needed = await format_currency(needed)
    
    parse_mode = "Markdown"
    if pricing.discount_amount > 0:
        from html import escape

        from vpn_bot.coupon_flow import format_confirm_discount_line_html
        from vpn_bot.utils import markdown_bold_to_html

        text = (
            markdown_bold_to_html(LanguageManager.get('wg.buy_confirm_title'))
            + LanguageManager.get('wg.buy_plan_label', name=escape(profile.name)) + "\n"
            + LanguageManager.get('wg.buy_price_label', price=formatted_price) + "\n"
            + LanguageManager.get('wg.buy_balance_label', balance=formatted_balance) + "\n"
        )
        text += format_confirm_discount_line_html(
            await format_currency(pricing.base_amount),
            await format_currency(pricing.discount_amount),
            formatted_price,
        ) + "\n"
        parse_mode = "HTML"
    else:
        text = (
            LanguageManager.get('wg.buy_confirm_title')
            + LanguageManager.get('wg.buy_plan_label', name=profile.name) + "\n"
            + LanguageManager.get('wg.buy_price_label', price=formatted_price) + "\n"
            + LanguageManager.get('wg.buy_balance_label', balance=formatted_balance) + "\n"
        )
    
    keyboard = []
    if can_afford:
        text += LanguageManager.get('wg.buy_can_afford')
        keyboard.append([InlineKeyboardButton(LanguageManager.get('common.confirm'), callback_data="confirm_pay_wg")])
    else:
        text += LanguageManager.get('wg.buy_insufficient', needed=formatted_needed)
        keyboard.append([InlineKeyboardButton(LanguageManager.get('wg.btn_fast_pay', amount=formatted_needed), callback_data=f"fast_pay_wg_{needed}")])
        keyboard.append([InlineKeyboardButton(LanguageManager.get('menu.wallet'), callback_data="wallet_menu")])
        
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data="buy_wg")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=parse_mode)
    return BUY_PLAN_CONFIRM_WG

@safe_response
async def process_purchase_flow_wg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle the final payment and delivery for WireGuard."""
    query = update.callback_query
    
    if query.data.startswith('fast_pay_wg_'):
        # Similar to OVPN fast pay but for WG
        await query.answer()
        needed_amount = float(query.data.split('_')[3])
        profile_id = context.user_data.get('selected_wg_profile_id')
        
        context.user_data['topup_amount'] = needed_amount
        context.user_data['pending_wg_plan_id'] = profile_id
        context.user_data['pending_coupon_id'] = coupon_id_from_context(context)
        
        cards = await get_admin_setting('payment_cards', [])
        if not cards:
            await query.edit_message_text(LanguageManager.get('common.payment_info_missing'))
            return ConversationHandler.END
            
        # ... logic to show cards (can reuse logic from process_purchase_flow OR call it with adapted context)
        # For now, let's keep it simple and just use the same card display logic
        card_text = ""
        for card in cards:
            card_text += LanguageManager.get('wallet.card_template', number=card.get('number'), holder=card.get('holder'), bank=card.get('bank'))
            
        formatted_amount = await format_currency(needed_amount)
        header = LanguageManager.get('wallet.pay_instruction_header', amount=formatted_amount)
        text = f"{header}{card_text}\n{LanguageManager.get('wallet.pay_instruction_footer')}"
        
        keyboard = [[InlineKeyboardButton(LanguageManager.get('common.cancel'), callback_data='buy_wg')]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return WALLET_RECEIPT

    if query.data == 'confirm_pay_wg':
        if await block_if_current_user_banned(update):
            return ConversationHandler.END
        from vpn_bot.utils import enforce_rate_limit
        if await enforce_rate_limit(update, seconds=3):
            return BUY_PLAN_CONFIRM_WG

        await query.edit_message_text(LanguageManager.get('buy.processing_wait'), parse_mode='Markdown')
        profile_id = context.user_data.get('selected_wg_profile_id')
        user_tg_id = update.effective_user.id
        
        async def progress_callback(attempt):
            try:
                msg = LanguageManager.get('buy.processing_retry', attempt=attempt)
                await query.edit_message_text(msg, parse_mode='Markdown')
            except Exception:
                pass

        from vpn_bot.user_features import finalize_wg_purchase
        success = await finalize_wg_purchase(
            user_tg_id, profile_id, context, is_tg_id=True, progress_callback=progress_callback
        )
        menu_keyboard = [[InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')]]
        if success:
            clear_active_coupon(context.user_data)
            from vpn_bot.utils import ensure_telegram_text

            done_text = ensure_telegram_text(
                LanguageManager.get('buy.delivery_complete'),
                fallback_key='buy.delivery_complete',
            )
            try:
                await query.edit_message_text(
                    done_text,
                    reply_markup=InlineKeyboardMarkup(menu_keyboard),
                    parse_mode='Markdown',
                )
            except Exception:
                pass
            context.user_data.pop(TERMS_SESSION_KEY, None)
            return ConversationHandler.END

        await query.edit_message_text(
            LanguageManager.get('buy.mt_error'),
            reply_markup=InlineKeyboardMarkup(menu_keyboard),
            parse_mode='Markdown',
        )
        return ConversationHandler.END

async def main_menu_text_dispatch(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Dispatch text-based main menu button clicks to their handlers."""
    if not update.message or not update.message.text:
        return ConversationHandler.END

    text = update.message.text
    
    # Map button text to functions
    if text in LanguageManager.get_all_translations_raw('menu.buy_service'):
        await buy_service(update, context)
    elif text in LanguageManager.get_all_translations_raw('menu.buy_wg'):
        await buy_wg_service(update, context)
    elif text in LanguageManager.get_all_translations_raw('menu.wallet'):
        await wallet_menu(update, context)
    elif text in LanguageManager.get_all_translations_raw('menu.my_subs'):
        await my_subscriptions(update, context)
    elif text in LanguageManager.get_all_translations_raw('menu.tutorials'):
        from vpn_bot.user_features import tutorials_menu
        await tutorials_menu(update, context)
    elif text in LanguageManager.get_all_translations_raw('menu.history'):
        from vpn_bot.user_features import purchase_history
        await purchase_history(update, context)
    elif text in LanguageManager.get_all_translations_raw('menu.support'):
        from vpn_bot.support_tickets import support_menu
        await support_menu(update, context)
    elif text in LanguageManager.get_all_translations_raw('menu.settings'):
        from vpn_bot.admin_management import is_user_admin
        if await is_user_admin(update.effective_user.id):
             from vpn_bot.admin_panel import admin_start
             await admin_start(update, context)
        else:
             await start(update, context)
    else:
        # Fallback to main menu if text doesn't match but filter did
        await start(update, context)
        
    return ConversationHandler.END

# --- Wallet Conversation ---


async def check_user_registration(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Helper to check if user is registered (has phone number).
    If not, sends reg welcome and returns REG_NAME.
    """
    user_id = update.effective_user.id
    query = update.callback_query
    
    async with AsyncSessionLocal() as session:
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        if not user or not user.phone_number:
            # --- Maintenance Status Check ---
            from vpn_bot.utils import check_maintenance_status
            is_maint, maint_msg = await check_maintenance_status()
            if is_maint:
                if query: await query.message.edit_text(maint_msg, parse_mode='Markdown')
                else: await update.message.reply_text(maint_msg, parse_mode='Markdown')
                return ConversationHandler.END
            
            text = await get_admin_setting('reg_welcome_msg', LanguageManager.get('buy.reg_welcome'))
            if query:
                await query.message.edit_text(text, parse_mode='Markdown')
            else:
                await update.message.reply_text(text, parse_mode='Markdown')
            return REG_NAME
            
    return None

# Common states shared by multiple handlers
COMMON_REG_STATES = {
    REG_NAME: [MessageHandler(filters.TEXT & (~filters.COMMAND) & (~MENU_BUTTONS_FILTER), receive_name)],
    REG_PHONE: [
        MessageHandler(filters.CONTACT, receive_phone),
        MessageHandler(filters.TEXT & (~filters.COMMAND) & (~MENU_BUTTONS_FILTER), receive_phone_manual_warning)
    ],
}

# --- Wallet Conversation ---

wallet_handler = ConversationHandler(
    entry_points=[
        CommandHandler('start', start),
        CallbackQueryHandler(wallet_menu, pattern='^wallet_menu$'),
        CallbackQueryHandler(topup_amount_selected, pattern='^topup_\\d+$'),
        CallbackQueryHandler(topup_custom_amount, pattern='^topup_custom$'),
        MessageHandler(filters.Regex(f"^({'|'.join(LanguageManager.get_all_translations('menu.wallet'))})$"), wallet_menu)
    ],
    states={
        **COMMON_REG_STATES,
        WALLET_CUSTOM: [
            *conv_control_handlers(start),
            CallbackQueryHandler(topup_amount_selected, pattern='^topup_\\d+$'),
            CallbackQueryHandler(topup_custom_amount, pattern='^topup_custom$'),
            CallbackQueryHandler(start, pattern='^main_menu$'),
            MessageHandler(filters.TEXT & (~filters.COMMAND) & (~MENU_BUTTONS_FILTER), receive_custom_amount)
        ],
        WALLET_RECEIPT: [
            *conv_control_handlers(start),
            MessageHandler(filters.PHOTO | filters.Document.ALL | (filters.TEXT & (~filters.COMMAND) & (~MENU_BUTTONS_FILTER)), receive_receipt),
            CallbackQueryHandler(payment_help_callback, pattern='^payment_help$'),
            CallbackQueryHandler(wallet_menu, pattern='^wallet_menu$')
        ],
        WALLET_CONFIRM: [
            *conv_control_handlers(start),
            CallbackQueryHandler(confirm_receipt, pattern='^receipt_(yes|no)$')
        ]
    },
    fallbacks=[
        CommandHandler('start', start),
        *legacy_cancel_handlers(start),
        CallbackQueryHandler(start, pattern='^main_menu$'),
        MessageHandler(MENU_BUTTONS_FILTER, main_menu_text_dispatch)
    ],
    allow_reentry=True
)

async def set_receipt_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the current group as receipt notification group (super admin + Telegram group admin only)."""
    from telegram import ReplyKeyboardMarkup
    from vpn_bot.admin_management import is_super_admin
    from vpn_bot.admin_permissions import is_telegram_group_admin
    from vpn_bot.admin_settings_service import set_receipt_group_id

    user_id = update.effective_user.id
    if not await is_super_admin(user_id):
        await update.message.reply_text(LanguageManager.get('admin.receipt_group.set_fail_admin'))
        return

    group_id = update.effective_chat.id
    if update.effective_chat.type not in ['group', 'supergroup']:
        return

    if not await is_telegram_group_admin(context.bot, group_id, user_id):
        await update.message.reply_text(LanguageManager.get('admin.receipt_group.set_fail_group_admin'))
        return

    await set_receipt_group_id(group_id)

    keyboard = [[LanguageManager.get('admin.receipt_group.menu_pending')]]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        LanguageManager.get('admin.receipt_group.set_success'),
        reply_markup=reply_markup,
    )


async def set_backup_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the current group as the auto-backup group (super admin + Telegram group admin only)."""
    from telegram import ReplyKeyboardMarkup
    from vpn_bot.admin_management import is_super_admin
    from vpn_bot.admin_permissions import is_telegram_group_admin

    user_id = update.effective_user.id
    if not await is_super_admin(user_id):
        await update.message.reply_text(LanguageManager.get('admin.support_group.set_fail_admin'))
        return

    group_id = update.effective_chat.id
    if update.effective_chat.type not in ['group', 'supergroup']:
        return

    if not await is_telegram_group_admin(context.bot, group_id, user_id):
        await update.message.reply_text(LanguageManager.get('admin.support_group.set_fail_group_admin'))
        return

    # Persist the change to .env
    dotenv_file = dotenv.find_dotenv()
    if dotenv_file:
        dotenv.set_key(dotenv_file, "BACKUP_GROUP_ID", str(group_id))
    
    # Update config runtime
    config.BACKUP_GROUP_ID = str(group_id)

    # Show a dedicated backup group menu (similar to support group)
    keyboard = [
        [LanguageManager.get('admin.backup_group.menu_backup')],
        [LanguageManager.get('admin.backup_group.menu_manual')],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        LanguageManager.get('admin.backup_group.set_success'),
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

# Purchase Handler
buy_handler = ConversationHandler(
    entry_points=[
        CommandHandler('start', start),
        CallbackQueryHandler(buy_service, pattern='^buy_service$'),
        CallbackQueryHandler(confirm_purchase, pattern='^buy_plan_'),
        MessageHandler(filters.Regex(f"^({'|'.join(LanguageManager.get_all_translations('menu.buy_service'))})$"), buy_service),
        CallbackQueryHandler(buy_wg_service, pattern='^buy_wg$'),
        CallbackQueryHandler(confirm_purchase_wg, pattern='^buy_wg_plan_'),
        MessageHandler(filters.Regex(f"^({'|'.join(LanguageManager.get_all_translations('menu.buy_wg'))})$"), buy_wg_service)
    ],

    states={
        **COMMON_REG_STATES,
        TERMS_ACCEPT: [
            CallbackQueryHandler(handle_terms_accept, pattern='^terms_accept$'),
            CallbackQueryHandler(handle_terms_decline, pattern='^terms_decline$'),
        ],
        COUPON_PROMPT: [
            CallbackQueryHandler(coupon_skip, pattern='^coupon_skip$'),
            CallbackQueryHandler(coupon_enter_start, pattern='^coupon_enter$'),
        ],
        COUPON_ENTRY: [
            *conv_control_handlers(start),
            CallbackQueryHandler(coupon_skip, pattern='^coupon_skip$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, receive_coupon_code),
        ],
        BUY_PLAN_CONFIRM: [
            CallbackQueryHandler(confirm_purchase, pattern='^buy_plan_'),
            CallbackQueryHandler(process_purchase_flow, pattern='^(confirm_pay|wallet_menu|fast_pay_.*)$'),
            CallbackQueryHandler(buy_service, pattern='^buy_service$'),
            CallbackQueryHandler(what_is_multi_proto, pattern='^what_is_multi$'),
            CallbackQueryHandler(coupon_enter_inline, pattern='^coupon_enter_inline_buy_ovpn$'),
        ],
        BUY_PLAN_CONFIRM_WG: [
            CallbackQueryHandler(confirm_purchase_wg, pattern='^buy_wg_plan_'),
            CallbackQueryHandler(process_purchase_flow_wg, pattern='^(confirm_pay_wg|wallet_menu|fast_pay_wg_.*)$'),
            CallbackQueryHandler(buy_wg_service, pattern='^buy_wg$'),
            CallbackQueryHandler(coupon_enter_inline, pattern='^coupon_enter_inline_buy_wg$'),
        ],
        # Integrated wallet states for seamless top-up from purchase screen
        WALLET_CUSTOM: [
            *conv_control_handlers(start),
            CallbackQueryHandler(topup_amount_selected, pattern='^topup_\\d+$'),
            CallbackQueryHandler(topup_custom_amount, pattern='^topup_custom$'),
            MessageHandler(filters.TEXT & (~filters.COMMAND) & (~MENU_BUTTONS_FILTER), receive_custom_amount)
        ],
        WALLET_RECEIPT: [
            *conv_control_handlers(start),
            MessageHandler(filters.PHOTO | filters.Document.ALL | (filters.TEXT & (~filters.COMMAND) & (~MENU_BUTTONS_FILTER)), receive_receipt),
            CallbackQueryHandler(payment_help_callback, pattern='^payment_help$'),
            CallbackQueryHandler(wallet_menu, pattern='^wallet_menu$')
        ],
        WALLET_CONFIRM: [
            *conv_control_handlers(start),
            CallbackQueryHandler(confirm_receipt, pattern='^receipt_(yes|no)$')
        ]
    },

    fallbacks=[
        CommandHandler('start', start),
        *legacy_cancel_handlers(start),
        CallbackQueryHandler(start, pattern='^main_menu$'),
        MessageHandler(MENU_BUTTONS_FILTER, main_menu_text_dispatch)
    ],
    allow_reentry=True
)

# Listeners for main menu
# Note: start is command, others are callbacks.
# We need a way to link "My Subs" etc.
# Ideally, we have a main CallbackQueryHandler in main.py or here that dispatches.


def _main_menu_callback_handles(data: str) -> bool:
    """True only for user-menu callbacks this handler dispatches (not admin/WG)."""
    if not data:
        return False
    if data in (
        "main_menu",
        "my_subs",
        "buy_service",
        "buy_wg",
        "history",
        "support",
        "tutorials",
        "download_apps",
        "admin_reports",
        "report_export_sales",
        "my_wallet",
        "wallet_menu",
        "settings_lang",
    ):
        return True
    prefixes = (
        "view_sub_",
        "view_wg_sub_",
        "history_",
        "tutorial_",
        "renew_wg_confirm_",
        "renew_wg_",
        "renew_confirm_",
        "renew_",
        "topup_",
        "get_config_",
        "get_wg_conf_",
        "get_conn_info_",
        "get_ovpn_file_",
        "dl_ovpn_",
        "dl_info_",
        "set_lang_",
    )
    return data.startswith(prefixes)


@safe_response
async def main_menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data or ""

    if not _main_menu_callback_handles(data):
        try:
            await query.answer()
        except Exception:
            pass
        return

    if data == 'main_menu':
        await start(update, context)
    elif data == 'my_subs':
        await my_subscriptions(update, context)
    elif data.startswith('view_sub_'):
        await view_subscription_detail(update, context)
    elif data.startswith('view_wg_sub_'):
        await view_wg_subscription_detail(update, context)
    elif data == 'buy_service':
        await buy_service(update, context)
    elif data == 'buy_wg':
        await buy_wg_service(update, context)
    elif data == 'history':
        from vpn_bot.user_features import purchase_history
        await purchase_history(update, context)
    elif data.startswith('history_'):
        from vpn_bot.user_features import history_navigate
        await history_navigate(update, context)
    elif query.data == 'my_wallet':
        from vpn_bot.bot_handler import wallet_menu
        return await wallet_menu(update, context)
    
    elif data == 'support':
        from vpn_bot.support_tickets import support_menu
        await support_menu(update, context)
    elif data == 'tutorials':
        from vpn_bot.user_features import tutorials_menu
        await tutorials_menu(update, context)
    elif data.startswith('tutorial_'):
        from vpn_bot.user_features import show_tutorial
        await show_tutorial(update, context)
    
    # WireGuard Renewals
    elif query.data.startswith('renew_wg_confirm_'):
        from vpn_bot.user_features import confirm_wg_renewal
        return await confirm_wg_renewal(update, context)
    elif query.data.startswith('renew_wg_'):
        from vpn_bot.user_features import renew_wg_subscription
        return await renew_wg_subscription(update, context)
    
    # Generic Renewals (OpenVPN)
    elif query.data.startswith('renew_confirm_'):
        from vpn_bot.user_features import confirm_renewal
        return await confirm_renewal(update, context)
    elif query.data.startswith('renew_'):
        from vpn_bot.user_features import renew_subscription
        return await renew_subscription(update, context)
    elif data == 'download_apps':
        from vpn_bot.user_features import download_apps_menu
        await download_apps_menu(update, context)
    elif data == 'admin_reports':
        from vpn_bot.admin_reports import sales_dashboard
        await sales_dashboard(update, context)
    elif data == 'report_export_sales':
        from vpn_bot.admin_reports import export_sales_csv
        await export_sales_csv(update, context)
    elif data == 'wallet_menu' or data.startswith('topup_'):
        await wallet_menu(update, context)

    elif data.startswith('get_config_'):
        sub_id = int(data.split('_')[2])
        await show_config_submenu(update, context, sub_id)

    elif data.startswith('get_wg_conf_'):
        await send_wg_config_again(update, context)

    elif data.startswith('get_conn_info_'):
        await view_subscription_connection_info(update, context)

    elif data.startswith('get_ovpn_file_'):
        await view_subscription_ovpn_file(update, context)

    elif data.startswith('dl_ovpn_'):
        # dl_ovpn_{sub_id}_{ovpn_id}
        parts = data.split('_')
        sub_id = int(parts[2])
        ovpn_id = int(parts[3])
        sub = await get_owned_ovpn_sub(sub_id, update.effective_user.id)
        if not sub:
            await query.answer(LanguageManager.get('config.sub_not_found'), show_alert=True)
            return
        async with AsyncSessionLocal() as session:
            ovpn = await session.get(OvpnConfig, ovpn_id)
            if ovpn:
                await query.answer(f"Sending {ovpn.display_name or 'Config'}...")
                await send_ovpn_file(context.bot, update.effective_chat.id, sub, ovpn)
            else:
                await query.answer(LanguageManager.get('common.error_not_found'), show_alert=True)

    elif data.startswith('dl_info_'):
        # dl_info_{sub_id}
        sub_id = int(data.split('_')[2])
        sub = await get_owned_ovpn_sub(sub_id, update.effective_user.id)
        if not sub:
            await query.answer(LanguageManager.get('config.sub_not_found'), show_alert=True)
            return
        async with AsyncSessionLocal() as session:
            srv = await session.get(Server, sub.server_id)
            if srv:
                await query.answer(LanguageManager.get('common.sending_info'))
                await send_connection_info(context.bot, update.effective_chat.id, sub, srv)
            else:
                await query.answer(LanguageManager.get('common.error_not_found'), show_alert=True)
    
    # Language settings handlers (work after conversation ends)
    elif data == 'settings_lang':
        from vpn_bot.admin_settings import language_menu
        await language_menu(update, context)
    elif data.startswith('set_lang_'):
        from vpn_bot.admin_settings import set_language_callback
        await set_language_callback(update, context)
    
    elif data.startswith('renew_'):
        from vpn_bot.user_features import renew_subscription, confirm_renewal
        if 'confirm' in data:
            await confirm_renewal(update, context)
        else:
            await renew_subscription(update, context)

