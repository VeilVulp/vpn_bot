"""
Admin Reports & Analytics Module
Provides sales summaries, revenue statistics, and CSV exports.
"""

import io
import csv
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from sqlalchemy import select, func, and_

from database import AsyncSessionLocal
from models import User, Subscription, Transaction, Profile

async def sales_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display comprehensive sales dashboard."""
    query = update.callback_query
    if query:
        await query.answer()

    # Time ranges
    now = datetime.utcnow()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    week_start = today_start - timedelta(days=7)
    month_start = today_start - timedelta(days=30)

    async with AsyncSessionLocal() as session:
        # 1. Revenue Stats (from Transactions type='purchase')
        # Total
        total_rev = await session.execute(
            select(func.sum(Transaction.amount)).where(Transaction.type == 'purchase')
        )
        total_rev = total_rev.scalar() or 0.0

        # Today
        today_rev = await session.execute(
            select(func.sum(Transaction.amount)).where(
                and_(Transaction.type == 'purchase', Transaction.created_at >= today_start)
            )
        )
        today_rev = today_rev.scalar() or 0.0

        # Month
        month_rev = await session.execute(
            select(func.sum(Transaction.amount)).where(
                and_(Transaction.type == 'purchase', Transaction.created_at >= month_start)
            )
        )
        month_rev = month_rev.scalar() or 0.0

        # 2. Subscription counts
        total_subs = await session.execute(select(func.count(Subscription.id)))
        total_subs = total_subs.scalar() or 0

        active_subs = await session.execute(
            select(func.count(Subscription.id)).where(Subscription.expiry_date > now)
        )
        active_subs = active_subs.scalar() or 0

        # 3. User Growth
        total_users = await session.execute(select(func.count(User.id)))
        total_users = total_users.scalar() or 0

        new_users_today = await session.execute(
            select(func.count(User.id)).where(User.created_at >= today_start)
        )
        new_users_today = new_users_today.scalar() or 0

    text = (
        "📊 **Sales & Analytics Dashboard**\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💰 **Revenue Summary**\n"
        f"• Total Revenue: `${total_rev:,.2f}`\n"
        f"• Rev Today: `${today_rev:,.2f}`\n"
        f"• Rev Last 30d: `${month_rev:,.2f}`\n\n"
        "📱 **Subscription Status**\n"
        f"• Total Accounts: `{total_subs}`\n"
        f"• 🟢 Active: `{active_subs}`\n"
        f"• 🔴 Expired: `{total_subs - active_subs}`\n\n"
        "👥 **User Growth**\n"
        f"• Total Users: `{total_users}`\n"
        f"• New Today: `{new_users_today}`\n\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Last update: " + now.strftime("%Y-%m-%d %H:%M:%S") + " UTC"
    )

    keyboard = [
        [InlineKeyboardButton("📝 Export Sales CSV", callback_data='report_export_sales')],
        [InlineKeyboardButton("🔙 Back to Admin", callback_data='admin_start')],
    ]

    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def export_sales_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Generate and send CSV report of all sales."""
    query = update.callback_query
    await query.answer("Generating CSV report...")

    async with AsyncSessionLocal() as session:
        # Join Transaction with User for better reporting
        stmt = (
            select(Transaction, User.telegram_id.label('tg_id'))
            .join(User, Transaction.user_id == User.id)
            .where(Transaction.type == 'purchase')
            .order_by(Transaction.created_at.desc())
        )
        results = await session.execute(stmt)
        rows = results.all()

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Telegram ID', 'Amount', 'Date', 'Description'])

    for r in rows:
        txn = r.Transaction
        writer.writerow([txn.id, r.tg_id, txn.amount, txn.created_at.strftime("%Y-%m-%d %H:%M"), txn.description])

    output.seek(0)
    
    # Send as document
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=io.BytesIO(output.getvalue().encode('utf-8')),
        filename=f"sales_report_{datetime.now().strftime('%Y%m%d')}.csv",
        caption="📄 **Full Sales Report (CSV)**\nContains all purchase transactions.",
        parse_mode='Markdown'
    )
