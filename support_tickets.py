"""
Support Ticket System
Handles user support requests and admin responses.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from sqlalchemy import select, desc, and_
from datetime import datetime

from database import AsyncSessionLocal
from models import User, Ticket, TicketMessage
from config import config

# States
TICKET_SUBJECT, TICKET_MESSAGE, TICKET_REPLY = range(3)

# --- User Ticket Functions ---

async def support_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Support center main menu."""
    query = update.callback_query
    if query:
        await query.answer()
    
    user_id = update.effective_user.id
    
    async with AsyncSessionLocal() as session:
        # Get user
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        if not user:
            msg = "❌ User not found."
            if query:
                await query.edit_message_text(msg)
            else:
                await update.message.reply_text(msg)
            return ConversationHandler.END
        
        # Count open tickets
        t_res = await session.execute(
            select(Ticket).where(
                and_(Ticket.user_id == user.id, Ticket.status != 'closed')
            )
        )
        open_tickets = len(t_res.scalars().all())
    
    # Get support hours from admin settings (optional)
    from admin_settings import get_admin_setting
    support_text = await get_admin_setting('support_hours_text', 
        "Support is available 24/7.\nWe typically respond within 2-4 hours.")
    
    text = (
        f"💬 **Support Center**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"{support_text}\n\n"
        f"📋 Open Tickets: {open_tickets}\n"
    )
    
    keyboard = [
        [InlineKeyboardButton("📝 Create New Ticket", callback_data='ticket_create')],
        [InlineKeyboardButton("📋 My Tickets", callback_data='ticket_list')],
        [InlineKeyboardButton("🔙 Main Menu", callback_data='main_menu')]
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
    await query.answer()
    
    # Get subjects from admin settings (with defaults)
    from admin_settings import get_admin_setting
    custom_subjects = await get_admin_setting('ticket_subjects', [])
    
    # Default subjects + custom ones
    default_subjects = [
        "🔌 Connection Issues",
        "💰 Payment Problem",
        "📱 App Help",
        "🔄 Renewal Request",
        "❓ General Question"
    ]
    
    all_subjects = custom_subjects if custom_subjects else default_subjects
    
    keyboard = []
    for idx, subject in enumerate(all_subjects):
        keyboard.append([InlineKeyboardButton(subject, callback_data=f'ticket_subj_{idx}')])
    
    keyboard.append([
        InlineKeyboardButton("✏️ Custom Subject", callback_data='ticket_custom_subject'),
        InlineKeyboardButton("❌ Cancel", callback_data='support')
    ])
    
    await query.edit_message_text(
        "📝 **Create Support Ticket**\n\n"
        "Please select a subject for your ticket:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return TICKET_SUBJECT

async def select_ticket_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle subject button selection."""
    query = update.callback_query
    await query.answer()
    
    data = query.data
    
    if data == 'ticket_custom_subject':
        # Prompt for custom subject
        await query.edit_message_text(
            "📝 **Custom Subject**\n\n"
            "Please type your subject (5-200 characters):\n\n"
            "Or /cancel to go back",
            parse_mode='Markdown'
        )
        return TICKET_SUBJECT
    
    # Get subjects list
    from admin_settings import get_admin_setting
    custom_subjects = await get_admin_setting('ticket_subjects', [])
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
        
        await query.edit_message_text(
            f"✅ **Subject:** {subject}\n\n"
            "Now please describe your issue in detail:\n\n"
            "You can also send a screenshot if needed.",
            parse_mode='Markdown'
        )
        return TICKET_MESSAGE
    else:
        await query.edit_message_text("❌ Invalid selection.")
        return ConversationHandler.END

async def receive_ticket_subject(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive custom ticket subject via text."""
    subject = update.message.text
    
    if len(subject) < 5:
        await update.message.reply_text(
            "❌ Subject too short. Please provide more details.\n\n"
            "Or /cancel to abort:"
        )
        return TICKET_SUBJECT
    
    if len(subject) > 200:
        await update.message.reply_text(
            "❌ Subject too long (max 200 characters).\n\n"
            "Please shorten it or /cancel:"
        )
        return TICKET_SUBJECT
    
    context.user_data['ticket_subject'] = subject
    
    await update.message.reply_text(
        f"✅ Subject: {subject}\n\n"
        "Now please describe your issue in detail:\n\n"
        "You can also send a screenshot if needed."
    )
    return TICKET_MESSAGE

async def receive_ticket_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive ticket message and create ticket."""
    user_id = update.effective_user.id
    message_text = update.message.text or update.message.caption or ""
    attachment_file_id = None
    
    # Check for photo attachment
    if update.message.photo:
        attachment_file_id = update.message.photo[-1].file_id
    
    if not message_text and not attachment_file_id:
        await update.message.reply_text(
            "❌ Please provide a message or screenshot.\n\n"
            "Or /cancel to abort:"
        )
        return TICKET_MESSAGE
    
    subject = context.user_data.get('ticket_subject', 'No subject')
    
    async with AsyncSessionLocal() as session:
        # Get user
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        if not user:
            await update.message.reply_text("❌ User not found.")
            return ConversationHandler.END
        
        # Create ticket
        ticket = Ticket(
            user_id=user.id,
            subject=subject,
            status='open',
            priority='medium'
        )
        session.add(ticket)
        await session.flush()  # Get ticket ID

        
        # Create first message
        ticket_msg = TicketMessage(
            ticket_id=ticket.id,
            sender_type='user',
            sender_id=user_id,
            message=message_text or "[Screenshot attached]",
            attachment_file_id=attachment_file_id
        )
        session.add(ticket_msg)
        await session.commit()
        
        ticket_id = ticket.id
    
    # Notify all admins
    await notify_admins_new_ticket(update.get_bot(), ticket_id, user.telegram_id, subject, message_text, attachment_file_id)
    
    await update.message.reply_text(
        f"✅ **Ticket #{ticket_id} Created!**\n\n"
        f"Subject: {subject}\n\n"
        "We'll respond as soon as possible.\n"
        "You'll be notified when an admin replies.",
        parse_mode='Markdown'
    )
    
    context.user_data.clear()
    
    # Return to support menu
    from bot_handler import start
    await start(update, context)
    return ConversationHandler.END

async def notify_admins_new_ticket(bot, ticket_id, user_telegram_id, subject, message, attachment_file_id):
    """Notify all admins about new ticket."""
    text = (
        f"🔔 **New Support Ticket #{ticket_id}**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"From: User ID {user_telegram_id}\n"
        f"Subject: {subject}\n\n"
        f"Message:\n{message[:500]}\n\n"
        f"Use /admin to manage tickets."
    )
    
    keyboard = [[InlineKeyboardButton("📋 View Ticket", callback_data=f'admin_ticket_{ticket_id}')]]
    
    for admin_id in config.ADMIN_IDS:
        try:
            if attachment_file_id:
                await bot.send_photo(
                    chat_id=admin_id,
                    photo=attachment_file_id,
                    caption=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
            else:
                await bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
        except Exception as e:
            print(f"Failed to notify admin {admin_id}: {e}")

async def my_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display user's tickets."""
    query = update.callback_query
    await query.answer()
    
    user_id = update.effective_user.id
    
    async with AsyncSessionLocal() as session:
        # Get user
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        if not user:
            await query.edit_message_text("❌ User not found.")
            return ConversationHandler.END
        
        # Get tickets
        t_res = await session.execute(
            select(Ticket)
            .where(Ticket.user_id == user.id)
            .order_by(desc(Ticket.created_at))
        )
        tickets = t_res.scalars().all()
    
    if not tickets:
        text = "📋 **My Tickets**\n\nYou have no tickets yet."
        keyboard = [
            [InlineKeyboardButton("📝 Create Ticket", callback_data='ticket_create')],
            [InlineKeyboardButton("🔙 Back", callback_data='support')]
        ]
    else:
        text = "📋 **My Support Tickets**\n━━━━━━━━━━━━━━━━━━━━\n\n"
        keyboard = []
        
        # Group by status
        open_tickets = [t for t in tickets if t.status != 'closed']
        closed_tickets = [t for t in tickets if t.status == 'closed']
        
        if open_tickets:
            text += "🟢 **Open Tickets:**\n\n"
            for ticket in open_tickets[:5]:  # Show last 5 open
                status_emoji = "⏳" if ticket.status == 'waiting_admin' else "💬"
                text += (
                    f"{status_emoji} **Ticket #{ticket.id}**\n"
                    f"Subject: {ticket.subject[:50]}\n"
                    f"Status: {ticket.status.replace('_', ' ').title()}\n"
                    f"Created: {ticket.created_at.strftime('%Y-%m-%d %H:%M')}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                )
                keyboard.append([InlineKeyboardButton(f"📖 View #{ticket.id}", callback_data=f'ticket_view_{ticket.id}')])
        
        if closed_tickets:
            text += "\n⚪ **Closed Tickets:**\n\n"
            for ticket in closed_tickets[:3]:  # Show last 3 closed
                text += (
                    f"✅ **Ticket #{ticket.id}**\n"
                    f"Subject: {ticket.subject[:50]}\n"
                    f"Closed: {ticket.closed_at.strftime('%Y-%m-%d') if ticket.closed_at else 'N/A'}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                )
                keyboard.append([InlineKeyboardButton(f"📖 View #{ticket.id}", callback_data=f'ticket_view_{ticket.id}')])
        
        keyboard.append([InlineKeyboardButton("🔙 Back", callback_data='support')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def view_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View ticket conversation."""
    query = update.callback_query
    ticket_id = int(query.data.split('_')[2])
    
    await query.answer()
    
    user_id = update.effective_user.id
    
    async with AsyncSessionLocal() as session:
        # Get user
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        # Get ticket
        ticket = await session.get(Ticket, ticket_id)
        
        if not ticket or ticket.user_id != user.id:
            await query.edit_message_text("❌ Ticket not found.")
            return ConversationHandler.END
        
        # Get messages
        m_res = await session.execute(
            select(TicketMessage)
            .where(TicketMessage.ticket_id == ticket_id)
            .order_by(TicketMessage.created_at)
        )
        messages = m_res.scalars().all()
    
    status_emoji = {
        'open': '🟢',
        'waiting_admin': '⏳',
        'waiting_user': '💬',
        'closed': '✅'
    }.get(ticket.status, '🔵')
    
    text = (
        f"{status_emoji} **Ticket #{ticket.id}**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Subject: {ticket.subject}\n"
        f"Status: {ticket.status.replace('_', ' ').title()}\n"
        f"Created: {ticket.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"**Conversation:**\n\n"
    )
    
    for msg in messages:
        sender = "👤 You" if msg.sender_type == 'user' else "👨‍💼 Support"
        time = msg.created_at.strftime('%m/%d %H:%M')
        text += f"{sender} ({time}):\n{msg.message}\n\n"
    
    keyboard = []
    
    if ticket.status != 'closed':
        keyboard.append([InlineKeyboardButton("💬 Reply", callback_data=f'ticket_reply_{ticket_id}')])
        keyboard.append([InlineKeyboardButton("✅ Close Ticket", callback_data=f'ticket_close_{ticket_id}')])
    
    keyboard.append([InlineKeyboardButton("🔙 My Tickets", callback_data='ticket_list')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def reply_to_ticket_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start replying to ticket."""
    query = update.callback_query
    ticket_id = int(query.data.split('_')[2])
    
    context.user_data['reply_ticket_id'] = ticket_id
    
    await query.answer()
    await query.edit_message_text(
        f"💬 **Reply to Ticket #{ticket_id}**\n\n"
        "Type your message:\n\n"
        "Or /cancel to abort",
        parse_mode='Markdown'
    )
    return TICKET_REPLY

async def receive_ticket_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive and save ticket reply."""
    user_id = update.effective_user.id
    message_text = update.message.text or update.message.caption or ""
    attachment_file_id = None
    
    if update.message.photo:
        attachment_file_id = update.message.photo[-1].file_id
    
    ticket_id = context.user_data.get('reply_ticket_id')
    
    async with AsyncSessionLocal() as session:
        # Get ticket
        ticket = await session.get(Ticket, ticket_id)
        
        if not ticket:
            await update.message.reply_text("❌ Ticket not found.")
            return ConversationHandler.END
        
        # Add message
        ticket_msg = TicketMessage(
            ticket_id=ticket_id,
            sender_type='user',
            sender_id=user_id,
            message=message_text or "[Attachment]",
            attachment_file_id=attachment_file_id
        )
        session.add(ticket_msg)
        
        # Update ticket status
        ticket.status = 'waiting_admin'
        ticket.updated_at = datetime.now()
        
        await session.commit()
    
    # Notify admins
    await notify_admins_ticket_reply(update.get_bot(), ticket_id, user_id, message_text, attachment_file_id)
    
    await update.message.reply_text(
        f"✅ **Reply sent to Ticket #{ticket_id}**\n\n"
        "Support will be notified.",
        parse_mode='Markdown'
    )
    
    context.user_data.clear()
    from bot_handler import start
    await start(update, context)
    return ConversationHandler.END

async def notify_admins_ticket_reply(bot, ticket_id, user_telegram_id, message, attachment_file_id):
    """Notify admins about ticket reply."""
    text = (
        f"💬 **Ticket #{ticket_id} - New Reply**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"From: User ID {user_telegram_id}\n\n"
        f"Message:\n{message[:500]}\n\n"
        f"Use /admin to respond."
    )
    
    keyboard = [[InlineKeyboardButton("📋 View Ticket", callback_data=f'admin_ticket_{ticket_id}')]]
    
    for admin_id in config.ADMIN_IDS:
        try:
            if attachment_file_id:
                await bot.send_photo(
                    chat_id=admin_id,
                    photo=attachment_file_id,
                    caption=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
            else:
                await bot.send_message(
                    chat_id=admin_id,
                    text=text,
                    reply_markup=InlineKeyboardMarkup(keyboard),
                    parse_mode='Markdown'
                )
        except Exception as e:
            print(f"Failed to notify admin {admin_id}: {e}")

async def close_ticket_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """User closes their ticket."""
    query = update.callback_query
    ticket_id = int(query.data.split('_')[2])
    
    await query.answer()
    
    user_id = update.effective_user.id
    
    async with AsyncSessionLocal() as session:
        # Get user
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        # Get ticket
        ticket = await session.get(Ticket, ticket_id)
        
        if not ticket or ticket.user_id != user.id:
            await query.edit_message_text("❌ Ticket not found.")
            return ConversationHandler.END
        
        ticket.status = 'closed'
        ticket.closed_at = datetime.now()
        await session.commit()
    
    await query.edit_message_text(
        f"✅ **Ticket #{ticket_id} Closed**\n\n"
        "Thank you for using our support!",
        parse_mode='Markdown'
    )
    return ConversationHandler.END

# --- Cancel Handler ---

async def cancel_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel ticket creation and return to support menu."""
    context.user_data.clear()
    if update.callback_query:
        await update.callback_query.answer()
        await support_menu(update, context)
    else:
        from bot_handler import start
        await start(update, context)
    return ConversationHandler.END

# --- Conversation Handler ---

support_ticket_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(support_menu, pattern='^support$'),
        MessageHandler(filters.Regex('^💬 Support$'), support_menu),
        CallbackQueryHandler(create_ticket_start, pattern='^ticket_create$'),
        CallbackQueryHandler(my_tickets, pattern='^ticket_list$'),
        CallbackQueryHandler(view_ticket, pattern='^ticket_view_'),
        CallbackQueryHandler(reply_to_ticket_start, pattern='^ticket_reply_'),
        CallbackQueryHandler(close_ticket_user, pattern='^ticket_close_'),
    ],
    states={
        TICKET_SUBJECT: [
            CallbackQueryHandler(select_ticket_subject, pattern='^ticket_subj_'),
            CallbackQueryHandler(select_ticket_subject, pattern='^ticket_custom_subject$'),
            CallbackQueryHandler(support_menu, pattern='^support$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_ticket_subject)
        ],
        TICKET_MESSAGE: [MessageHandler(filters.TEXT | filters.PHOTO, receive_ticket_message)],
        TICKET_REPLY: [MessageHandler(filters.TEXT | filters.PHOTO, receive_ticket_reply)],
    },
    fallbacks=[
        CommandHandler('cancel', cancel_ticket),
        CallbackQueryHandler(support_menu, pattern='^support$'),
    ]
)
