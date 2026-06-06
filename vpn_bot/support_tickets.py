"""
Support Ticket System
Handles user support requests and admin responses.
"""

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, ReplyKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from sqlalchemy import select, desc, and_
from datetime import datetime

from vpn_bot.models import User, Ticket, TicketMessage, AdminSetting
from vpn_bot.config import config
from vpn_bot.utils import LanguageManager, rate_limit, safe_response
from vpn_bot.bot_handler import MENU_BUTTONS_FILTER, main_menu_text_dispatch, REG_NAME, REG_PHONE, receive_name, receive_phone, receive_phone_manual_warning
from vpn_bot.conversation_controls import (
    CANCEL_CALLBACK,
    conv_control_handlers,
    conv_markup,
    is_conv_cancel,
    legacy_cancel_handlers,
    merge_markup,
    reply_conv_prompt,
)

# States
TICKET_SUBJECT, TICKET_MESSAGE, TICKET_REPLY = range(3)

logger = logging.getLogger("vpn_bot.support_tickets")

from vpn_bot.admin_settings_service import get_support_group_id, set_support_group_id
from vpn_bot.admin_ticket_service import (
    get_ticket_notif_mode, get_open_ticket_count, get_user_tickets,
    create_ticket_from_user, get_ticket_comprehensive, add_ticket_message,
    close_ticket
)
from vpn_bot.admin_user_service import get_user_by_tg_id

async def set_support_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Set the current group as support group (super admin + Telegram group admin only)."""
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

    await set_support_group_id(group_id)

    # Show the persistent menu
    keyboard = [
        [LanguageManager.get('admin.support_group.menu_active')],
        [LanguageManager.get('admin.support_group.menu_search'), LanguageManager.get('admin.support_group.menu_settings')]
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

    await update.message.reply_text(
        LanguageManager.get('admin.support_group.set_success'),
        reply_markup=reply_markup
    )

# --- User Ticket Functions ---

@safe_response
async def support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Support center main menu."""
    query = update.callback_query
    if query:
        await query.answer()
    
    user_id = update.effective_user.id
    from vpn_bot.utils import LanguageManager
    
    user = await get_user_by_tg_id(user_id)

    if not user:
        msg = LanguageManager.get('ticket.user_not_found')
        if query: await query.edit_message_text(msg)
        else: await update.message.reply_text(msg)
        return ConversationHandler.END

    if user.is_banned:
        msg = LanguageManager.get('user.account_banned')
        if query:
            await query.answer(msg, show_alert=True)
        else:
            await update.message.reply_text(msg)
        return ConversationHandler.END

    # Check registration
    if not user.phone_number:
        msg = LanguageManager.get('buy.reg_welcome') 
        if query: await query.message.reply_text(msg, parse_mode='Markdown')
        else: await update.message.reply_text(msg, parse_mode='Markdown')
        context.user_data['reg_next'] = 'support'
        return REG_NAME

    open_tickets = await get_open_ticket_count(user.id)
    
    # Get support hours from admin settings (optional)
    from vpn_bot.admin_settings import get_admin_setting
    support_text = await get_admin_setting('support_hours_text', 
        LanguageManager.get('ticket.menu_default_text'))
    
    text = LanguageManager.get('ticket.menu_title', text=support_text, count=open_tickets)
    
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('ticket.btn_create'), callback_data='ticket_create')],
        [InlineKeyboardButton(LanguageManager.get('ticket.btn_list'), callback_data='ticket_list')],
        [InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    return ConversationHandler.END

async def create_ticket_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start ticket creation with subject selection buttons."""
    query = update.callback_query

    user = await get_user_by_tg_id(update.effective_user.id)
    if user and user.is_banned:
        msg = LanguageManager.get('user.account_banned')
        await query.answer(msg, show_alert=True)
        return ConversationHandler.END

    await query.answer()

    # --- Maintenance Check ---
    from vpn_bot.utils import check_maintenance_status
    is_maint, maint_msg = await check_maintenance_status()
    if is_maint:
        await query.edit_message_text(maint_msg, parse_mode='Markdown')
        return ConversationHandler.END
    
    # Get subjects from admin settings (with defaults)
    from vpn_bot.admin_settings import get_admin_setting
    
    custom_subjects = await get_admin_setting('ticket_subjects', [])
    
    # Default subjects from locales
    default_subjects = LanguageManager.get('ticket.default_subjects')
    # If returned string (key missing), fallback to localized list
    if isinstance(default_subjects, str):
        default_subjects = [
            f"🔌 {LanguageManager.get('ticket.label_conn_issues')}",
            f"💰 {LanguageManager.get('ticket.label_payment_issues')}",
            f"📱 {LanguageManager.get('ticket.label_app_help')}",
            f"🔄 {LanguageManager.get('ticket.label_renewal_req')}",
            f"❓ {LanguageManager.get('ticket.label_general_ques')}"
        ]
    
    all_subjects = custom_subjects if custom_subjects else default_subjects
    
    keyboard = []
    for idx, subject in enumerate(all_subjects):
        keyboard.append([InlineKeyboardButton(subject, callback_data=f'ticket_subj_{idx}')])
    
    keyboard.append([
        InlineKeyboardButton(LanguageManager.get('ticket.btn_custom'), callback_data='ticket_custom_subject'),
    ])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.cancel_operation'), callback_data=CANCEL_CALLBACK)])
    
    await query.edit_message_text(
        LanguageManager.get('ticket.create_title'),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return TICKET_SUBJECT

async def select_ticket_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle subject button selection."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    from vpn_bot.utils import LanguageManager
    
    if data == 'ticket_custom_subject':
        await reply_conv_prompt(update, LanguageManager.get('ticket.custom_prompt'))
        return TICKET_SUBJECT
    
    # Get subjects list
    from vpn_bot.admin_settings import get_admin_setting
    custom_subjects = await get_admin_setting('ticket_subjects', [])
    
    default_subjects = LanguageManager.get('ticket.default_subjects')
    if not isinstance(default_subjects, list):
        default_subjects = [
            "🔌 Connection Issues",
            "💰 Payment Problem",
            "📱 App Help",
            "🔄 Renewal Request",
            "❓ General Question"
        ]

    all_subjects = custom_subjects if custom_subjects else default_subjects
    
    idx = int(data.split('_')[2])
    if 0 <= idx < len(all_subjects):
        subject = all_subjects[idx]
        context.user_data['ticket_subject'] = subject
        
        await reply_conv_prompt(
            update,
            LanguageManager.get('ticket.subject_selected', subject=subject),
        )
        return TICKET_MESSAGE
    else:
        await query.edit_message_text(LanguageManager.get('common.invalid_selection'))
        return ConversationHandler.END

async def receive_ticket_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive custom ticket subject via text."""
    if is_conv_cancel(update):
        return await cancel_ticket(update, context)
    subject = update.message.text
    from vpn_bot.utils import LanguageManager
    
    if len(subject) < 5:
        await update.message.reply_text(
            LanguageManager.get('ticket.valid_short'), reply_markup=conv_markup()
        )
        return TICKET_SUBJECT

    if len(subject) > 200:
        await update.message.reply_text(
            LanguageManager.get('ticket.valid_long'), reply_markup=conv_markup()
        )
        return TICKET_SUBJECT

    context.user_data['ticket_subject'] = subject

    await reply_conv_prompt(
        update, LanguageManager.get('ticket.subject_selected', subject=subject)
    )
    return TICKET_MESSAGE

@rate_limit(seconds=5)
async def receive_ticket_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive ticket message and create ticket."""
    if is_conv_cancel(update):
        return await cancel_ticket(update, context)
    user_id = update.effective_user.id
    message_text = update.message.text or update.message.caption or ""
    attachment_file_id = None
    attachment_type = None
    from vpn_bot.utils import LanguageManager
    
    if update.message.photo:
        attachment_file_id = update.message.photo[-1].file_id
        attachment_type = 'photo'
    elif update.message.video:
        attachment_file_id = update.message.video.file_id
        attachment_type = 'video'
    elif update.message.document:
        attachment_file_id = update.message.document.file_id
        attachment_type = 'document'
    
    if not message_text and not attachment_file_id:
        await update.message.reply_text(
            LanguageManager.get('ticket.msg_prompt'), reply_markup=conv_markup()
        )
        return TICKET_MESSAGE
    
    subject = context.user_data.get('ticket_subject', LanguageManager.get('common.na'))
    
    user = await get_user_by_tg_id(user_id)
    if not user:
        await update.message.reply_text(LanguageManager.get('ticket.user_not_found'))
        return ConversationHandler.END
    
    ticket_id = await create_ticket_from_user(user.id, subject, message_text, attachment_file_id, attachment_type)
    
    # Notify all admins
    await notify_admins_new_ticket(update.get_bot(), ticket_id, user_id, subject, message_text, attachment_file_id, attachment_type)
    
    await update.message.reply_text(
        LanguageManager.get('ticket.msg_created', id=ticket_id, subject=subject),
        parse_mode='Markdown'
    )
    
    context.user_data.clear()
    return await view_ticket(update, context, ticket_id=ticket_id)

async def notify_admins_new_ticket(bot, ticket_id, user_telegram_id, subject, message, attachment_file_id, attachment_type=None):
    """Notify all admins about new ticket (Group or PV)."""
    title = LanguageManager.get('ticket.notify_new_title', id=ticket_id)
    pv_body = LanguageManager.get(
        'ticket.notify_new_body', tg_id=user_telegram_id, subject=subject, message=message[:500]
    )
    group_body = LanguageManager.get(
        'ticket.notify_new_body_group', subject=subject, message=message[:500]
    )

    keyboard = [[InlineKeyboardButton(LanguageManager.get('ticket.btn_view_ticket'), callback_data=f'admin_ticket_{ticket_id}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    mode = await get_ticket_notif_mode()
    group_id = await get_support_group_id()

    if mode == 'group' and group_id:
        dest_ids = [group_id]
        to_group = True
    else:
        from vpn_bot.admin_permissions import PERM_TICKETS, get_admins_for_permission
        dest_ids = await get_admins_for_permission(PERM_TICKETS)
        to_group = False

    for dest_id in dest_ids:
        text = f"{title}{group_body if to_group else pv_body}"
        logger.debug("notify_admins_new_ticket ticket=%s dest=%s group=%s", ticket_id, dest_id, to_group)
        try:
            if attachment_file_id:
                if attachment_type == 'photo':
                    await bot.send_photo(chat_id=dest_id, photo=attachment_file_id, caption=text, reply_markup=reply_markup, parse_mode='Markdown')
                elif attachment_type == 'video':
                    await bot.send_video(chat_id=dest_id, video=attachment_file_id, caption=text, reply_markup=reply_markup, parse_mode='Markdown')
                elif attachment_type == 'audio':
                    await bot.send_audio(chat_id=dest_id, audio=attachment_file_id, caption=text, reply_markup=reply_markup, parse_mode='Markdown')
                elif attachment_type == 'voice':
                    await bot.send_voice(chat_id=dest_id, voice=attachment_file_id, caption=text, reply_markup=reply_markup, parse_mode='Markdown')
                else:
                    await bot.send_document(chat_id=dest_id, document=attachment_file_id, caption=text, reply_markup=reply_markup, parse_mode='Markdown')
            else:
                await bot.send_message(
                    chat_id=dest_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
        except Exception as e:
            logger.warning("Failed to notify dest %s for ticket %s: %s", dest_id, ticket_id, e)

async def my_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display user's tickets."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    from vpn_bot.utils import LanguageManager
    
    user = await get_user_by_tg_id(user_id)
    if not user:
        await query.edit_message_text(LanguageManager.get('ticket.user_not_found'))
        return ConversationHandler.END
    
    tickets = await get_user_tickets(user.id)
    
    if not tickets:
        text = LanguageManager.get('ticket.list_empty')
        keyboard = [
            [InlineKeyboardButton(LanguageManager.get('ticket.btn_create'), callback_data='ticket_create')],
            [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='support')]
        ]
    else:
        text = LanguageManager.get('ticket.list_title')
        keyboard = []
        
        # Group by status
        open_tickets = [t for t in tickets if t.status != 'closed']
        closed_tickets = [t for t in tickets if t.status == 'closed']
        
        if open_tickets:
            text += LanguageManager.get('ticket.open_title')
            for ticket in open_tickets[:5]:  # Show last 5 open
                status_emoji = "⏳" if ticket.status == 'waiting_admin' else "💬"
                text += (
                    LanguageManager.get('ticket.card_open_header', id=ticket.id, icon=status_emoji) +
                    f"{LanguageManager.get('ticket.label_subject')}: {ticket.subject[:50]}\\n"
                    f"{LanguageManager.get('ticket.label_status')}: {LanguageManager.get(f'status.{ticket.status}')}\\n"
                    f"{LanguageManager.get('ticket.label_created')}: {ticket.created_at.strftime('%Y-%m-%d %H:%M')}\\n"
                    f"━━━━━━━━━━\\n"
                )
                keyboard.append([InlineKeyboardButton(LanguageManager.get('ticket.btn_view', id=ticket.id), callback_data=f'ticket_view_{ticket.id}')])
        
        if closed_tickets:
            text += LanguageManager.get('ticket.closed_title')
            for ticket in closed_tickets[:3]:  # Show last 3 closed
                text += (
                    LanguageManager.get('ticket.card_closed_header', id=ticket.id) +
                    f"{LanguageManager.get('ticket.label_subject')}: {ticket.subject[:50]}\\n"
                    f"{LanguageManager.get('ticket.label_closed')}: {ticket.closed_at.strftime('%Y-%m-%d') if ticket.closed_at else LanguageManager.get('common.na')}\\n"
                    f"━━━━━━━━━━\\n"
                )
                keyboard.append([InlineKeyboardButton(LanguageManager.get('ticket.btn_view', id=ticket.id), callback_data=f'ticket_view_{ticket.id}')])
        
        keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='support')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def view_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE, ticket_id: int = None):
    """View ticket conversation."""
    query = update.callback_query
    
    if ticket_id is None and query:
        try:
            ticket_id = int(query.data.split('_')[2])
        except (IndexError, ValueError):
            pass
            
    if query:
        await query.answer()
    
    if not ticket_id:
        return ConversationHandler.END
    
    user_id = update.effective_user.id
    from vpn_bot.utils import LanguageManager
    
    user = await get_user_by_tg_id(user_id)
    ticket, _, messages = await get_ticket_comprehensive(ticket_id)
    
    if not user or not ticket or ticket.user_id != user.id:
        msg = LanguageManager.get('ticket.view_not_found')
        if query: await query.edit_message_text(msg)
        else: await update.message.reply_text(msg)
        return ConversationHandler.END
    
    status_emoji = {
        'open': '🟢',
        'waiting_admin': '⏳',
        'waiting_user': '💬',
        'closed': '✅'
    }.get(ticket.status, '🔵')
    
    text = LanguageManager.get('ticket.view_header',
        emoji=status_emoji,
        id=ticket.id,
        subject=ticket.subject,
        status=LanguageManager.get(f'status.{ticket.status}'),
        date=ticket.created_at.strftime('%Y-%m-%d %H:%M')
    )
    
    for msg in messages:
        sender = LanguageManager.get('ticket.sender_you') if msg.sender_type == 'user' else LanguageManager.get('ticket.sender_support')
        time = msg.created_at.strftime('%m/%d %H:%M')
        attach_label = f" 📎 [{msg.attachment_type.upper()}]" if msg.attachment_file_id else ""
        text += LanguageManager.get('ticket.msg_format', sender=sender, time=time, message=msg.message + attach_label)
    
    keyboard = []
    
    if ticket.status != 'closed':
        keyboard.append([InlineKeyboardButton(LanguageManager.get('ticket.btn_reply'), callback_data=f'ticket_reply_{ticket_id}')])
        keyboard.append([InlineKeyboardButton(LanguageManager.get('ticket.btn_close'), callback_data=f'ticket_close_{ticket_id}')])
    
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    return ConversationHandler.END

async def reply_to_ticket_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start replying to ticket — verifies ownership before allowing reply."""
    query = update.callback_query
    ticket_id = int(query.data.split('_')[2])
    from vpn_bot.utils import LanguageManager

    user_id = update.effective_user.id
    user = await get_user_by_tg_id(user_id)
    ticket, _, _ = await get_ticket_comprehensive(ticket_id)
    if not user or not ticket or ticket.user_id != user.id:
        await query.answer(LanguageManager.get('ticket.view_not_found'), show_alert=True)
        return ConversationHandler.END

    context.user_data['reply_ticket_id'] = ticket_id
    await query.answer()
    await reply_conv_prompt(update, LanguageManager.get('ticket.reply_start', id=ticket_id))
    return TICKET_REPLY

@rate_limit(seconds=3)
async def receive_ticket_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and save ticket reply."""
    if is_conv_cancel(update):
        return await cancel_ticket(update, context)
    user_id = update.effective_user.id
    message_text = update.message.text or update.message.caption or ""
    attachment_file_id = None
    attachment_type = None
    from vpn_bot.utils import LanguageManager
    
    if update.message.photo:
        attachment_file_id = update.message.photo[-1].file_id
        attachment_type = 'photo'
    elif update.message.video:
        attachment_file_id = update.message.video.file_id
        attachment_type = 'video'
    elif update.message.document:
        attachment_file_id = update.message.document.file_id
        attachment_type = 'document'
    elif update.message.audio:
        attachment_file_id = update.message.audio.file_id
        attachment_type = 'audio'
    elif update.message.voice:
        attachment_file_id = update.message.voice.file_id
        attachment_type = 'voice'
    
    ticket_id = context.user_data.get('reply_ticket_id')
    
    success, _ = await add_ticket_message(
        ticket_id, user_id, 'user', message_text,
        attachment_file_id, attachment_type
    )
    
    if not success:
        await update.message.reply_text(LanguageManager.get('ticket.view_not_found'))
        return ConversationHandler.END
    
    # Notify admins
    await notify_admins_ticket_reply(update.get_bot(), ticket_id, user_id, message_text, attachment_file_id, attachment_type)
    
    await update.message.reply_text(
        LanguageManager.get('ticket.reply_sent', id=ticket_id),
        parse_mode='Markdown'
    )
    
    context.user_data.clear()
    return await view_ticket(update, context, ticket_id=ticket_id)

async def notify_admins_ticket_reply(bot, ticket_id, user_telegram_id, message, attachment_file_id, attachment_type):
    """Notify admins about ticket reply (Group or PV)."""
    title = LanguageManager.get('ticket.notify_reply_title', id=ticket_id)
    pv_body = LanguageManager.get('ticket.notify_reply_body', tg_id=user_telegram_id, message=message[:500])
    group_body = LanguageManager.get('ticket.notify_reply_body_group', message=message[:500])

    keyboard = [[InlineKeyboardButton(LanguageManager.get('ticket.btn_view_ticket'), callback_data=f'admin_ticket_{ticket_id}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)

    mode = await get_ticket_notif_mode()
    group_id = await get_support_group_id()

    if mode == 'group' and group_id:
        dest_ids = [group_id]
        to_group = True
    else:
        from vpn_bot.admin_permissions import PERM_TICKETS, get_admins_for_permission
        dest_ids = await get_admins_for_permission(PERM_TICKETS)
        to_group = False

    for dest_id in dest_ids:
        text = f"{title}{group_body if to_group else pv_body}"
        logger.debug("notify_admins_ticket_reply ticket=%s dest=%s group=%s", ticket_id, dest_id, to_group)
        try:
            if attachment_file_id:
                if attachment_type == 'photo':
                    await bot.send_photo(chat_id=dest_id, photo=attachment_file_id, caption=text, reply_markup=reply_markup, parse_mode='Markdown')
                elif attachment_type == 'video':
                    await bot.send_video(chat_id=dest_id, video=attachment_file_id, caption=text, reply_markup=reply_markup, parse_mode='Markdown')
                elif attachment_type == 'audio':
                    await bot.send_audio(chat_id=dest_id, audio=attachment_file_id, caption=text, reply_markup=reply_markup, parse_mode='Markdown')
                elif attachment_type == 'voice':
                    await bot.send_voice(chat_id=dest_id, voice=attachment_file_id, caption=text, reply_markup=reply_markup, parse_mode='Markdown')
                else: 
                    await bot.send_document(chat_id=dest_id, document=attachment_file_id, caption=text, reply_markup=reply_markup, parse_mode='Markdown')
            else:
                await bot.send_message(
                    chat_id=dest_id,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode='Markdown'
                )
        except Exception as e:
            logger.warning("Failed to notify dest %s for ticket reply %s: %s", dest_id, ticket_id, e)

async def close_ticket_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User closes their ticket."""
    query = update.callback_query
    ticket_id = int(query.data.split('_')[2])
    
    await query.answer()
    
    user_id = update.effective_user.id
    from vpn_bot.utils import LanguageManager
    
    user = await get_user_by_tg_id(user_id)
    ticket, _, _ = await get_ticket_comprehensive(ticket_id)
    
    if not user or not ticket or ticket.user_id != user.id:
        await query.edit_message_text(LanguageManager.get('ticket.view_not_found'))
        return ConversationHandler.END

    await close_ticket(ticket_id)
    
    menu_keyboard = [[InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')]]
    await query.edit_message_text(
        LanguageManager.get('ticket.closed_msg', id=ticket_id),
        reply_markup=InlineKeyboardMarkup(menu_keyboard),
        parse_mode='Markdown'
    )
    return ConversationHandler.END

# --- Cancel Handler ---

async def cancel_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel ticket flow and return to support menu or main menu."""
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer(LanguageManager.get('common.cancelled'), show_alert=False)
        await support_menu(update, context)
    else:
        from vpn_bot.bot_handler import start
        await update.message.reply_text(LanguageManager.get('common.cancelled'), parse_mode='Markdown')
        await start(update, context)
    return ConversationHandler.END

# --- Conversation Handler ---

support_ticket_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(support_menu, pattern='^support$'),
        MessageHandler(filters.Regex(f"^({'|'.join(LanguageManager.get_all_translations('menu.support'))})$"), support_menu),

        CallbackQueryHandler(create_ticket_start, pattern='^ticket_create$'),
        CallbackQueryHandler(my_tickets, pattern='^ticket_list$'),
        CallbackQueryHandler(view_ticket, pattern='^ticket_view_'),
        CallbackQueryHandler(reply_to_ticket_start, pattern='^ticket_reply_'),
        CallbackQueryHandler(close_ticket_user, pattern='^ticket_close_'),
    ],
    states={
        TICKET_SUBJECT: [
            *conv_control_handlers(cancel_ticket),
            CallbackQueryHandler(select_ticket_subject, pattern='^ticket_subj_'),
            CallbackQueryHandler(select_ticket_subject, pattern='^ticket_custom_subject$'),
            CallbackQueryHandler(support_menu, pattern='^support$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, receive_ticket_subject)
        ],
        TICKET_MESSAGE: [
            *conv_control_handlers(cancel_ticket),
            MessageHandler(
                ((filters.TEXT & ~MENU_BUTTONS_FILTER) | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE)
                & (~filters.COMMAND),
                receive_ticket_message,
            ),
        ],
        TICKET_REPLY: [
            *conv_control_handlers(cancel_ticket),
            MessageHandler(
                ((filters.TEXT & ~MENU_BUTTONS_FILTER) | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE)
                & (~filters.COMMAND),
                receive_ticket_reply,
            ),
        ],
        REG_NAME: [MessageHandler(filters.TEXT & (~filters.COMMAND) & (~MENU_BUTTONS_FILTER), receive_name)],
        REG_PHONE: [
            MessageHandler(filters.CONTACT, receive_phone),
            MessageHandler(filters.TEXT & (~filters.COMMAND) & (~MENU_BUTTONS_FILTER), receive_phone_manual_warning)
        ],
    },
    fallbacks=[
        *legacy_cancel_handlers(cancel_ticket),
        CallbackQueryHandler(support_menu, pattern='^support$'),
        MessageHandler(MENU_BUTTONS_FILTER, main_menu_text_dispatch)
    ]
)
