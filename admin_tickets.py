"""
Admin Ticket Management
Handles admin responses to support tickets.
"""

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler, MessageHandler, filters, CallbackQueryHandler
from sqlalchemy import select, desc, and_
from datetime import datetime

from database import AsyncSessionLocal
from models import User, Ticket, TicketMessage
from config import config

# States
ADMIN_TICKET_REPLY = range(1)

async def admin_list_tickets(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all tickets for admin."""
    query = update.callback_query
    await query.answer()
    
    async with AsyncSessionLocal() as session:
        # Get all tickets
        t_res = await session.execute(
            select(Ticket).order_by(desc(Ticket.updated_at)).limit(20)
        )
        tickets = t_res.scalars().all()
    
    if not tickets:
        text = "📋 **Support Tickets**\n\nNo tickets yet."
        keyboard = [[InlineKeyboardButton("🔙 Admin Panel", callback_data='admin_start')]]
    else:
        text = "📋 **Support Tickets**\n━━━━━━━━━━━━━━━━━━━━\n\n"
        keyboard = []
        
        # Group by status
        open_tickets = [t for t in tickets if t.status in ['open', 'waiting_admin']]
        waiting_user = [t for t in tickets if t.status == 'waiting_user']
        closed_tickets = [t for t in tickets if t.status == 'closed']
        
        if open_tickets:
            text += f"🔴 **Needs Response ({len(open_tickets)}):**\n\n"
            for ticket in open_tickets[:5]:
                text += (
                    f"**Ticket #{ticket.id}**\n"
                    f"{ticket.subject[:40]}\n"
                    f"Updated: {ticket.updated_at.strftime('%m/%d %H:%M')}\n"
                    f"━━━━━━━━━━━━━━━━━━━━\n"
                )
                keyboard.append([InlineKeyboardButton(f"📖 #{ticket.id}", callback_data=f'admin_ticket_{ticket.id}')])
        
        if waiting_user:
            text += f"\n⏳ **Waiting User ({len(waiting_user)}):**\n\n"
            for ticket in waiting_user[:3]:
                text += f"#{ticket.id}: {ticket.subject[:30]}\n"
        
        if closed_tickets:
            text += f"\n✅ **Closed ({len(closed_tickets)}):**\n\n"
            for ticket in closed_tickets[:3]:
                text += f"#{ticket.id}: {ticket.subject[:30]}\n"
        
        keyboard.append([InlineKeyboardButton("🔙 Admin Panel", callback_data='admin_start')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def admin_view_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin views ticket details."""
    query = update.callback_query
    ticket_id = int(query.data.split('_')[2])
    
    await query.answer()
    
    async with AsyncSessionLocal() as session:
        # Get ticket
        ticket = await session.get(Ticket, ticket_id)
        
        if not ticket:
            await query.edit_message_text("❌ Ticket not found.")
            return ConversationHandler.END
        
        # Get user
        user = await session.get(User, ticket.user_id)
        
        # Get messages
        m_res = await session.execute(
            select(TicketMessage)
            .where(TicketMessage.ticket_id == ticket_id)
            .order_by(TicketMessage.created_at)
        )
        messages = m_res.scalars().all()
    
    status_emoji = {
        'open': '🟢',
        'waiting_admin': '🔴',
        'waiting_user': '⏳',
        'closed': '✅'
    }.get(ticket.status, '🔵')
    
    text = (
        f"{status_emoji} **Ticket #{ticket.id}**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"👤 **User:** {user.full_name or 'N/A'}\n"
        f"📱 **Phone:** `{user.phone_number or 'Not Shared'}`\n"
        f"🆔 **TG ID:** `{user.telegram_id}`\n"
        f"📝 **Subject:** {ticket.subject}\n"
        f"📊 **Status:** {ticket.status.replace('_', ' ').title()}\n"
        f"📅 **Created:** {ticket.created_at.strftime('%Y-%m-%d %H:%M')}\n\n"
        f"💬 **Conversation:**\n\n"
    )
    
    for msg in messages:
        sender = "👤 User" if msg.sender_type == 'user' else "👨‍💼 Admin"
        time = msg.created_at.strftime('%m/%d %H:%M')
        text += f"{sender} ({time}):\n{msg.message[:200]}\n\n"
    
    keyboard = []
    
    if ticket.status != 'closed':
        keyboard.append([InlineKeyboardButton("💬 Reply", callback_data=f'admin_reply_{ticket_id}')])
        keyboard.append([InlineKeyboardButton("✅ Close Ticket", callback_data=f'admin_close_{ticket_id}')])
    
    keyboard.append([InlineKeyboardButton("🔙 Tickets", callback_data='admin_tickets')])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return ConversationHandler.END

async def admin_reply_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin starts replying to ticket."""
    query = update.callback_query
    ticket_id = int(query.data.split('_')[2])
    
    context.user_data['admin_reply_ticket_id'] = ticket_id
    
    await query.answer()
    await query.edit_message_text(
        f"💬 **Reply to Ticket #{ticket_id}**\n\n"
        "Type your response:\n\n"
        "Or /cancel to abort",
        parse_mode='Markdown'
    )
    return ADMIN_TICKET_REPLY

async def admin_receive_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin sends reply to ticket."""
    admin_id = update.effective_user.id
    message_text = update.message.text or update.message.caption or ""
    attachment_file_id = None
    
    if update.message.photo:
        attachment_file_id = update.message.photo[-1].file_id
    
    ticket_id = context.user_data.get('admin_reply_ticket_id')
    
    async with AsyncSessionLocal() as session:
        # Get ticket
        ticket = await session.get(Ticket, ticket_id)
        
        if not ticket:
            await update.message.reply_text("❌ Ticket not found.")
            return ConversationHandler.END
        
        # Get user
        user = await session.get(User, ticket.user_id)
        
        # Add message
        ticket_msg = TicketMessage(
            ticket_id=ticket_id,
            sender_type='admin',
            sender_id=admin_id,
            message=message_text or "[Attachment]",
            attachment_file_id=attachment_file_id
        )
        session.add(ticket_msg)
        
        # Update ticket status
        ticket.status = 'waiting_user'
        ticket.updated_at = datetime.now()
        
        await session.commit()
        
        user_telegram_id = user.telegram_id
    
    # Notify user
    await notify_user_admin_reply(update.get_bot(), user_telegram_id, ticket_id, message_text, attachment_file_id)
    
    await update.message.reply_text(
        f"✅ **Reply sent to Ticket #{ticket_id}**\n\n"
        "User will be notified.",
        parse_mode='Markdown'
    )
    
    context.user_data.clear()
    from admin_panel import admin_start
    await admin_start(update, context)
    return ConversationHandler.END

async def notify_user_admin_reply(bot, user_telegram_id, ticket_id, message, attachment_file_id):
    """Notify user about admin reply."""
    text = (
        f"💬 **Support Reply - Ticket #{ticket_id}**\n"
        f"━━━━━━━━━━━━━━━━━━━━\n\n"
        f"Admin response:\n\n"
        f"{message}\n\n"
        f"Use /start → Support to view and reply."
    )
    
    keyboard = [[InlineKeyboardButton("📖 View Ticket", callback_data=f'ticket_view_{ticket_id}')]]
    
    try:
        if attachment_file_id:
            await bot.send_photo(
                chat_id=user_telegram_id,
                photo=attachment_file_id,
                caption=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
        else:
            await bot.send_message(
                chat_id=user_telegram_id,
                text=text,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode='Markdown'
            )
    except Exception as e:
        print(f"Failed to notify user {user_telegram_id}: {e}")

async def admin_close_ticket(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Admin closes ticket."""
    query = update.callback_query
    ticket_id = int(query.data.split('_')[2])
    
    await query.answer()
    
    async with AsyncSessionLocal() as session:
        ticket = await session.get(Ticket, ticket_id)
        
        if not ticket:
            await query.edit_message_text("❌ Ticket not found.")
            return ConversationHandler.END
        
        ticket.status = 'closed'
        ticket.closed_at = datetime.now()
        await session.commit()
    
    await query.edit_message_text(
        f"✅ **Ticket #{ticket_id} Closed**\n\n"
        "Ticket has been marked as resolved.",
        parse_mode='Markdown'
    )
    return ConversationHandler.END

# --- Conversation Handler ---

admin_ticket_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(admin_list_tickets, pattern='^admin_tickets$'),
        CallbackQueryHandler(admin_view_ticket, pattern='^admin_ticket_'),
        CallbackQueryHandler(admin_reply_start, pattern='^admin_reply_'),
        CallbackQueryHandler(admin_close_ticket, pattern='^admin_close_'),
    ],
    states={
        ADMIN_TICKET_REPLY: [MessageHandler(filters.TEXT | filters.PHOTO, admin_receive_reply)],
    },
    fallbacks=[
        CallbackQueryHandler(admin_list_tickets, pattern='^admin_tickets$'),
    ]
)
