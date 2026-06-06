
import logging
import re

from sqlalchemy import select, func
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest, Forbidden
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import PaymentReceipt, ReceiptNotification, User, Transaction
from vpn_bot.utils import LanguageManager, format_currency

logger = logging.getLogger("vpn_bot.admin_receipt")


async def count_pending_receipts() -> int:
    """Count pending payment receipts."""
    async with AsyncSessionLocal() as session:
        return (
            await session.execute(
                select(func.count(PaymentReceipt.id)).where(
                    PaymentReceipt.status == "pending"
                )
            )
        ).scalar() or 0


async def get_pending_receipts():
    """Fetch all pending payment receipts with user info (newest first)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PaymentReceipt, User)
            .outerjoin(User, PaymentReceipt.user_id == User.id)
            .where(PaymentReceipt.status == "pending")
            .order_by(PaymentReceipt.submitted_at.desc())
        )
        return result.all()


async def get_pending_receipts_page(page: int = 0, per_page: int = 8):
    """Fetch one page of pending receipts (newest first)."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PaymentReceipt, User)
            .outerjoin(User, PaymentReceipt.user_id == User.id)
            .where(PaymentReceipt.status == "pending")
            .order_by(PaymentReceipt.submitted_at.desc())
            .limit(per_page)
            .offset(page * per_page)
        )
        return result.all()

async def get_receipt_with_user(receipt_id: int):
    """Fetch a specific receipt by ID with user info."""
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(PaymentReceipt, User)
            .outerjoin(User, PaymentReceipt.user_id == User.id)
            .where(PaymentReceipt.id == receipt_id)
        )
        return result.first()

async def approve_payment_receipt(receipt_id: int, admin_id: int):
    """Approve a receipt via WalletManager (single code path, row locks)."""
    from vpn_bot.wallet_manager import WalletManager

    async with AsyncSessionLocal() as session:
        row = (
            await session.execute(
                select(PaymentReceipt, User)
                .join(User, PaymentReceipt.user_id == User.id)
                .where(PaymentReceipt.id == receipt_id)
            )
        ).first()
        if not row:
            return False, "Receipt or User not found"
        receipt, user = row
        user_id = user.id
        amount = receipt.amount

    ok = await WalletManager.approve_receipt(receipt_id, admin_id, txn_type="deposit_card")
    if not ok:
        return False, "Receipt already processed"

    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_id)
    return True, (user, amount)

async def reject_payment_receipt(receipt_id: int, admin_id: int):
    """Reject a payment receipt."""
    from vpn_bot.wallet_manager import WalletManager

    return await WalletManager.reject_receipt(receipt_id, admin_id)


def receipt_display_id(receipt: PaymentReceipt) -> str:
    if receipt.unique_id:
        return f"RCP-{receipt.id}-{receipt.unique_id}"
    return f"RCP-{receipt.id:05d}"


def actor_from_admin_note(admin_note: str | None) -> str:
    if not admin_note:
        return LanguageManager.get("common.na")
    m = re.search(r"(?:Approved|Rejected) by (\d+)", admin_note, re.I)
    if m:
        return m.group(1)
    return admin_note[:40]


def _mask_phone(phone: str | None) -> str:
    if not phone:
        return LanguageManager.get("common.not_shared")
    digits = str(phone).strip()
    if len(digits) <= 4:
        return "****"
    return f"{digits[:3]}****{digits[-2:]}"


async def build_receipt_notification_caption(
    receipt: PaymentReceipt,
    user: User | None = None,
    *,
    mask_for_group: bool = False,
    tg_display_name: str | None = None,
    tg_username: str | None = None,
) -> str:
    """Rebuild admin notification caption (base + optional user block)."""
    full_id = receipt_display_id(receipt)
    amount_str = await format_currency(
        receipt.amount, unit=getattr(receipt, "currency_unit", None)
    )
    text = LanguageManager.get("admin.receipt.new_notification", id=full_id, amount=amount_str)
    if receipt.user_caption:
        text += LanguageManager.get("admin.receipt.user_note", note=receipt.user_caption)
    if user:
        display_name = tg_display_name or user.full_name or LanguageManager.get("common.na")
        username = tg_username if tg_username is not None else (user.username or LanguageManager.get("common.na"))
        if mask_for_group:
            text += LanguageManager.get(
                "admin.receipt.user_info_notif_group",
                name=display_name,
                phone=_mask_phone(user.phone_number),
                username=username or LanguageManager.get("common.na"),
            )
        else:
            text += LanguageManager.get(
                "admin.receipt.user_info_notif",
                id=user.telegram_id,
                name=display_name,
                phone=user.phone_number or LanguageManager.get("common.not_shared"),
                username=username or LanguageManager.get("common.na"),
            )
    if receipt.receipt_type == "text" and receipt.receipt_file_id:
        text += LanguageManager.get("admin.receipt.reference_label", ref=receipt.receipt_file_id)
    return text


async def _send_receipt_to_chat(
    bot,
    chat_id: int,
    receipt: PaymentReceipt,
    *,
    caption: str,
    keyboard: InlineKeyboardMarkup,
    receipt_text: str = "",
) -> int | None:
    """Send receipt notification to one chat; return message_id or None."""
    file_id = receipt.receipt_file_id if receipt.receipt_type in ("photo", "document") else None
    try:
        msg = None
        if receipt.receipt_type == "photo" and file_id:
            msg = await bot.send_photo(
                chat_id, file_id, caption=caption, reply_markup=keyboard, parse_mode="Markdown"
            )
        elif receipt.receipt_type == "document" and file_id:
            msg = await bot.send_document(
                chat_id, file_id, caption=caption, reply_markup=keyboard, parse_mode="Markdown"
            )
        else:
            msg = await bot.send_message(
                chat_id, caption, reply_markup=keyboard, parse_mode="Markdown"
            )
        return msg.message_id if msg else None
    except Exception as exc:
        logger.error("Failed to notify chat %s for receipt %s: %s", chat_id, receipt.id, exc)
        return None


async def notify_admins_new_receipt(
    bot,
    receipt: PaymentReceipt,
    user: User,
    *,
    receipt_text: str = "",
    tg_display_name: str = "",
    tg_username: str | None = None,
) -> None:
    """Route new receipt notifications per receipt_notif_mode (pv / group / both)."""
    from vpn_bot.admin_permissions import PERM_RECEIPTS, get_admins_for_permission
    from vpn_bot.admin_settings_service import get_receipt_group_id, get_receipt_notif_mode

    mode = await get_receipt_notif_mode()
    group_id = await get_receipt_group_id()
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.receipt.approve_btn"),
                    callback_data=f"receipt_approve_{receipt.id}",
                ),
                InlineKeyboardButton(
                    LanguageManager.get("admin.receipt.reject_btn"),
                    callback_data=f"receipt_reject_{receipt.id}",
                ),
            ]
        ]
    )

    destinations: list[tuple[int, bool]] = []
    seen: set[int] = set()

    if mode in ("pv", "both"):
        for admin_id in await get_admins_for_permission(PERM_RECEIPTS):
            if admin_id not in seen:
                destinations.append((int(admin_id), False))
                seen.add(admin_id)

    if mode in ("group", "both") and group_id:
        gid = int(group_id)
        if gid not in seen:
            destinations.append((gid, True))
            seen.add(gid)

    if not destinations:
        return

    admin_messages: list[dict[str, int]] = []
    for chat_id, to_group in destinations:
        caption = await build_receipt_notification_caption(
            receipt,
            user,
            mask_for_group=to_group,
            tg_display_name=tg_display_name,
            tg_username=tg_username,
        )
        msg_id = await _send_receipt_to_chat(
            bot,
            chat_id,
            receipt,
            caption=caption,
            keyboard=keyboard,
            receipt_text=receipt_text,
        )
        if msg_id is not None:
            admin_messages.append({"admin_id": chat_id, "message_id": msg_id})

    if admin_messages:
        try:
            async with AsyncSessionLocal() as session:
                for m in admin_messages:
                    session.add(
                        ReceiptNotification(
                            receipt_id=receipt.id,
                            admin_id=int(m["admin_id"]),
                            message_id=int(m["message_id"]),
                        )
                    )
                await session.commit()
        except Exception as exc:
            logger.error(
                "Failed to save receipt_notifications for receipt %s: %s",
                receipt.id,
                exc,
            )


async def is_receipt_group_chat(chat_id: int) -> bool:
    from vpn_bot.admin_settings_service import get_receipt_group_id

    gid = await get_receipt_group_id()
    return bool(gid and int(chat_id) == int(gid))


def receipt_status_footer(receipt: PaymentReceipt, *, actor_admin_id: int | None = None) -> str:
    actor = str(actor_admin_id) if actor_admin_id is not None else actor_from_admin_note(receipt.admin_note)
    full_id = receipt_display_id(receipt)
    if receipt.status == "approved":
        return LanguageManager.get(
            "admin.receipt.notif_resolved_approved", id=full_id, admin=actor
        )
    if receipt.status == "rejected":
        return LanguageManager.get(
            "admin.receipt.notif_resolved_rejected", id=full_id, admin=actor
        )
    return LanguageManager.get("admin.receipt.notif_still_pending", id=full_id)


async def sync_receipt_admin_notifications(
    bot,
    receipt_id: int,
    *,
    actor_admin_id: int | None = None,
) -> None:
    """Update all stored admin notification messages: remove buttons, show final status."""
    async with AsyncSessionLocal() as session:
        receipt = await session.get(PaymentReceipt, receipt_id)
        if not receipt or receipt.status == "pending":
            return
        user = await session.get(User, receipt.user_id)
        notif_rows = (
            await session.execute(
                select(ReceiptNotification).where(
                    ReceiptNotification.receipt_id == receipt_id
                )
            )
        ).scalars().all()

    from vpn_bot.admin_settings_service import get_receipt_group_id

    group_id = await get_receipt_group_id()
    footer = receipt_status_footer(receipt, actor_admin_id=actor_admin_id)

    for notif in notif_rows:
        chat_id = int(notif.admin_id)
        msg_id = int(notif.message_id)
        mask_for_group = bool(group_id and chat_id == int(group_id))
        base_caption = await build_receipt_notification_caption(
            receipt, user, mask_for_group=mask_for_group
        )
        final_text = base_caption + footer
        try:
            await bot.edit_message_reply_markup(
                chat_id=chat_id, message_id=msg_id, reply_markup=None
            )
        except (BadRequest, Forbidden) as exc:
            if "not modified" not in str(exc).lower():
                logger.debug("receipt %s: clear markup %s: %s", receipt_id, chat_id, exc)
        except Exception as exc:
            logger.debug("receipt %s: clear markup %s: %s", receipt_id, chat_id, exc)

        if receipt.receipt_type in ("photo", "document"):
            try:
                await bot.edit_message_caption(
                    chat_id=chat_id,
                    message_id=msg_id,
                    caption=final_text,
                    parse_mode="Markdown",
                )
                continue
            except BadRequest as exc:
                if "no caption" in str(exc).lower() or "message is not a media" in str(exc).lower():
                    pass
                elif "not modified" not in str(exc).lower():
                    logger.warning(
                        "receipt %s: caption edit failed for admin %s: %s",
                        receipt_id,
                        chat_id,
                        exc,
                    )
            except Forbidden:
                logger.warning("receipt %s: admin %s blocked bot", receipt_id, chat_id)
                continue
            except Exception as exc:
                logger.warning(
                    "receipt %s: caption edit failed for admin %s: %s",
                    receipt_id,
                    chat_id,
                    exc,
                )

        try:
            await bot.edit_message_text(
                chat_id=chat_id,
                message_id=msg_id,
                text=final_text,
                parse_mode="Markdown",
            )
        except BadRequest as exc:
            if "not modified" not in str(exc).lower():
                logger.warning(
                    "receipt %s: text edit failed for admin %s: %s",
                    receipt_id,
                    chat_id,
                    exc,
                )
        except Forbidden:
            logger.warning("receipt %s: admin %s blocked bot", receipt_id, chat_id)
        except Exception as exc:
            logger.warning(
                "receipt %s: text edit failed for admin %s: %s",
                receipt_id,
                chat_id,
                exc,
            )


def already_processed_alert_text(receipt: PaymentReceipt) -> str:
    if receipt.status == "approved":
        status_label = LanguageManager.get("admin.receipt.status_approved")
    elif receipt.status == "rejected":
        status_label = LanguageManager.get("admin.receipt.status_rejected")
    else:
        status_label = receipt.status
    return LanguageManager.get(
        "admin.receipt.already_processed_alert",
        id=receipt_display_id(receipt),
        status=status_label,
        admin=actor_from_admin_note(receipt.admin_note),
    )
