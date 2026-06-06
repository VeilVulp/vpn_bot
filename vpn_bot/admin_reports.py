"""
Admin Reports & Analytics Module
Provides sales summaries, revenue statistics, and CSV exports.
"""

import io
import csv
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

from vpn_bot.admin_report_service import (
    get_revenue_stats, get_subscription_stats, get_user_stats, get_sales_report_data
)
from vpn_bot.admin_management import is_user_admin
from vpn_bot.admin_permissions import has_admin_perm, PERM_REPORTS
from vpn_bot.utils import LanguageManager, get_currency_unit, format_currency

async def sales_dashboard(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored sales_dashboard using admin_report_service."""
    query = update.callback_query
    if query: await query.answer()
    
    uid = update.effective_user.id if update.effective_user else None
    if not uid or not await is_user_admin(uid) or not await has_admin_perm(uid, PERM_REPORTS):
        if query: await query.edit_message_text(LanguageManager.get('admin.access_denied'))
        return

    from datetime import timedelta
    now = datetime.now()
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start - timedelta(days=30)
    # Actually simpler to just pass dates from service if needed, but let's stick to the current logic

    total_rev_map = await get_revenue_stats()
    today_rev_map = await get_revenue_stats(today_start)
    month_rev_map = await get_revenue_stats(today_start - timedelta(days=30))

    async def format_rev_map(rev_map):
        if not rev_map: 
            curr = await get_currency_unit()
            return await format_currency(0, unit=curr)
        parts = []
        for unit, amount in rev_map.items():
            parts.append(await format_currency(abs(amount or 0), unit=unit))
        return " + ".join(parts)

    total_rev_str = await format_rev_map(total_rev_map)
    today_rev_str = await format_rev_map(today_rev_map)
    month_rev_str = await format_rev_map(month_rev_map)

    sub_stats = await get_subscription_stats()
    user_stats = await get_user_stats()

    text = LanguageManager.get('admin.reports.dashboard',
        total_rev=total_rev_str,
        today_rev=today_rev_str,
        month_rev=month_rev_str,
        total_subs=sub_stats['total_all'],
        active_subs=sub_stats['active_all'],
        expired_subs=sub_stats['total_all'] - sub_stats['active_all'],
        total_users=user_stats['total'],
        new_today=user_stats['new_today'],
        timestamp=now.strftime("%Y-%m-%d %H:%M:%S")
    )

    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.reports.btn_export'), callback_data='report_export_sales')],
        [InlineKeyboardButton(LanguageManager.get('admin.reports.btn_back'), callback_data='admin_start')],
    ]

    if query:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def export_sales_csv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export sales data as CSV — requires PERM_REPORTS."""
    query = update.callback_query
    uid = update.effective_user.id if update.effective_user else None
    if not uid or not await is_user_admin(uid) or not await has_admin_perm(uid, PERM_REPORTS):
        await query.answer(LanguageManager.get('admin.access_denied'), show_alert=True)
        return

    await query.answer(LanguageManager.get('admin.reports.generating'))

    rows = await get_sales_report_data()

    # Create CSV in memory
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['ID', 'Telegram ID', 'Amount', 'Currency', 'Date', 'Description'])

    for r in rows:
        txn = r.Transaction
        writer.writerow([txn.id, r.tg_id, abs(txn.amount), getattr(txn, 'currency_unit', 'USD'), txn.created_at.strftime("%Y-%m-%d %H:%M"), txn.description])

    output.seek(0)
    
    await context.bot.send_document(
        chat_id=update.effective_chat.id,
        document=io.BytesIO(output.getvalue().encode('utf-8')),
        filename=f"sales_report_{datetime.now().strftime('%Y%m%d')}.csv",
        caption=LanguageManager.get('admin.reports.csv_caption'),
        parse_mode='Markdown'
    )
