
from sqlalchemy import select, func
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import User, Subscription, Transaction, WireGuardSubscription
from datetime import datetime

async def get_revenue_stats(start_date: datetime = None):
    """Fetch sum of purchase amounts grouped by currency."""
    async with AsyncSessionLocal() as session:
        stmt = select(Transaction.currency_unit, func.sum(Transaction.amount)).where(Transaction.type == 'purchase')
        if start_date:
            stmt = stmt.where(Transaction.created_at >= start_date)
        stmt = stmt.group_by(Transaction.currency_unit)
        res = await session.execute(stmt)
        return {unit: amount for unit, amount in res.all()}

async def get_subscription_stats():
    """Fetch total and active counts for both Regular and WireGuard subscriptions."""
    async with AsyncSessionLocal() as session:
        now = datetime.now()
        
        # Regular
        total_res = await session.execute(select(func.count(Subscription.id)))
        total_subs = total_res.scalar() or 0
        active_res = await session.execute(select(func.count(Subscription.id)).where(Subscription.expiry_date > now))
        active_subs = active_res.scalar() or 0
        
        # WireGuard
        total_wg_res = await session.execute(select(func.count(WireGuardSubscription.id)))
        total_wg_subs = total_wg_res.scalar() or 0
        active_wg_res = await session.execute(select(func.count(WireGuardSubscription.id)).where(WireGuardSubscription.expiry_date > now))
        active_wg_subs = active_wg_res.scalar() or 0
        
        return {
            'total_regular': total_subs,
            'active_regular': active_subs,
            'total_wg': total_wg_subs,
            'active_wg': active_wg_subs,
            'total_all': total_subs + total_wg_subs,
            'active_all': active_subs + active_wg_subs
        }

async def get_user_stats():
    """Fetch total users and users joined today."""
    async with AsyncSessionLocal() as session:
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
        
        total_res = await session.execute(select(func.count(User.id)))
        total_users = total_res.scalar() or 0
        
        new_res = await session.execute(select(func.count(User.id)).where(User.created_at >= today_start))
        new_today = new_res.scalar() or 0
        
        return {
            'total': total_users,
            'new_today': new_today
        }

async def get_sales_report_data():
    """Fetch all purchase transactions with TG IDs for CSV export."""
    async with AsyncSessionLocal() as session:
        stmt = (
            select(Transaction, User.telegram_id.label('tg_id'))
            .join(User, Transaction.user_id == User.id)
            .where(Transaction.type == 'purchase')
            .order_by(Transaction.created_at.desc())
        )
        results = await session.execute(stmt)
        return results.all()
