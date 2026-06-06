
import asyncio
from datetime import datetime

from sqlalchemy import select, func as sa_func, or_, desc
from sqlalchemy.orm import joinedload
from telegram import InlineKeyboardButton, InlineKeyboardMarkup

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import (
    User, Subscription, WireGuardSubscription, Ticket, Server, Transaction, PaymentReceipt,
)
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.utils import LanguageManager, format_currency, format_datetime, logger
from telegram.helpers import escape_markdown

SUBS_PAGE_SIZE = 5
MAX_SEARCH_RESULTS = 8


async def find_users_by_query(query_text: str, limit: int = MAX_SEARCH_RESULTS):
    """Return up to ``limit`` users matching search criteria."""
    q = (query_text or "").strip()
    if not q:
        return []

    async with AsyncSessionLocal() as session:
        users: list[User] = []
        seen: set[int] = set()

        def add(user):
            if user and user.id not in seen:
                seen.add(user.id)
                users.append(user)

        # Telegram ID (exact)
        if q.isdigit() and len(q) < 12:
            tid = int(q)
            add(
                (await session.execute(select(User).where(User.telegram_id == tid)))
                .scalars()
                .first()
            )

        # @username or username
        uname = q.lstrip("@").lower()
        if uname:
            res = await session.execute(
                select(User).where(
                    or_(
                        User.username.ilike(uname),
                        User.username.ilike(f"@{uname}"),
                    )
                ).limit(limit)
            )
            for u in res.scalars().all():
                add(u)

        # Phone
        if len(users) < limit and (q.startswith("+") or (q.isdigit() and len(q) >= 10)):
            res = await session.execute(
                select(User).where(User.phone_number.contains(q)).limit(limit)
            )
            for u in res.scalars().all():
                add(u)

        # OVPN username (exact)
        if len(users) < limit:
            sub = (
                await session.execute(
                    select(Subscription).where(Subscription.mikrotik_username == q)
                )
            ).scalars().first()
            if sub:
                add(await session.get(User, sub.user_id))

        # WG identifier (exact)
        if len(users) < limit:
            wg_sub = (
                await session.execute(
                    select(WireGuardSubscription).where(
                        WireGuardSubscription.unique_identifier == q
                    )
                )
            ).scalars().first()
            if wg_sub:
                add(await session.get(User, wg_sub.user_id))

        # Full name (partial, multiple)
        if len(users) < limit and len(q) >= 2:
            res = await session.execute(
                select(User).where(User.full_name.ilike(f"%{q}%")).limit(limit)
            )
            for u in res.scalars().all():
                add(u)
                if len(users) >= limit:
                    break

        return users[:limit]


async def find_user_by_query(query_text: str):
    """Single-user search; returns first match."""
    users = await find_users_by_query(query_text, limit=1)
    return users[0] if users else None


def format_user_pick_label(user, *, max_len: int = 64) -> str:
    """Human-readable label for admin user picker buttons (recent / search results)."""
    name = None
    if user.full_name and str(user.full_name).strip():
        name = str(user.full_name).strip()
    else:
        fn = (user.first_name or "").strip()
        ln = (user.last_name or "").strip()
        combined = f"{fn} {ln}".strip()
        if combined:
            name = combined

    phone = (user.phone_number or "").strip() if user.phone_number else ""
    username = (user.username or "").strip()
    if username and not username.startswith("@"):
        username = f"@{username}"

    if name and phone:
        label = f"{name} · {phone}"
    elif name:
        label = name
    elif phone:
        label = phone
    elif username:
        label = username
    else:
        label = str(user.telegram_id)

    return label[:max_len]


async def get_recent_users(limit: int = 10):
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(User).order_by(desc(User.created_at)).limit(limit)
        )
        return res.scalars().all()


async def get_user_by_id(user_id: int):
    async with AsyncSessionLocal() as session:
        return await session.get(User, user_id)


async def get_user_by_tg_id(tg_id: int):
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.telegram_id == tg_id))
        return result.scalars().first()


async def _count_subs(model, user_id: int, active: bool):
    async with AsyncSessionLocal() as session:
        q = select(sa_func.count()).select_from(model).where(model.user_id == user_id)
        if active:
            q = q.where(model.status == "active")
        else:
            q = q.where(model.status != "active")
        return (await session.execute(q)).scalar() or 0


async def get_user_comprehensive_info(
    user_id: int,
    *,
    ovpn_page: int = 0,
    wg_page: int = 0,
    subs_page_size: int = SUBS_PAGE_SIZE,
):
    async with AsyncSessionLocal() as session:
        active_subs = (
            await session.execute(
                select(sa_func.count())
                .select_from(Subscription)
                .where(Subscription.user_id == user_id, Subscription.status == "active")
            )
        ).scalar() or 0
        expired_subs = (
            await session.execute(
                select(sa_func.count())
                .select_from(Subscription)
                .where(Subscription.user_id == user_id, Subscription.status != "active")
            )
        ).scalar() or 0
        active_wg = (
            await session.execute(
                select(sa_func.count())
                .select_from(WireGuardSubscription)
                .where(
                    WireGuardSubscription.user_id == user_id,
                    WireGuardSubscription.status == "active",
                )
            )
        ).scalar() or 0
        expired_wg = (
            await session.execute(
                select(sa_func.count())
                .select_from(WireGuardSubscription)
                .where(
                    WireGuardSubscription.user_id == user_id,
                    WireGuardSubscription.status != "active",
                )
            )
        ).scalar() or 0
        total_ovpn = (
            await session.execute(
                select(sa_func.count())
                .select_from(Subscription)
                .where(Subscription.user_id == user_id)
            )
        ).scalar() or 0
        total_wg = (
            await session.execute(
                select(sa_func.count())
                .select_from(WireGuardSubscription)
                .where(WireGuardSubscription.user_id == user_id)
            )
        ).scalar() or 0
        ticket_count = (
            await session.execute(
                select(sa_func.count()).select_from(Ticket).where(Ticket.user_id == user_id)
            )
        ).scalar() or 0
        pending_receipts = (
            await session.execute(
                select(sa_func.count())
                .select_from(PaymentReceipt)
                .where(
                    PaymentReceipt.user_id == user_id,
                    PaymentReceipt.status == "pending",
                )
            )
        ).scalar() or 0

        ovpn_offset = ovpn_page * subs_page_size
        wg_offset = wg_page * subs_page_size
        ovpn_subs = (
            await session.execute(
                select(Subscription)
                .where(Subscription.user_id == user_id)
                .order_by(desc(Subscription.expiry_date))
                .offset(ovpn_offset)
                .limit(subs_page_size)
            )
        ).scalars().all()
        wg_subs = (
            await session.execute(
                select(WireGuardSubscription)
                .where(WireGuardSubscription.user_id == user_id)
                .order_by(desc(WireGuardSubscription.expiry_date))
                .offset(wg_offset)
                .limit(subs_page_size)
            )
        ).scalars().all()

        return {
            "active_subs": active_subs,
            "expired_subs": expired_subs,
            "active_wg": active_wg,
            "expired_wg": expired_wg,
            "total_ovpn": total_ovpn,
            "total_wg": total_wg,
            "ticket_count": ticket_count,
            "pending_receipts": pending_receipts,
            "ovpn_subs": ovpn_subs,
            "wg_subs": wg_subs,
            "ovpn_page": ovpn_page,
            "wg_page": wg_page,
            "subs_page_size": subs_page_size,
        }


async def format_user_info_text(user, data):
    balance_display = await format_currency(user.wallet_balance)
    tg_user = f"@{user.username}" if user.username else LanguageManager.get("common.na")

    text = LanguageManager.get(
        "admin.user.full_info_title",
        telegram_id=str(user.telegram_id),
        tg_username=tg_user,
        full_name=escape_markdown(user.full_name or "N/A", version=1),
        phone=user.phone_number or LanguageManager.get("common.na"),
        balance=balance_display,
        banned=LanguageManager.get("admin.user.banned_yes")
        if user.is_banned
        else LanguageManager.get("admin.user.banned_no"),
        active_subs=str(data["active_subs"]),
        expired_subs=str(data["expired_subs"]),
        active_wg=str(data["active_wg"]),
        expired_wg=str(data["expired_wg"]),
        ticket_count=str(data["ticket_count"]),
        pending_receipts=str(data.get("pending_receipts", 0)),
    )

    for s in data["ovpn_subs"]:
        text += LanguageManager.get(
            "admin.user.sub_detail",
            type="OVPN/L2TP",
            name=s.mikrotik_username,
            status=LanguageManager.get("status.active")
            if s.status == "active"
            else LanguageManager.get("status.disabled"),
            expiry=await format_datetime(s.expiry_date, include_time=False)
            if s.expiry_date
            else LanguageManager.get("common.na"),
            used=f"{(s.used_bytes or 0) / (1024**3):.2f}",
            total=f"{(s.total_limit_bytes or 0) / (1024**3):.0f}",
            bar="█" * int(
                min(((s.used_bytes or 0) / max(s.total_limit_bytes or 1, 1)) * 10, 10)
            )
            + "░"
            * (
                10
                - int(
                    min(((s.used_bytes or 0) / max(s.total_limit_bytes or 1, 1)) * 10, 10)
                )
            ),
        )

    for ws in data["wg_subs"]:
        text += LanguageManager.get(
            "admin.user.sub_detail",
            type="WireGuard",
            name=ws.unique_identifier,
            status=LanguageManager.get("status.active")
            if ws.status == "active"
            else LanguageManager.get("status.disabled"),
            expiry=await format_datetime(ws.expiry_date, include_time=False)
            if ws.expiry_date
            else LanguageManager.get("common.na"),
            used=f"{((ws.total_bytes_rx or 0) + (ws.total_bytes_tx or 0)) / (1024**3):.2f}",
            total=f"{(ws.bytes_remaining or 0) / (1024**3):.0f}"
            if ws.bytes_remaining
            else "∞",
            bar="━" * 10,
        )
    return text


def build_user_hub_keyboard(user, data) -> InlineKeyboardMarkup:
    uid = user.id
    keyboard = []

    if user.is_banned:
        keyboard.append(
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.user.btn_unban"),
                    callback_data=f"unban_user_{uid}",
                )
            ]
        )
    else:
        keyboard.append(
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.user.btn_ban"),
                    callback_data=f"ban_user_{uid}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.user.btn_edit_balance"),
                callback_data=f"edit_balance_byuid_{uid}",
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.user.btn_notify"),
                callback_data=f"admin_notify_user_{uid}",
            )
        ]
    )

    inbox_row = []
    if data.get("ticket_count", 0) > 0:
        inbox_row.append(
            InlineKeyboardButton(
                LanguageManager.get("admin.user.btn_tickets"),
                callback_data=f"admin_user_tickets_{uid}",
            )
        )
    if data.get("pending_receipts", 0) > 0:
        inbox_row.append(
            InlineKeyboardButton(
                LanguageManager.get(
                    "admin.user.btn_receipts_pending",
                    count=data["pending_receipts"],
                ),
                callback_data=f"admin_user_receipts_{uid}",
            )
        )
    if inbox_row:
        keyboard.append(inbox_row)
    else:
        keyboard.append(
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.user.btn_tickets"),
                    callback_data=f"admin_user_tickets_{uid}",
                )
            ]
        )

    keyboard.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.user.btn_transactions"),
                callback_data=f"admin_user_txns_{uid}",
            )
        ]
    )

    for s in data["ovpn_subs"]:
        keyboard.append(
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.user.btn_manage_ovpn", name=s.mikrotik_username),
                    callback_data=f"manage_sub_{s.mikrotik_username}",
                )
            ]
        )

    ovpn_pages = max(
        1, (data["total_ovpn"] + data["subs_page_size"] - 1) // data["subs_page_size"]
    )
    if ovpn_pages > 1:
        nav = []
        if data["ovpn_page"] > 0:
            nav.append(
                InlineKeyboardButton(
                    "◀️ OVPN",
                    callback_data=f"user_ovpn_page_{uid}_{data['ovpn_page'] - 1}",
                )
            )
        if data["ovpn_page"] < ovpn_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    "OVPN ▶️",
                    callback_data=f"user_ovpn_page_{uid}_{data['ovpn_page'] + 1}",
                )
            )
        if nav:
            keyboard.append(nav)

    for ws in data["wg_subs"]:
        keyboard.append(
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.user.btn_manage_wg", name=ws.unique_identifier),
                    callback_data=f"manage_wg_{ws.id}",
                )
            ]
        )

    wg_pages = max(1, (data["total_wg"] + data["subs_page_size"] - 1) // data["subs_page_size"])
    if wg_pages > 1:
        nav = []
        if data["wg_page"] > 0:
            nav.append(
                InlineKeyboardButton(
                    "◀️ WG",
                    callback_data=f"user_wg_page_{uid}_{data['wg_page'] - 1}",
                )
            )
        if data["wg_page"] < wg_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    "WG ▶️",
                    callback_data=f"user_wg_page_{uid}_{data['wg_page'] + 1}",
                )
            )
        if nav:
            keyboard.append(nav)

    keyboard.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.user.btn_refresh"),
                callback_data=f"admin_user_hub_{uid}",
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.user.btn_delete_account"),
                callback_data=f"delete_account_{uid}",
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.user.btn_new_search"),
                callback_data="admin_user_search",
            ),
            InlineKeyboardButton(
                LanguageManager.get("common.back"),
                callback_data="admin_start",
            ),
        ]
    )
    return InlineKeyboardMarkup(keyboard)


def user_hub_back_markup(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.user.btn_back_hub"),
                    callback_data=f"admin_user_hub_{user_id}",
                )
            ]
        ]
    )


async def get_user_transactions(user_id: int, limit: int = 10):
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .order_by(desc(Transaction.created_at))
            .limit(limit)
        )
        return res.scalars().all()


async def get_user_receipts_summary(user_id: int, limit: int = 10):
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(PaymentReceipt)
            .where(PaymentReceipt.user_id == user_id)
            .order_by(desc(PaymentReceipt.submitted_at))
            .limit(limit)
        )
        return res.scalars().all()


async def update_user_balance(user_id: int, new_balance: float, log_adjust=False):
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            return False

        old_balance = user.wallet_balance
        user.wallet_balance = new_balance

        if log_adjust and old_balance != new_balance:
            old_display = await format_currency(old_balance)
            new_display = await format_currency(new_balance)
            txn = Transaction(
                user_id=user.id,
                amount=new_balance - old_balance,
                type="manual_adjustment",
                description=f"Admin manual adjustment from {old_display} to {new_display}",
            )
            session.add(txn)

        await session.commit()
        return True


async def log_transaction(user_id: int, amount: float, txn_type: str, description: str):
    async with AsyncSessionLocal() as session:
        txn = Transaction(
            user_id=user_id,
            amount=amount,
            type=txn_type,
            description=description,
        )
        session.add(txn)
        await session.commit()
        return True


async def toggle_user_ban(user_id: int):
    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            return False, None

        new_banned_state = not user.is_banned
        user.is_banned = new_banned_state
        user.is_active = not new_banned_state

        subs = (
            await session.execute(
                select(Subscription).where(
                    Subscription.user_id == user_id, Subscription.status == "active"
                )
            )
        ).scalars().all()

        async def sync_mt_sub(sub, banned):
            server = await session.get(Server, sub.server_id)
            if server:
                try:
                    mgr = get_mikrotik_manager(server)
                    if banned:
                        await asyncio.to_thread(mgr.disable_user, sub.mikrotik_username)
                    else:
                        await asyncio.to_thread(mgr.enable_user, sub.mikrotik_username)
                except Exception:
                    pass

        wg_subs = (
            await session.execute(
                select(WireGuardSubscription)
                .options(joinedload(WireGuardSubscription.interface))
                .where(
                    WireGuardSubscription.user_id == user_id,
                    WireGuardSubscription.status == "active",
                )
            )
        ).scalars().all()

        async def sync_mt_wg(ws, banned):
            if ws.interface:
                server = await session.get(Server, ws.interface.server_id)
                if server:
                    try:
                        mgr = get_mikrotik_manager(server)
                        await asyncio.to_thread(
                            mgr.set_wg_peer_status,
                            ws.interface.name,
                            ws.peer_public_key,
                            banned,
                        )
                    except Exception:
                        pass

        await asyncio.gather(
            *(sync_mt_sub(s, new_banned_state) for s in subs),
            *(sync_mt_wg(ws, new_banned_state) for ws in wg_subs),
        )

        await session.commit()
        return True, new_banned_state


async def delete_user_full(user_id: int):
    from sqlalchemy import delete

    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
        if not user:
            return False

        ovpn_subs = (
            await session.execute(
                select(Subscription).where(Subscription.user_id == user_id)
            )
        ).scalars().all()
        for sub in ovpn_subs:
            server = await session.get(Server, sub.server_id)
            if server:
                try:
                    mgr = get_mikrotik_manager(server)
                    await asyncio.to_thread(mgr.connect)
                    await asyncio.to_thread(mgr.delete_user, sub.mikrotik_username)
                    await asyncio.to_thread(mgr.close)
                except Exception:
                    pass

        wg_subs = (
            await session.execute(
                select(WireGuardSubscription).where(WireGuardSubscription.user_id == user_id)
            )
        ).scalars().all()
        for ws in wg_subs:
            from vpn_bot.models import WireGuardInterface

            res = await session.execute(
                select(WireGuardInterface).where(WireGuardInterface.id == ws.interface_id)
            )
            iface = res.scalar()
            if iface:
                server = await session.get(Server, iface.server_id)
                if server:
                    try:
                        mgr = get_mikrotik_manager(server)
                        await asyncio.to_thread(mgr.connect)
                        await asyncio.to_thread(
                            mgr.remove_wg_peer, iface.name, ws.peer_public_key
                        )
                        await asyncio.to_thread(mgr.remove_wg_queue, ws.unique_identifier)
                        await asyncio.to_thread(mgr.close)
                    except Exception:
                        pass

        from vpn_bot.models import TicketMessage

        wg_iface_ids = [ws.interface_id for ws in wg_subs if ws.interface_id]

        await session.execute(delete(Subscription).where(Subscription.user_id == user_id))
        await session.execute(
            delete(WireGuardSubscription).where(WireGuardSubscription.user_id == user_id)
        )
        if wg_iface_ids:
            from vpn_bot.admin_wg_service import sync_wg_interfaces_current_users

            await sync_wg_interfaces_current_users(session, wg_iface_ids)
        await session.execute(delete(Transaction).where(Transaction.user_id == user_id))
        await session.execute(
            delete(PaymentReceipt).where(PaymentReceipt.user_id == user_id)
        )
        await session.execute(
            delete(TicketMessage).where(
                TicketMessage.ticket_id.in_(
                    select(Ticket.id).where(Ticket.user_id == user_id)
                )
            )
        )
        await session.execute(delete(Ticket).where(Ticket.user_id == user_id))

        await session.delete(user)
        await session.commit()
        return True
