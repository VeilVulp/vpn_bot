import logging
import asyncio
from datetime import datetime, timedelta
from sqlalchemy import select, delete, func, and_, or_
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import User, Subscription, Transaction, PaymentReceipt, ReceiptNotification, Ticket, TicketMessage, Server
from vpn_bot.mikrotik_manager import MikroTikManager

logger = logging.getLogger(__name__)
from vpn_bot.settings_utils import get_admin_setting, set_admin_setting
from vpn_bot.utils import LanguageManager, parse_duration_to_seconds

async def get_cleanup_thresholds():
    """Retrieve current cleanup thresholds from admin settings (returned in seconds)."""
    # Default durations (mostly in days)
    defaults = {
        'subs': '3d',
        'receipts': '5d',
        'tx': '180d', # 6 months approx
        'tickets': '7d',
        'inactive_users': '30d'
    }
    
    custom = await get_admin_setting('cleanup_settings', {})
    if not isinstance(custom, dict):
        custom = {}
        
    thresholds_seconds = {}
    for key, default_val in defaults.items():
        val = custom.get(key, default_val)
        thresholds_seconds[key] = parse_duration_to_seconds(val)
        
    return thresholds_seconds

async def get_db_health_stats():
    """Get statistics about items eligible for cleanup."""
    async with AsyncSessionLocal() as session:
        now = datetime.now()
        thresholds = await get_cleanup_thresholds()
        
        # 1. Expired Subscriptions
        expired_threshold = now - timedelta(seconds=thresholds['subs'])
        res_expired = await session.execute(
            select(func.count(Subscription.id)).where(
                or_(
                    Subscription.expiry_date < expired_threshold,
                    Subscription.status == 'inconsistent',
                    Subscription.status == 'disabled'
                )
            )
        )
        expired_count = res_expired.scalar() or 0
        
        # 2. Pending Receipts
        receipt_threshold = now - timedelta(seconds=thresholds['receipts'])
        res_receipts = await session.execute(
            select(func.count(PaymentReceipt.id)).where(
                and_(PaymentReceipt.status == 'pending', PaymentReceipt.submitted_at < receipt_threshold)
            )
        )
        pending_receipts_count = res_receipts.scalar() or 0
        
        # 3. Old Transactions
        tx_threshold = now - timedelta(seconds=thresholds['tx'])
        res_tx = await session.execute(
            select(func.count(Transaction.id)).where(Transaction.created_at < tx_threshold)
        )
        old_tx_count = res_tx.scalar() or 0
        
        # 4. Closed Tickets
        ticket_threshold = now - timedelta(seconds=thresholds['tickets'])
        res_tickets = await session.execute(
            select(func.count(Ticket.id)).where(
                and_(Ticket.status == 'closed', Ticket.closed_at < ticket_threshold)
            )
        )
        closed_tickets_count = res_tickets.scalar() or 0
        
        # 5. Inactive Users
        user_threshold = now - timedelta(seconds=thresholds['inactive_users'])
        # Complex query: users with no subscriptions
        res_users = await session.execute(
            select(func.count(User.id)).where(
                and_(
                    User.wallet_balance == 0,
                    User.created_at < user_threshold,
                    ~User.subscriptions.any()
                )
            )
        )
        inactive_users_count = res_users.scalar() or 0
        
        # 1b. Expired/Inactive WireGuard Subscriptions
        from vpn_bot.models import WireGuardSubscription
        res_wg_expired = await session.execute(
            select(func.count(WireGuardSubscription.id)).where(
                or_(
                    WireGuardSubscription.expiry_date < expired_threshold,
                    WireGuardSubscription.status == 'inconsistent',
                    WireGuardSubscription.status == 'disabled'
                )
            )
        )
        wg_expired_count = res_wg_expired.scalar() or 0
        
        return {
            'expired_subs': expired_count,
            'expired_wg_subs': wg_expired_count,
            'pending_receipts': pending_receipts_count,
            'old_transactions': old_tx_count,
            'closed_tickets': closed_tickets_count,
            'inactive_users': inactive_users_count
        }

async def clean_pending_receipts_service(days=5):
    """Delete stale pending receipts; skip those archived in the receipt Telegram group."""
    from vpn_bot.admin_settings_service import get_receipt_group_id

    group_id = await get_receipt_group_id()
    async with AsyncSessionLocal() as session:
        threshold = datetime.now() - timedelta(days=days)
        excluded_ids: set[int] = set()
        if group_id:
            res = await session.execute(
                select(ReceiptNotification.receipt_id).where(
                    ReceiptNotification.admin_id == int(group_id)
                )
            )
            excluded_ids = set(res.scalars().all())

        conditions = and_(
            PaymentReceipt.status == 'pending',
            PaymentReceipt.submitted_at < threshold,
        )
        if excluded_ids:
            conditions = and_(conditions, PaymentReceipt.id.notin_(excluded_ids))

        stmt = delete(PaymentReceipt).where(conditions)
        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount

async def clean_old_transactions_service(months=6):
    async with AsyncSessionLocal() as session:
        threshold = datetime.now() - timedelta(days=months*30)
        stmt = delete(Transaction).where(Transaction.created_at < threshold)
        res = await session.execute(stmt)
        await session.commit()
        return res.rowcount

async def clean_closed_tickets_service(days=7):
    async with AsyncSessionLocal() as session:
        threshold = datetime.now() - timedelta(days=days)
        res = await session.execute(select(Ticket.id).where(and_(Ticket.status == 'closed', Ticket.closed_at < threshold)))
        ticket_ids = res.scalars().all()
        if not ticket_ids: return 0
        await session.execute(delete(TicketMessage).where(TicketMessage.ticket_id.in_(ticket_ids)))
        res = await session.execute(delete(Ticket).where(Ticket.id.in_(ticket_ids)))
        await session.commit()
        return res.rowcount

async def clean_inactive_users_service(days=30):
    async with AsyncSessionLocal() as session:
        threshold = datetime.now() - timedelta(days=days)
        res = await session.execute(
            select(User.id).where(
                and_(User.wallet_balance == 0, User.created_at < threshold, ~User.subscriptions.any())
            )
        )
        user_ids = res.scalars().all()
        if not user_ids: return 0
        await session.execute(delete(Transaction).where(Transaction.user_id.in_(user_ids)))
        await session.execute(delete(PaymentReceipt).where(PaymentReceipt.user_id.in_(user_ids)))
        await session.execute(delete(User).where(User.id.in_(user_ids)))
        await session.commit()
        return len(user_ids)
