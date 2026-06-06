
from sqlalchemy import select, desc
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Ticket, TicketMessage, User
from datetime import datetime

async def get_tickets_by_filter(status_filter: str = 'active', limit: int = 50):
    """Fetch tickets based on status filter."""
    async with AsyncSessionLocal() as session:
        if status_filter == 'active':
            stmt = select(Ticket).where(Ticket.status != 'closed').order_by(desc(Ticket.updated_at))
        else:
            stmt = select(Ticket).where(Ticket.status == 'closed').order_by(desc(Ticket.updated_at))
        
        result = await session.execute(stmt.limit(limit))
        return result.scalars().all()

async def get_ticket_comprehensive(ticket_id: int):
    """Fetch ticket, user, and all messages."""
    async with AsyncSessionLocal() as session:
        ticket = await session.get(Ticket, ticket_id)
        if not ticket: return None, None, []
        
        user = await session.get(User, ticket.user_id)
        m_res = await session.execute(
            select(TicketMessage).where(TicketMessage.ticket_id == ticket_id).order_by(TicketMessage.created_at)
        )
        messages = m_res.scalars().all()
        return ticket, user, messages

async def add_ticket_message(ticket_id: int, sender_id: int, sender_type: str, message: str, attachment_file_id: str = None, attachment_type: str = None):
    """Add a message to a ticket and update ticket status/time.

    For user senders, ``sender_id`` is the DB user id and ownership is
    enforced (ticket.user_id must match). Admin senders bypass this check.
    """
    async with AsyncSessionLocal() as session:
        ticket = await session.get(Ticket, ticket_id)
        if not ticket: return False, "Ticket not found"

        if sender_type == 'user':
            # sender_id is a Telegram ID; look up the DB user to verify ownership
            db_user = await session.execute(
                select(User).where(User.telegram_id == sender_id)
            )
            db_user = db_user.scalars().first()
            if not db_user or ticket.user_id != db_user.id:
                return False, "Not your ticket"
        
        # Add message
        msg = TicketMessage(
            ticket_id=ticket_id,
            sender_id=sender_id,
            sender_type=sender_type,
            message=message or "[Attachment]",
            attachment_file_id=attachment_file_id,
            attachment_type=attachment_type
        )
        session.add(msg)
        
        # Update ticket
        ticket.status = 'waiting_user' if sender_type == 'admin' else 'waiting_admin'
        ticket.updated_at = datetime.now()
        
        await session.commit()
        
        # We need user telegram_id for notification
        user = await session.get(User, ticket.user_id)
        return True, user.telegram_id

async def close_ticket(ticket_id: int):
    """Close a ticket."""
    async with AsyncSessionLocal() as session:
        ticket = await session.get(Ticket, ticket_id)
        if not ticket: return False
        
        ticket.status = 'closed'
        ticket.closed_at = datetime.now()
        await session.commit()
        return True

async def get_ticket_notif_mode():
    """Fetch ticket notification mode from settings."""
    from vpn_bot.models import AdminSetting
    async with AsyncSessionLocal() as session:
        setting = await session.get(AdminSetting, 'ticket_notif_mode')
        return setting.value if setting else 'pv'

async def set_ticket_notif_mode(mode: str):
    """Update ticket notification mode in settings."""
    from vpn_bot.models import AdminSetting
    async with AsyncSessionLocal() as session:
        setting = await session.get(AdminSetting, 'ticket_notif_mode')
        if not setting:
            setting = AdminSetting(key='ticket_notif_mode', value=mode)
            session.add(setting)
        else:
            setting.value = mode
        await session.commit()
        return True

async def search_tickets_by_user(tg_id: int):
    """Find a user and return their tickets."""
    async with AsyncSessionLocal() as session:
        u_res = await session.execute(select(User).where(User.telegram_id == tg_id))
        user = u_res.scalars().first()
        if not user: return None, []
        
        t_res = await session.execute(
            select(Ticket).where(Ticket.user_id == user.id).order_by(desc(Ticket.updated_at))
        )
        return user, t_res.scalars().all()

async def create_outbound_ticket(user_id: int, admin_id: int, subject: str, message: str, attachment_file_id: str = None, attachment_type: str = None):
    """Create a new ticket from admin to user."""
    async with AsyncSessionLocal() as session:
        ticket = Ticket(
            user_id=user_id,
            subject=subject,
            status='waiting_user'
        )
        session.add(ticket)
        await session.flush()
        
        msg = TicketMessage(
            ticket_id=ticket.id,
            sender_id=admin_id,
            sender_type='admin',
            message=message or "[Attachment]",
            attachment_file_id=attachment_file_id,
            attachment_type=attachment_type
        )
        session.add(msg)
        await session.commit()
        return ticket.id

async def count_tickets_needing_admin() -> int:
    """Count tickets awaiting admin action (open or waiting_admin)."""
    from sqlalchemy import func
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count())
            .select_from(Ticket)
            .where(Ticket.status.in_(("open", "waiting_admin")))
        )
        return int(result.scalar() or 0)


async def get_open_ticket_count(user_id: int):
    """Count open/waiting tickets for a specific user ID."""
    from sqlalchemy import and_
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(Ticket).where(
                and_(Ticket.user_id == user_id, Ticket.status != 'closed')
            )
        )
        return len(res.scalars().all())

async def get_user_tickets(user_id: int):
    """Fetch all tickets for a specific user ID, ordered by creation."""
    async with AsyncSessionLocal() as session:
        t_res = await session.execute(
            select(Ticket)
            .where(Ticket.user_id == user_id)
            .order_by(desc(Ticket.created_at))
        )
        return t_res.scalars().all()

async def create_ticket_from_user(user_id: int, subject: str, message: str, attachment_file_id: str = None, attachment_type: str = None):
    """Create a new ticket from a user."""
    async with AsyncSessionLocal() as session:
        ticket = Ticket(
            user_id=user_id,
            subject=subject,
            status='open',
            priority='medium'
        )
        session.add(ticket)
        await session.flush()
        
        msg = TicketMessage(
            ticket_id=ticket.id,
            sender_type='user',
            sender_id=user_id, # This is wrong in the original code? No, TicketMessage.sender_id is generic
            message=message or "[Attachment]",
            attachment_file_id=attachment_file_id,
            attachment_type=attachment_type
        )
        session.add(msg)
        await session.commit()
        return ticket.id
