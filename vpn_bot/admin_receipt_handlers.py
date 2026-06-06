"""Admin receipt approval handlers (split from admin_panel)."""

from __future__ import annotations

import logging

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, ConversationHandler

from vpn_bot.admin_audit import audit_log
from vpn_bot.admin_receipt_service import (
    count_pending_receipts,
    get_pending_receipts_page,
    approve_payment_receipt,
    reject_payment_receipt,
    get_receipt_with_user,
)
from vpn_bot.config import config
from vpn_bot.settings_utils import get_admin_setting
from vpn_bot.utils import LanguageManager, format_currency, safe_response

from vpn_bot.admin_panel_shared import universal_reply

logger = logging.getLogger("vpn_bot.admin")

RECEIPTS_PER_PAGE = 8

# --- Receipt Approval Workflow ---

RECEIPTS_PER_PAGE = 8


async def _receipt_inbox_back_callback(chat_id: int, *, chat_type: str | None) -> str:
    from vpn_bot.admin_permissions import resolve_group_admin_scope

    scope = await resolve_group_admin_scope(chat_id, chat_type=chat_type)
    return "receipt_group_admin" if scope == "receipt" else "admin_start"


def _pending_receipts_back_markup(back_callback: str = "admin_start") -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(LanguageManager.get("common.back"), callback_data=back_callback)]]
    )


async def send_backup_group_menu(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Inline backup menu for the backup Telegram group."""
    from vpn_bot.admin_settings import get_admin_setting

    interval = await get_admin_setting("backup_interval_hours", "6h")
    group = config.BACKUP_GROUP_ID or LanguageManager.get("common.not_set")
    text = LanguageManager.get("admin.backup.menu", group=group, interval=interval)
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.backup.btn_interval"),
                    callback_data="backup_set_interval",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.backup.btn_export"),
                    callback_data="backup_export",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.backup.btn_import"),
                    callback_data="backup_import",
                )
            ],
        ]
    )
    if update.callback_query:
        await update.callback_query.answer()
        await _send_or_edit_admin_message(update, context, text, keyboard)
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")


@safe_response
async def send_receipt_group_admin_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Hub menu for receipt Telegram group (/admin)."""
    from vpn_bot.admin_permissions import PERM_RECEIPTS, require_admin_message

    if not await require_admin_message(
        update, perm=PERM_RECEIPTS, chat_context="receipt_or_private"
    ):
        return ConversationHandler.END

    context.user_data["receipt_notif_back"] = "receipt_group_admin"
    text = LanguageManager.get("admin.receipt_group.admin_menu")
    keyboard = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.receipt_group.btn_pending"),
                    callback_data="pending_receipts",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.receipt_group.btn_notif"),
                    callback_data="receipt_notif_mode",
                )
            ],
        ]
    )
    if update.callback_query:
        await update.callback_query.answer()
        await _send_or_edit_admin_message(update, context, text, keyboard)
    elif update.message:
        await update.message.reply_text(text, reply_markup=keyboard, parse_mode="Markdown")
    return ConversationHandler.END


async def _pending_receipt_button_label(receipt, user) -> str:
    amount_str = await format_currency(
        receipt.amount, unit=getattr(receipt, "currency_unit", None)
    )
    if user:
        who = user.username or user.full_name or str(user.telegram_id)
    else:
        who = LanguageManager.get("common.na")
    who = str(who).replace("\n", " ")[:24]
    prefix = "🐉 " if getattr(receipt, "is_wireguard", False) else ""
    plan = f" | P{receipt.plan_id}" if receipt.plan_id else ""
    return f"{prefix}#{receipt.id} {amount_str} | {who}{plan}"


async def _build_pending_receipts_keyboard(
    rows: list,
    *,
    page: int,
    total: int,
    per_page: int,
    back_callback: str = "admin_start",
) -> list[list[InlineKeyboardButton]]:
    keyboard: list[list[InlineKeyboardButton]] = []
    for receipt, user in rows:
        label = await _pending_receipt_button_label(receipt, user)
        keyboard.append(
            [InlineKeyboardButton(label, callback_data=f"view_receipt_{receipt.id}")]
        )

    total_pages = max(1, (total + per_page - 1) // per_page)
    if total_pages > 1:
        nav: list[InlineKeyboardButton] = []
        if page > 0:
            nav.append(
                InlineKeyboardButton(
                    LanguageManager.get("admin.receipt.btn_prev"),
                    callback_data=f"pending_receipts_page_{page - 1}",
                )
            )
        nav.append(
            InlineKeyboardButton(
                LanguageManager.get(
                    "admin.receipt.page_indicator",
                    current=page + 1,
                    total=total_pages,
                ),
                callback_data="pending_receipts_noop",
            )
        )
        if page < total_pages - 1:
            nav.append(
                InlineKeyboardButton(
                    LanguageManager.get("admin.receipt.btn_next"),
                    callback_data=f"pending_receipts_page_{page + 1}",
                )
            )
        keyboard.append(nav)

    keyboard.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.receipt.btn_notif_mode"),
                callback_data="receipt_notif_mode",
            )
        ]
    )
    keyboard.append(
        [InlineKeyboardButton(LanguageManager.get("common.back"), callback_data=back_callback)]
    )
    return keyboard


async def _send_or_edit_admin_message(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    """Edit callback message when possible; otherwise send a new text message."""
    query = update.callback_query
    if query:
        for attempt in (
            lambda: query.edit_message_text(
                text, reply_markup=reply_markup, parse_mode="Markdown"
            ),
            lambda: query.edit_message_caption(
                caption=text, reply_markup=reply_markup, parse_mode="Markdown"
            ),
        ):
            try:
                await attempt()
                return
            except Exception:
                continue
        if query.message:
            try:
                await query.message.delete()
            except Exception:
                pass
    await context.bot.send_message(
        update.effective_chat.id,
        text,
        reply_markup=reply_markup,
        parse_mode="Markdown",
    )


async def _render_pending_receipts_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    page: int = 0,
) -> None:
    if update.callback_query:
        await update.callback_query.answer()

    back_cb = await _receipt_inbox_back_callback(
        update.effective_chat.id, chat_type=update.effective_chat.type
    )
    context.user_data["receipt_notif_back"] = "pending_receipts"

    total = await count_pending_receipts()
    if total == 0:
        await _send_or_edit_admin_message(
            update,
            context,
            LanguageManager.get("admin.receipt.no_pending"),
            _pending_receipts_back_markup(back_cb),
        )
        return

    per_page = RECEIPTS_PER_PAGE
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = max(0, min(page, total_pages - 1))
    context.user_data["pending_receipts_page"] = page

    rows = await get_pending_receipts_page(page, per_page)
    text = LanguageManager.get(
        "admin.receipt.title",
        count=total,
        page=page + 1,
        pages=total_pages,
    )
    keyboard = await _build_pending_receipts_keyboard(
        rows, page=page, total=total, per_page=per_page, back_callback=back_cb
    )
    await _send_or_edit_admin_message(
        update, context, text, InlineKeyboardMarkup(keyboard)
    )


@safe_response
async def list_pending_receipts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pending receipts inbox with pagination."""
    from vpn_bot.admin_permissions import PERM_RECEIPTS, require_admin_message

    if not await require_admin_message(
        update, perm=PERM_RECEIPTS, chat_context="receipt_or_private"
    ):
        return ConversationHandler.END
    await _render_pending_receipts_list(update, context, page=0)
    return ConversationHandler.END


@safe_response
async def toggle_receipt_notif_mode(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle receipt notification mode (pv / group / both)."""
    query = update.callback_query
    await query.answer()

    from vpn_bot.admin_settings_service import (
        get_receipt_group_id,
        get_receipt_notif_mode,
        set_receipt_notif_mode,
    )

    data = query.data
    if data.startswith("receipt_set_mode_"):
        new_mode = data.rsplit("_", 1)[-1]
        if new_mode in ("group", "both"):
            group_id = await get_receipt_group_id()
            if not group_id:
                await query.answer(
                    LanguageManager.get("admin.receipt.error_no_group"),
                    show_alert=True,
                )
                return ConversationHandler.END
        await set_receipt_notif_mode(new_mode)
        mode_label = LanguageManager.get(f"admin.receipt.mode_{new_mode}")
        await query.answer(
            LanguageManager.get("admin.receipt.mode_updated", mode=mode_label),
            show_alert=True,
        )

    current_mode = await get_receipt_notif_mode()
    mode_label = LanguageManager.get(f"admin.receipt.mode_{current_mode}")
    text = LanguageManager.get("admin.receipt.notif_mode_title", mode=mode_label)
    back_cb = context.user_data.get("receipt_notif_back", "pending_receipts")
    keyboard = [
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.receipt.mode_pv")
                + (" ✅" if current_mode == "pv" else ""),
                callback_data="receipt_set_mode_pv",
            )
        ],
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.receipt.mode_group")
                + (" ✅" if current_mode == "group" else ""),
                callback_data="receipt_set_mode_group",
            )
        ],
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.receipt.mode_both")
                + (" ✅" if current_mode == "both" else ""),
                callback_data="receipt_set_mode_both",
            )
        ],
        [InlineKeyboardButton(LanguageManager.get("common.back"), callback_data=back_cb)],
    ]
    await query.edit_message_text(
        text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
    )
    return ConversationHandler.END


@safe_response
async def pending_receipts_page_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paginate pending receipts list."""
    query = update.callback_query
    if query.data == "pending_receipts_noop":
        await query.answer()
        return ConversationHandler.END
    page = int(query.data.rsplit("_", 1)[-1])
    await _render_pending_receipts_list(update, context, page=page)
    return ConversationHandler.END

@safe_response
async def view_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored view_receipt using admin_receipt_service."""
    query = update.callback_query
    receipt_id = int(query.data.split('_')[2])
    await query.answer()

    row = await get_receipt_with_user(receipt_id)
    if not row:
        await query.edit_message_text(LanguageManager.get('common.error'))
        return

    receipt, user = row
    full_id = f"RCP-{receipt.id}-{receipt.unique_id}" if receipt.unique_id else f"RCP-{receipt.id:05d}"

    unit = getattr(receipt, 'currency_unit', 'USD')
    display_amount = await format_currency(receipt.amount, unit=unit)
    formatted_date = await format_datetime(receipt.submitted_at, include_time=True)
    name = (user.full_name if user else None) or LanguageManager.get('common.na')
    username = (user.username if user else None) or LanguageManager.get('common.na')

    text = LanguageManager.get(
        'admin.receipt.view_detail',
        id=full_id,
        name=escape_markdown(name, version=1),
        username=escape_markdown(username, version=1),
        amount=display_amount,
        date=formatted_date,
    )
    if receipt.user_caption:
        text += LanguageManager.get('admin.receipt.user_note', note=receipt.user_caption)

    page = context.user_data.get("pending_receipts_page", 0)
    back_cb = f"pending_receipts_page_{page}" if page else "pending_receipts"
    keyboard = [
        [
            InlineKeyboardButton(
                LanguageManager.get('admin.receipt.approve_btn'),
                callback_data=f"receipt_approve_{receipt_id}",
            ),
            InlineKeyboardButton(
                LanguageManager.get('admin.receipt.reject_btn'),
                callback_data=f"receipt_reject_{receipt_id}",
            ),
        ],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data=back_cb)],
    ]
    
    if receipt.receipt_type == 'text':
        text += LanguageManager.get('admin.receipt.reference_label', ref=receipt.receipt_file_id)
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return
        
    try:
        if receipt.receipt_type == 'photo': 
            await context.bot.send_photo(update.effective_chat.id, receipt.receipt_file_id, caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        elif receipt.receipt_type == 'document': 
            await context.bot.send_document(update.effective_chat.id, receipt.receipt_file_id, caption=text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        if query.message: await query.message.delete()
    except Exception as e:
        logger.error(f"Error showing receipt: {e}")
        await query.edit_message_text(text + LanguageManager.get('admin.receipt.load_error'), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def _finish_receipt_inbox_if_private(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Re-render pending receipts inbox only in private admin chat (not receipt group archive)."""
    from vpn_bot.admin_receipt_service import is_receipt_group_chat

    if await is_receipt_group_chat(update.effective_chat.id):
        return
    page = context.user_data.get("pending_receipts_page", 0)
    total = await count_pending_receipts()
    per_page = RECEIPTS_PER_PAGE
    total_pages = max(1, (total + per_page - 1) // per_page)
    if page >= total_pages:
        page = max(0, total_pages - 1)
    await _render_pending_receipts_list(update, context, page=page)


async def confirm_receipt_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored confirm_receipt_action logic."""
    query = update.callback_query
    data = query.data.split('_')
    action = data[1] # 'approve' or 'reject'
    receipt_id = int(data[2])
    admin_id = update.effective_user.id

    approve_msg = await get_admin_setting('receipt_approve_msg', LanguageManager.get('admin.receipt.default_approve_msg'))
    reject_msg = await get_admin_setting('receipt_deny_msg', LanguageManager.get('admin.receipt.default_reject_msg'))

    from vpn_bot.admin_receipt_service import (
        already_processed_alert_text,
        sync_receipt_admin_notifications,
    )

    row = await get_receipt_with_user(receipt_id)
    if not row or row[0].status != 'pending':
        receipt = row[0] if row else None
        if receipt:
            await query.answer(already_processed_alert_text(receipt), show_alert=True)
            await sync_receipt_admin_notifications(
                context.bot, receipt_id, actor_admin_id=None
            )
        else:
            await query.answer(LanguageManager.get('common.error'), show_alert=True)
        from vpn_bot.admin_menu import invalidate_inbox_cache
        invalidate_inbox_cache()
        await _finish_receipt_inbox_if_private(update, context)
        return ConversationHandler.END

    await query.answer(LanguageManager.get('common.processing'))
    callback_answered = True
    receipt, user = row

    async def _notify_already_processed(receipt_row) -> None:
        alert = already_processed_alert_text(receipt_row)
        if callback_answered:
            try:
                await context.bot.send_message(
                    update.effective_chat.id, alert, parse_mode="Markdown"
                )
            except Exception:
                pass
        else:
            await query.answer(alert, show_alert=True)
        await sync_receipt_admin_notifications(
            context.bot, receipt_id, actor_admin_id=None
        )

    if action == 'approve':
        success, result = await approve_payment_receipt(receipt_id, admin_id)
        if success:
            user_obj, amount = result
            await audit_log(
                admin_id,
                "receipt_approve",
                target_type="receipt",
                target_id=str(receipt_id),
                detail={"amount": amount, "user_tg_id": user_obj.telegram_id},
            )
            # Notify user
            amount_str = await format_currency(amount)
            display_id = f"RCP-{receipt_id}-{receipt.unique_id}" if receipt.unique_id else f"RCP-{receipt_id:05d}"
            
            await context.bot.send_message(update.effective_chat.id, LanguageManager.get('admin.receipt.approved_log', id=display_id, amount=amount_str), parse_mode='Markdown')
            
            notify_text = f"{approve_msg}\n\n💵 **{LanguageManager.get('admin.receipt.label_amount')}:** {amount_str}\n📋 **{LanguageManager.get('admin.receipt.label_receipt_id')}:** `{display_id}`"
            try:
                await context.bot.send_message(user_obj.telegram_id, notify_text, parse_mode='Markdown')
                
                # Plan delivery logic
                if receipt.plan_id:
                     coupon_id = getattr(receipt, 'discount_code_id', None)
                     if getattr(receipt, 'is_wireguard', False):
                         await finalize_wg_purchase(
                             user_obj.telegram_id, receipt.plan_id, context, is_tg_id=True, coupon_id=coupon_id
                         )
                     else:
                         success_sub, sub_obj, err_msg = await checkout_subscription(
                             user_obj.telegram_id, receipt.plan_id, coupon_id=coupon_id
                         )
                         if success_sub:
                             await context.bot.send_message(update.effective_chat.id, f"✅ Plan delivered to user.", parse_mode='Markdown')
                             # Send config logic is handled inside checkout? No, checkout returns sub.
                             # We need to trigger delivery delivery messaging here like in bot_handler?
                             # For now, let's at least confirm success to admin.
                             # Actually user_features.send_config_files should be called if we want to send files.
                             from vpn_bot.user_features import send_config_files
                             async with AsyncSessionLocal() as session:
                                 # Re-fetch server to be safe
                                 server_obj = await session.get(Server, sub_obj.server_id)
                                 await send_config_files(context.bot, user_obj.telegram_id, sub_obj, server_obj, session)
                         else:
                             await context.bot.send_message(update.effective_chat.id, f"⚠️ Wallet charged, but Plan Delivery Failed: {err_msg}", parse_mode='Markdown')
            except Exception as e:
                logger.error(f"Receipt approve notification/delivery error for RCP-{receipt_id}: {e}")
                await context.bot.send_message(update.effective_chat.id, f"⚠️ Receipt approved but delivery error: {e}", parse_mode='Markdown')
            await sync_receipt_admin_notifications(
                context.bot, receipt_id, actor_admin_id=admin_id
            )
        else:
            fresh = await get_receipt_with_user(receipt_id)
            if fresh and fresh[0].status != 'pending':
                await _notify_already_processed(fresh[0])
            elif not callback_answered:
                await query.answer(LanguageManager.get('common.error'), show_alert=True)

    elif action == 'reject':
        success = await reject_payment_receipt(receipt_id, admin_id)
        if success:
            await audit_log(
                admin_id,
                "receipt_reject",
                target_type="receipt",
                target_id=str(receipt_id),
                detail={"user_tg_id": user.telegram_id},
            )
            display_id = f"RCP-{receipt_id}-{receipt.unique_id}" if receipt.unique_id else f"RCP-{receipt_id:05d}"
            await context.bot.send_message(update.effective_chat.id, LanguageManager.get('admin.receipt.rejected_log', id=display_id), parse_mode='Markdown')
            
            notify_text = f"{reject_msg}\n\n📋 **{LanguageManager.get('admin.receipt.label_receipt_id')}:** `{display_id}`"
            try: await context.bot.send_message(user.telegram_id, notify_text, parse_mode='Markdown')
            except Exception as e: logger.error(f"Failed to notify user about receipt rejection RCP-{receipt_id}: {e}")
            await sync_receipt_admin_notifications(
                context.bot, receipt_id, actor_admin_id=admin_id
            )
        else:
            fresh = await get_receipt_with_user(receipt_id)
            if fresh and fresh[0].status != 'pending':
                await _notify_already_processed(fresh[0])
            elif not callback_answered:
                await query.answer(LanguageManager.get('common.error'), show_alert=True)

    from vpn_bot.admin_menu import invalidate_inbox_cache
    invalidate_inbox_cache()
    await _finish_receipt_inbox_if_private(update, context)
    return ConversationHandler.END
