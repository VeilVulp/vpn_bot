"""
Admin Ticket Management
Handles admin responses to support tickets.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CallbackQueryHandler

from vpn_bot.admin_ticket_service import (
    get_tickets_by_filter, get_ticket_comprehensive, add_ticket_message,
    close_ticket, search_tickets_by_user, create_outbound_ticket,
    set_ticket_notif_mode
)
from vpn_bot.admin_user_service import get_user_by_tg_id
from vpn_bot.utils import LanguageManager, format_datetime, safe_response
from vpn_bot.bot_handler import MENU_BUTTONS_FILTER, main_menu_text_dispatch
from vpn_bot.admin_conversation import admin_exit_to_menu, build_admin_fallback_handlers
from vpn_bot.admin_menu import build_admin_back_markup
from vpn_bot.conversation_controls import conv_control_handlers, is_conv_cancel, reply_conv_prompt
from vpn_bot.admin_permissions import (
    PERM_TICKETS,
    PERM_TICKETS_ACTIVE,
    PERM_TICKETS_CLOSED,
    PERM_TICKETS_REPLY,
    PERM_TICKETS_SEARCH,
    require_admin_message,
)

# States
ADMIN_TICKET_REPLY = 0
SEARCH_TICKET_USER = 2
OUTBOUND_USER_ID, OUTBOUND_SUBJECT, OUTBOUND_MESSAGE = range(10, 13)

async def admin_create_ticket_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start the outbound ticket creation flow."""
    query = update.callback_query
    await query.answer()
    await reply_conv_prompt(
        update,
        LanguageManager.get('admin.tickets.create_user_prompt'),
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_tickets')]]),
    )
    return OUTBOUND_USER_ID

@safe_response
async def admin_ticket_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Submenu for managing tickets."""
    if not await require_admin_message(update, perm=PERM_TICKETS, chat_context="support_or_private"):
        return ConversationHandler.END
    query = update.callback_query
    if query: await query.answer()
    
    from vpn_bot.admin_menu import inline_button, keyboard_row_pair

    text = LanguageManager.get('admin.tickets.menu_title')
    keyboard = [
        *keyboard_row_pair(
            'admin.tickets.btn_active', 'admin_tickets_active',
            'admin.tickets.btn_closed', 'admin_tickets_closed',
        ),
        *keyboard_row_pair(
            'admin.tickets.btn_search_user', 'admin_tickets_search',
            'admin.tickets.btn_create', 'admin_tickets_create',
        ),
        [inline_button('admin.tickets.btn_notif_mode', 'admin_tickets_notif_mode', short=True)],
    ]
    
    # Only show back to main menu if in private chat
    if update.effective_chat.type == 'private':
        keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_start')])
    
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def toggle_ticket_notif_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle or show notification mode settings."""
    query = update.callback_query
    await query.answer()
    
    from vpn_bot.support_tickets import get_ticket_notif_mode, get_support_group_id
    
    # Check if this is a toggle action
    data = query.data
    if data.startswith('admin_tickets_set_mode_'):
        new_mode = data.split('_')[-1] # 'pv' or 'group'
        
        if new_mode == 'group':
            # We still need to check group_id
            group_id = await get_support_group_id()
            if not group_id:
                await query.answer(LanguageManager.get('admin.tickets.error_no_group'), show_alert=True)
                return
        
        await set_ticket_notif_mode(new_mode)
            
        mode_label = LanguageManager.get(f'admin.tickets.mode_{new_mode}')
        await query.answer(LanguageManager.get('admin.tickets.mode_updated', mode=mode_label), show_alert=True)

    # Show menu
    current_mode = await get_ticket_notif_mode()
    mode_label = LanguageManager.get(f'admin.tickets.mode_{current_mode}')
    
    text = LanguageManager.get('admin.tickets.notif_mode_title', mode=mode_label)
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.tickets.mode_pv') + (" ✅" if current_mode == 'pv' else ""), callback_data='admin_tickets_set_mode_pv')],
        [InlineKeyboardButton(LanguageManager.get('admin.tickets.mode_group') + (" ✅" if current_mode == 'group' else ""), callback_data='admin_tickets_set_mode_group')],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_tickets')]
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def admin_list_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored admin_list_tickets using admin_ticket_service."""
    query = update.callback_query
    data = query.data if query else ""
    status_filter = 'closed' if 'closed' in data else 'active'
    list_perm = PERM_TICKETS_CLOSED if status_filter == 'closed' else PERM_TICKETS_ACTIVE
    if not await require_admin_message(update, perm=list_perm, chat_context="support_or_private"):
        return ConversationHandler.END
    if query:
        await query.answer()
    
    tickets = await get_tickets_by_filter(status_filter)
    
    if not tickets:
        text = LanguageManager.get('admin.tickets.empty')
        keyboard = [[InlineKeyboardButton(LanguageManager.get('admin.tickets.btn_tickets'), callback_data='admin_tickets')]]
    else:
        title_key = 'admin.tickets.btn_active' if status_filter == 'active' else 'admin.tickets.btn_closed'
        text = f"{LanguageManager.get(title_key)}\n━━━━━━━━━━\n\n"
        keyboard = []
        
        for ticket in tickets:
            status_emoji = {'open': '🟢', 'waiting_admin': '🔴', 'waiting_user': '⏳', 'closed': '✅'}.get(ticket.status, '🔵')
            text += f"{status_emoji} **#{ticket.id}**: {escape_markdown(ticket.subject[:40], version=1)}\n"
            text += f"📅 {ticket.updated_at.strftime('%m/%d %H:%M')}\n-------------------\n"
            keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.tickets.btn_view', id=ticket.id), callback_data=f'admin_ticket_{ticket.id}')])
        
        keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.tickets.btn_tickets'), callback_data='admin_tickets')])
    
    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END


async def admin_view_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored admin_view_ticket using admin_ticket_service."""
    query = update.callback_query
    ticket_id = int(query.data.split('_')[2])
    await query.answer()
    
    ticket, user, messages = await get_ticket_comprehensive(ticket_id)
    if not ticket:
        await query.edit_message_text(LanguageManager.get('admin.tickets.not_found'))
        return ConversationHandler.END
    
    status_emoji = {'open': '🟢', 'waiting_admin': '🔴', 'waiting_user': '⏳', 'closed': '✅'}.get(ticket.status, '🔵')
    formatted_created_at = await format_datetime(ticket.created_at, include_time=True)

    text = LanguageManager.get('admin.tickets.view_header',
        emoji=status_emoji, id=ticket.id,
        name=escape_markdown(user.full_name or LanguageManager.get('common.na'), version=1),
        phone=escape_markdown(user.phone_number or LanguageManager.get('common.not_shared'), version=1),
        tg_id=user.telegram_id, subject=escape_markdown(ticket.subject, version=1),
        status=LanguageManager.get(f'status.{ticket.status}'), date=formatted_created_at
    )
    
    for msg in messages:
        sender = LanguageManager.get('admin.tickets.sender_user') if msg.sender_type == 'user' else LanguageManager.get('admin.tickets.sender_admin')
        time = await format_datetime(msg.created_at, include_time=True)
        attach_label = f" 📎 [{msg.attachment_type.upper()}]" if msg.attachment_file_id else ""
        text += LanguageManager.get('admin.tickets.msg_format', sender=sender, time=time, message=escape_markdown(msg.message[:200], version=1) + attach_label)
    
    keyboard = []
    if ticket.status != 'closed':
        keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.tickets.btn_reply'), callback_data=f'admin_reply_{ticket_id}')])
        keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.tickets.btn_close'), callback_data=f'admin_close_{ticket_id}')])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.tickets.btn_tickets'), callback_data='admin_tickets')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def admin_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin starts replying to ticket."""
    query = update.callback_query
    ticket_id = int(query.data.split('_')[2])
    
    context.user_data['admin_reply_ticket_id'] = ticket_id
    
    await query.answer()
    await reply_conv_prompt(
        update,
        LanguageManager.get('admin.tickets.reply_prompt', id=ticket_id),
        reply_markup=build_admin_back_markup('admin_tickets'),
    )
    return ADMIN_TICKET_REPLY

async def admin_receive_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored admin_receive_reply using admin_ticket_service."""
    if is_conv_cancel(update):
        context.user_data.pop('admin_reply_ticket_id', None)
        return await admin_ticket_menu(update, context)
    if not await require_admin_message(update, perm=PERM_TICKETS_REPLY):
        return ConversationHandler.END
    admin_id = update.effective_user.id
    message_text = update.message.text or update.message.caption or ""
    attachment_file_id = None
    attachment_type = None
    
    # Extract attachment info
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
    
    ticket_id = context.user_data.get('admin_reply_ticket_id')
    
    success, result = await add_ticket_message(
        ticket_id, admin_id, 'admin', message_text,
        attachment_file_id, attachment_type
    )
    
    tickets_back = build_admin_back_markup('admin_tickets')
    if not success:
        await update.message.reply_text(LanguageManager.get('admin.tickets.not_found'), reply_markup=tickets_back)
        return ADMIN_TICKET_REPLY
        
    user_tg_id = result
    
    # Notify user
    admin_name = update.effective_user.full_name
    await notify_user_admin_reply(update.get_bot(), user_tg_id, ticket_id, message_text, attachment_file_id, attachment_type, admin_name=admin_name)
    
    await update.message.reply_text(
        LanguageManager.get('admin.tickets.reply_sent', id=ticket_id),
        reply_markup=tickets_back,
        parse_mode='Markdown',
    )
    
    context.user_data.clear()
    return await admin_view_ticket(update, context)

async def notify_user_admin_reply(bot, user_telegram_id, ticket_id, message, attachment_file_id, attachment_type, admin_name=None):
    """Notify user about admin reply with attribution."""
    admin_info = LanguageManager.get('ticket.admin_info', name=admin_name) if admin_name else ""
    text = LanguageManager.get('ticket.notify_user', id=ticket_id, message=message, admin_info=admin_info)
    keyboard = [[InlineKeyboardButton(LanguageManager.get('ticket.btn_view_ticket'), callback_data=f'ticket_view_{ticket_id}')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    try:
        if attachment_file_id:
            if attachment_type == 'photo':
                await bot.send_photo(chat_id=user_telegram_id, photo=attachment_file_id, caption=text, reply_markup=reply_markup, parse_mode='Markdown')
            elif attachment_type == 'video':
                await bot.send_video(chat_id=user_telegram_id, video=attachment_file_id, caption=text, reply_markup=reply_markup, parse_mode='Markdown')
            elif attachment_type == 'audio':
                await bot.send_audio(chat_id=user_telegram_id, audio=attachment_file_id, caption=text, reply_markup=reply_markup, parse_mode='Markdown')
            elif attachment_type == 'voice':
                await bot.send_voice(chat_id=user_telegram_id, voice=attachment_file_id, caption=text, reply_markup=reply_markup, parse_mode='Markdown')
            else: # document or fallback
                await bot.send_document(chat_id=user_telegram_id, document=attachment_file_id, caption=text, reply_markup=reply_markup, parse_mode='Markdown')
        else:
            await bot.send_message(
                chat_id=user_telegram_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode='Markdown'
            )
    except Exception as e:
        print(f"Failed to notify user {user_telegram_id}: {e}")

async def group_menu_settings_proxy(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Proxy to admin_ticket_menu when inside group."""
    return await admin_ticket_menu(update, context)

async def handle_admin_group_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored handle_admin_group_reply using admin_ticket_service."""
    from vpn_bot.support_tickets import get_support_group_id
    group_id = await get_support_group_id()
    if not group_id or update.effective_chat.id != group_id: return
        
    admin_id = update.effective_user.id
    from vpn_bot.admin_management import has_admin_perm, is_user_admin
    from vpn_bot.admin_permissions import PERM_TICKETS_REPLY
    if not await is_user_admin(admin_id):
        return
    if not await has_admin_perm(admin_id, PERM_TICKETS_REPLY):
        return

    if not update.message.reply_to_message: return
        
    replied_msg = update.message.reply_to_message
    if not replied_msg.from_user or replied_msg.from_user.id != context.bot.id: return
        
    ticket_id = None
    if replied_msg.reply_markup and replied_msg.reply_markup.inline_keyboard:
        for row in replied_msg.reply_markup.inline_keyboard:
            for btn in row:
                if btn.callback_data.startswith('admin_ticket_'):
                    try: ticket_id = int(btn.callback_data.split('_')[2])
                    except: pass
                    break

    if not ticket_id:
        import re
        match = re.search(r'#(\d+)', replied_msg.text or replied_msg.caption or "")
        if match: ticket_id = int(match.group(1))

    if not ticket_id: return

    message_text = update.message.text or update.message.caption or ""
    attachment_file_id = None
    attachment_type = None
    
    if update.message.photo:
        attachment_file_id = update.message.photo[-1].file_id
        attachment_type = 'photo'
    elif update.message.video:
        attachment_file_id = update.message.video.file_id
        attachment_type = 'video'
    elif update.message.document:
        attachment_file_id = update.message.document.file_id
        attachment_type = 'document'
    elif update.message.voice:
        attachment_file_id = update.message.voice.file_id
        attachment_type = 'voice'
    elif update.message.audio:
        attachment_file_id = update.message.audio.file_id
        attachment_type = 'audio'

    admin_name = update.effective_user.full_name
    
    success, result = await add_ticket_message(
        ticket_id, admin_id, 'admin', message_text,
        attachment_file_id, attachment_type
    )
    
    if not success:
        await update.message.reply_text(LanguageManager.get('admin.tickets.not_found'))
        return
        
    user_tg_id = result
    await notify_user_admin_reply(context.bot, user_tg_id, ticket_id, message_text, attachment_file_id, attachment_type, admin_name=admin_name)
    await update.message.reply_text(LanguageManager.get('admin.tickets.reply_sent', id=ticket_id), parse_mode='Markdown')

async def admin_close_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored admin_close_ticket using admin_ticket_service."""
    query = update.callback_query
    ticket_id = int(query.data.split('_')[2])
    await query.answer()
    
    success = await close_ticket(ticket_id)
    if not success:
        await query.edit_message_text(LanguageManager.get('admin.tickets.not_found'))
        return ConversationHandler.END
    
    await query.edit_message_text(
        LanguageManager.get('admin.tickets.closed_success', id=ticket_id),
        parse_mode='Markdown'
    )
    return ConversationHandler.END

async def admin_ticket_search_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt for user id to search tickets."""
    if not await require_admin_message(update, perm=PERM_TICKETS_SEARCH, chat_context="support_or_private"):
        return ConversationHandler.END
    query = update.callback_query
    if query: await query.answer()
    
    text = LanguageManager.get('admin.tickets.search_prompt')
    reply_markup = InlineKeyboardMarkup([[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='admin_tickets')]])
    
    if query:
        await reply_conv_prompt(update, text, reply_markup=reply_markup)
    else:
        await reply_conv_prompt(update, text, reply_markup=reply_markup)
    return SEARCH_TICKET_USER

async def admin_ticket_search_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored admin_ticket_search_receive using admin_ticket_service."""
    if is_conv_cancel(update):
        return await admin_ticket_menu(update, context)
    if not await require_admin_message(update, perm=PERM_TICKETS_SEARCH, chat_context="support_or_private"):
        return ConversationHandler.END
    user_id_str = update.message.text.strip()
    tickets_back = build_admin_back_markup('admin_tickets')
    if not user_id_str.isdigit():
        await update.message.reply_text(LanguageManager.get('admin.tickets.invalid_tg_id'), reply_markup=tickets_back)
        return SEARCH_TICKET_USER
    
    tg_id = int(user_id_str)
    user, tickets = await search_tickets_by_user(tg_id)
    
    if not user:
        await update.message.reply_text(LanguageManager.get('admin.tickets.user_not_found'), reply_markup=tickets_back)
        return SEARCH_TICKET_USER
        
    if not tickets:
        await update.message.reply_text(LanguageManager.get('admin.tickets.no_tickets_user'), reply_markup=tickets_back)
        from vpn_bot.admin_panel import admin_start
        await admin_start(update, context)
        return ConversationHandler.END
    
    text = LanguageManager.get('admin.tickets.user_tickets_title', name=escape_markdown(user.full_name or "N/A", version=1), tg_id=user.telegram_id)
    keyboard = []
    
    for ticket in tickets:
        status_emoji = {'open': '🟢', 'waiting_admin': '🔴', 'waiting_user': '⏳', 'closed': '✅'}.get(ticket.status, '🔵')
        text += f"{status_emoji} **#{ticket.id}**: {escape_markdown(ticket.subject[:40], version=1)} ({LanguageManager.get(f'status.{ticket.status}')})\n"
        keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.tickets.btn_view', id=ticket.id), callback_data=f'admin_ticket_{ticket.id}')])
    
    keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.tickets.btn_tickets'), callback_data='admin_tickets')])
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def admin_create_ticket_user_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Validate user ID and prompt for subject."""
    if is_conv_cancel(update):
        return await admin_ticket_menu(update, context)
    user_id_str = update.message.text.strip()
    tickets_back = build_admin_back_markup('admin_tickets')
    if not user_id_str.isdigit():
        await update.message.reply_text(LanguageManager.get('admin.tickets.error_invalid_id'), reply_markup=tickets_back)
        return OUTBOUND_USER_ID
    
    tg_id = int(user_id_str)
    user = await get_user_by_tg_id(tg_id)
    
    if not user:
        await update.message.reply_text(LanguageManager.get('admin.tickets.error_user_not_found'), reply_markup=tickets_back)
        return OUTBOUND_USER_ID
    
    context.user_data['outbound_user_obj_id'] = user.id
    context.user_data['outbound_tg_id'] = user.telegram_id
        
    await reply_conv_prompt(
        update,
        LanguageManager.get('admin.tickets.create_subject_prompt'),
        reply_markup=tickets_back,
    )
    return OUTBOUND_SUBJECT

async def admin_create_ticket_subject_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Store subject and prompt for message."""
    if is_conv_cancel(update):
        return await admin_ticket_menu(update, context)
    tickets_back = build_admin_back_markup('admin_tickets')
    subject = update.message.text.strip()
    if len(subject) < 3:
        await update.message.reply_text(LanguageManager.get('admin.tickets.subject_too_short'), reply_markup=tickets_back)
        return OUTBOUND_SUBJECT
        
    context.user_data['outbound_subject'] = subject
    await reply_conv_prompt(
        update,
        LanguageManager.get('admin.tickets.create_msg_prompt'),
        reply_markup=tickets_back,
    )
    return OUTBOUND_MESSAGE

async def admin_create_ticket_msg_received(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored admin_create_ticket_msg_received using admin_ticket_service."""
    if is_conv_cancel(update):
        context.user_data.clear()
        return await admin_ticket_menu(update, context)
    msg_text = update.message.text or update.message.caption or ""
    attachment_file_id = None
    attachment_type = None
    
    if update.message.photo:
        attachment_file_id = update.message.photo[-1].file_id
        attachment_type = 'photo'
    elif update.message.video:
        attachment_file_id = update.message.video.file_id
        attachment_type = 'video'
    elif update.message.document:
        attachment_file_id = update.message.document.file_id
        attachment_type = 'document'
    
    user_id = context.user_data.get('outbound_user_obj_id')
    tg_id = context.user_data.get('outbound_tg_id')
    subject = context.user_data.get('outbound_subject')
    admin_id = update.effective_user.id
    
    ticket_id = await create_outbound_ticket(
        user_id, admin_id, subject, msg_text,
        attachment_file_id, attachment_type
    )

    # Notify user
    notify_text = LanguageManager.get('admin.tickets.outbound_notify_user', subject=subject)
    keyboard = [[InlineKeyboardButton(LanguageManager.get('admin.tickets.btn_view_ticket'), callback_data=f'ticket_view_{ticket_id}')]]
    
    try:
        if attachment_file_id:
            await notify_user_admin_reply(update.get_bot(), tg_id, ticket_id, msg_text, attachment_file_id, attachment_type)
        else:
            await update.get_bot().send_message(
                chat_id=tg_id,
                text=notify_text + "\n\n" + msg_text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
    except Exception as e:
        await update.message.reply_text(LanguageManager.get('admin.tickets.create_notify_fail', error=str(e)))
        
    await update.message.reply_text(LanguageManager.get('admin.tickets.create_success', id=ticket_id))
    context.user_data.clear()
    return await admin_ticket_menu(update, context)

# --- Conversation Handler ---

admin_ticket_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(admin_ticket_menu, pattern='^admin_tickets$'),
        CallbackQueryHandler(admin_list_tickets, pattern='^admin_tickets_(active|closed)$'),
        CallbackQueryHandler(admin_ticket_search_start, pattern='^admin_tickets_search$'),
        CallbackQueryHandler(admin_create_ticket_start, pattern='^admin_tickets_create$'),
        CallbackQueryHandler(admin_view_ticket, pattern='^admin_ticket_'),
        CallbackQueryHandler(admin_reply_start, pattern='^admin_reply_'),
        CallbackQueryHandler(admin_close_ticket, pattern='^admin_close_'),
        CallbackQueryHandler(toggle_ticket_notif_mode, pattern='^admin_tickets_(notif_mode|set_mode_)'),
        
        # Support Group Menu Buttons
        MessageHandler(filters.Regex(f"^({'|'.join(LanguageManager.get_all_translations('admin.support_group.menu_active'))})$"), admin_list_tickets),
        MessageHandler(filters.Regex(f"^({'|'.join(LanguageManager.get_all_translations('admin.support_group.menu_search'))})$"), admin_ticket_search_start),
        MessageHandler(filters.Regex(f"^({'|'.join(LanguageManager.get_all_translations('admin.support_group.menu_settings'))})$"), group_menu_settings_proxy),
    ],
    states={
        ADMIN_TICKET_REPLY: [
            *conv_control_handlers(admin_exit_to_menu),
            MessageHandler(((filters.TEXT & ~MENU_BUTTONS_FILTER) | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE) & (~filters.COMMAND), admin_receive_reply),
            CallbackQueryHandler(admin_ticket_menu, pattern='^admin_tickets$'),
        ],
        SEARCH_TICKET_USER: [
            *conv_control_handlers(admin_exit_to_menu),
            MessageHandler((filters.TEXT & ~MENU_BUTTONS_FILTER) & (~filters.COMMAND), admin_ticket_search_receive),
            CallbackQueryHandler(admin_ticket_menu, pattern='^admin_tickets$'),
        ],
        OUTBOUND_USER_ID: [
            *conv_control_handlers(admin_exit_to_menu),
            MessageHandler((filters.TEXT & ~MENU_BUTTONS_FILTER) & (~filters.COMMAND), admin_create_ticket_user_received),
            CallbackQueryHandler(admin_ticket_menu, pattern='^admin_tickets$'),
        ],
        OUTBOUND_SUBJECT: [
            *conv_control_handlers(admin_exit_to_menu),
            MessageHandler((filters.TEXT & ~MENU_BUTTONS_FILTER) & (~filters.COMMAND), admin_create_ticket_subject_received),
            CallbackQueryHandler(admin_ticket_menu, pattern='^admin_tickets$'),
        ],
        OUTBOUND_MESSAGE: [
            *conv_control_handlers(admin_exit_to_menu),
            MessageHandler(((filters.TEXT & ~MENU_BUTTONS_FILTER) | filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE) & (~filters.COMMAND), admin_create_ticket_msg_received),
            CallbackQueryHandler(admin_ticket_menu, pattern='^admin_tickets$'),
        ],
    },
    fallbacks=[
        *build_admin_fallback_handlers(main_menu_text_dispatch),
        CallbackQueryHandler(admin_ticket_menu, pattern='^admin_tickets$'),
        CallbackQueryHandler(admin_exit_to_menu, pattern='^.*$'),
    ],
    allow_reentry=True
)


