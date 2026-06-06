"""Admin panel: discount code CRUD."""

from __future__ import annotations

import re
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    CallbackQueryHandler,
    ConversationHandler,
    MessageHandler,
    filters,
)

from vpn_bot.admin_conversation import (
    build_admin_fallback_handlers,
    clear_admin_flow_context,
)
from vpn_bot.admin_discount_service import (
    count_discount_codes,
    create_discount_code,
    delete_discount_code,
    get_discount_code,
    list_discount_codes,
    toggle_discount_code,
    update_discount_code,
)
from vpn_bot.admin_permissions import PERM_SALES_DISCOUNTS, guard_admin_handler
from vpn_bot.admin_audit import audit_log
from vpn_bot.conversation_controls import conv_control_handlers, reply_conv_prompt
from vpn_bot.discount_expiry import EXPIRY_UNIT_LIMITS, EXPIRY_UNITS, compute_valid_until, validate_expiry_amount
from vpn_bot.user_features import _format_history_separators_rtl
from vpn_bot.utils import (
    LanguageManager,
    ensure_telegram_text,
    format_currency,
    format_datetime,
    get_currency_unit,
    safe_response,
)

DISCOUNT_MENU = 500
DISCOUNT_TYPE = 501
DISCOUNT_NAME = 502
DISCOUNT_CODE = 503
DISCOUNT_VALUE = 504
DISCOUNT_MAX_PER_USER = 505
DISCOUNT_EDIT_MENU = 506
DISCOUNT_EDIT_FIELD = 507
DISCOUNT_EXPIRY_UNIT = 508
DISCOUNT_EXPIRY_VALUE = 509

DISCOUNT_LIST_PAGE_SIZE = 10


def _discount_text(key: str, **kwargs) -> str:
    return LanguageManager.get(key, **kwargs)


def _discount_body(text: str) -> str:
    return ensure_telegram_text(_format_history_separators_rtl(text))


_ARABIC_PERSIAN_RE = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF]")


def _format_discount_display_name(name: str) -> str:
    """RTL-friendly title: Latin names get emoji after text (e.g. Test 🎟)."""
    if LanguageManager._current_lang == "fa":
        if _ARABIC_PERSIAN_RE.search(name):
            return f"🎟 {name}"
        return f"{name} 🎟"
    return f"🎟 {name}"


async def _reply_discount(update: Update, text: str, *, reply_markup=None, parse_mode: str = "Markdown"):
    """Edit the callback inline message when possible; fall back to a new reply."""
    body = _discount_body(text)
    query = update.callback_query
    if query and query.message:
        try:
            await query.edit_message_text(body, reply_markup=reply_markup, parse_mode=parse_mode)
        except Exception:
            await query.message.reply_text(body, reply_markup=reply_markup, parse_mode=parse_mode)
    elif update.message:
        await update.message.reply_text(body, reply_markup=reply_markup, parse_mode=parse_mode)


async def _edit_discount(query, text: str, *, reply_markup=None, parse_mode: str = "Markdown"):
    body = _discount_body(text)
    try:
        await query.edit_message_text(body, reply_markup=reply_markup, parse_mode=parse_mode)
    except Exception:
        await query.message.reply_text(body, reply_markup=reply_markup, parse_mode=parse_mode)


def _expiry_unit_keyboard() -> InlineKeyboardMarkup:
    rows = []
    unit_buttons = [
        ("minute", "btn_expiry_minute"),
        ("hour", "btn_expiry_hour"),
        ("day", "btn_expiry_day"),
        ("week", "btn_expiry_week"),
        ("month", "btn_expiry_month"),
        ("year", "btn_expiry_year"),
    ]
    for i in range(0, len(unit_buttons), 2):
        row = []
        for unit, key in unit_buttons[i : i + 2]:
            row.append(
                InlineKeyboardButton(
                    LanguageManager.get(f"admin.discount.{key}"),
                    callback_data=f"discount_expiry_{unit}",
                )
            )
        rows.append(row)
    rows.append([
        InlineKeyboardButton(
            LanguageManager.get("admin.discount.btn_expiry_none"),
            callback_data="discount_expiry_none",
        )
    ])
    rows.append([
        InlineKeyboardButton(LanguageManager.get("common.cancel"), callback_data="discount_codes_menu"),
    ])
    return InlineKeyboardMarkup(rows)


async def _format_code_expiry_label(code_row) -> str:
    if not code_row.valid_until:
        return _discount_text("admin.discount.no_expiry")
    expires_at = await format_datetime(code_row.valid_until)
    vu = code_row.valid_until
    if vu.tzinfo is None:
        vu = vu.replace(tzinfo=timezone.utc)
    if vu < datetime.now(timezone.utc):
        return _discount_text("admin.discount.expired_temporal", expires_at=expires_at)
    return _discount_text("admin.discount.list_item_expiry", expires_at=expires_at)


async def _finalize_discount_create(update: Update, context, *, valid_until=None):
    w = context.user_data.get("discount_wizard", {})
    unit = await get_currency_unit() if w.get("discount_type") == "fixed" else None
    try:
        row = await create_discount_code(
            name=w["name"],
            code=w["code"],
            discount_type=w["discount_type"],
            value=w["value"],
            currency_unit=unit,
            max_uses_per_user=w["max_uses_per_user"],
            valid_until=valid_until,
        )
    except ValueError as exc:
        key = f"admin.discount.err_{exc.args[0]}" if exc.args else "admin.discount.save_failed"
        await _reply_discount(update, _discount_text(key))
        return DISCOUNT_EXPIRY_VALUE if valid_until is not None else DISCOUNT_EXPIRY_UNIT

    admin_id = update.effective_user.id if update.effective_user else 0
    await audit_log(
        admin_id,
        "discount_create",
        target_type="discount",
        target_id=str(getattr(row, "id", w.get("code"))),
        detail={"code": w.get("code")},
    )

    context.user_data.pop("discount_wizard", None)
    return await _render_discount_codes_menu(update, context)


async def _render_discount_codes_menu(update: Update, context):
    """Render discount list without safe_response (for nested admin callbacks)."""
    query = update.callback_query
    show_inactive = context.user_data.get("discount_list_mode") == "inactive"
    active_only = False if show_inactive else True
    page = max(0, int(context.user_data.get("discount_list_page", 0) or 0))
    total = await count_discount_codes(active_only=active_only)
    max_page = max(0, (total - 1) // DISCOUNT_LIST_PAGE_SIZE) if total else 0
    if page > max_page:
        page = max_page
        context.user_data["discount_list_page"] = page
    offset = page * DISCOUNT_LIST_PAGE_SIZE

    codes = await list_discount_codes(
        limit=DISCOUNT_LIST_PAGE_SIZE,
        offset=offset,
        active_only=active_only,
        order="asc",
    )
    notice = context.user_data.pop("discount_last_action_msg", None)
    if show_inactive:
        text = _discount_text("admin.discount.list_title_inactive") + "\n\n"
    else:
        text = _discount_text("admin.discount.list_title") + "\n\n"
    if notice:
        text = f"{notice}\n\n{text}"
    keyboard = []
    if not codes:
        text += _discount_text(
            "admin.discount.list_empty_inactive" if show_inactive else "admin.discount.list_empty"
        )
    else:
        for i, c in enumerate(codes):
            index = offset + i + 1
            if c.discount_type == "percent":
                val = f"{c.value:g}%"
            else:
                val = await format_currency(c.value, unit=c.currency_unit or "TOMAN")
            status = _discount_text("admin.discount.active") if c.is_active else _discount_text("admin.discount.inactive")
            expiry = await _format_code_expiry_label(c)
            display_name = _format_discount_display_name(c.name)
            line = _discount_text(
                "admin.discount.list_item",
                index=index,
                display_name=display_name,
                code=c.code,
                value=val,
                uses=c.total_uses,
                status=status,
                expiry=expiry,
            )
            text += line
            keyboard.append([
                InlineKeyboardButton(
                    _discount_text("admin.discount.btn_toggle", code=c.code),
                    callback_data=f"discount_toggle_{c.id}",
                ),
                InlineKeyboardButton(
                    _discount_text("admin.discount.btn_edit", code=c.code),
                    callback_data=f"discount_edit_{c.id}",
                ),
                InlineKeyboardButton(
                    _discount_text("admin.discount.btn_delete", code=c.code),
                    callback_data=f"discount_delete_{c.id}",
                ),
            ])

        show_from = offset + 1
        show_to = offset + len(codes)
        text += _discount_text(
            "admin.discount.list_footer",
            **{"from": show_from, "to": show_to, "total": total},
        )

    if show_inactive:
        keyboard.append([
            InlineKeyboardButton(
                _discount_text("admin.discount.btn_show_active"),
                callback_data="discount_list_active",
            )
        ])
    else:
        keyboard.append([
            InlineKeyboardButton(
                _discount_text("admin.discount.btn_show_inactive"),
                callback_data="discount_list_inactive",
            )
        ])

    if total > DISCOUNT_LIST_PAGE_SIZE:
        nav_row = []
        if page > 0:
            nav_row.append(
                InlineKeyboardButton(
                    _discount_text("admin.discount.btn_page_prev"),
                    callback_data="discount_page_prev",
                )
            )
        else:
            nav_row.append(
                InlineKeyboardButton(
                    _discount_text("admin.discount.btn_page_noop"),
                    callback_data="discount_page_noop",
                )
            )
        nav_row.append(
            InlineKeyboardButton(
                _discount_text("admin.discount.btn_page_indicator", current=page + 1, pages=max_page + 1),
                callback_data="discount_page_noop",
            )
        )
        if page < max_page:
            nav_row.append(
                InlineKeyboardButton(
                    _discount_text("admin.discount.btn_page_next"),
                    callback_data="discount_page_next",
                )
            )
        else:
            nav_row.append(
                InlineKeyboardButton(
                    _discount_text("admin.discount.btn_page_noop"),
                    callback_data="discount_page_noop",
                )
            )
        keyboard.append(nav_row)

    keyboard.append([InlineKeyboardButton(_discount_text("admin.discount.btn_add"), callback_data="discount_add")])
    keyboard.append([InlineKeyboardButton(_discount_text("common.back"), callback_data="sales_mgmt_menu")])

    markup = InlineKeyboardMarkup(keyboard)
    body = _discount_body(text)
    if query:
        try:
            await query.edit_message_text(body, reply_markup=markup, parse_mode="Markdown")
        except Exception:
            await query.message.reply_text(body, reply_markup=markup, parse_mode="Markdown")
    else:
        await update.message.reply_text(body, reply_markup=markup, parse_mode="Markdown")
    return DISCOUNT_MENU


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
@safe_response
async def discount_list_active(update: Update, context):
    context.user_data["discount_list_mode"] = "active"
    context.user_data["discount_list_page"] = 0
    return await _render_discount_codes_menu(update, context)


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
@safe_response
async def discount_list_inactive(update: Update, context):
    context.user_data["discount_list_mode"] = "inactive"
    context.user_data["discount_list_page"] = 0
    return await _render_discount_codes_menu(update, context)


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
@safe_response
async def discount_codes_menu(update: Update, context):
    context.user_data["discount_list_mode"] = "active"
    context.user_data["discount_list_page"] = 0
    return await _render_discount_codes_menu(update, context)


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
@safe_response
async def discount_page_prev(update: Update, context):
    page = max(0, int(context.user_data.get("discount_list_page", 0) or 0) - 1)
    context.user_data["discount_list_page"] = page
    return await _render_discount_codes_menu(update, context)


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
@safe_response
async def discount_page_next(update: Update, context):
    page = int(context.user_data.get("discount_list_page", 0) or 0) + 1
    context.user_data["discount_list_page"] = page
    return await _render_discount_codes_menu(update, context)


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
@safe_response
async def discount_page_noop(update: Update, context):
    query = update.callback_query
    if query:
        await query.answer()
    return DISCOUNT_MENU


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
async def sales_mgmt_exit(update: Update, context):
    clear_admin_flow_context(context)
    from vpn_bot.admin_sales import sales_mgmt_menu

    return await sales_mgmt_menu(update, context)


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
@safe_response
async def discount_add_start(update: Update, context):
    query = update.callback_query
    clear_admin_flow_context(context)
    context.user_data["discount_wizard"] = {}
    text = _discount_text("admin.discount.choose_type")
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(_discount_text("admin.discount.type_percent"), callback_data="discount_type_percent")],
        [InlineKeyboardButton(_discount_text("admin.discount.type_fixed"), callback_data="discount_type_fixed")],
        [InlineKeyboardButton(_discount_text("common.cancel"), callback_data="discount_codes_menu")],
    ])
    await _edit_discount(query, text, reply_markup=keyboard)
    return DISCOUNT_TYPE


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
@safe_response
async def discount_type_selected(update: Update, context):
    query = update.callback_query
    dtype = "percent" if query.data.endswith("percent") else "fixed"
    context.user_data.setdefault("discount_wizard", {})["discount_type"] = dtype
    await reply_conv_prompt(update, _discount_text("admin.discount.prompt_name"))
    return DISCOUNT_NAME


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
async def receive_discount_name(update: Update, context):
    name = (update.message.text or "").strip()
    if len(name) < 2:
        await update.message.reply_text(_discount_body(_discount_text("admin.discount.name_invalid")))
        return DISCOUNT_NAME
    context.user_data["discount_wizard"]["name"] = name
    await update.message.reply_text(_discount_body(_discount_text("admin.discount.prompt_code")))
    return DISCOUNT_CODE


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
async def receive_discount_code(update: Update, context):
    from vpn_bot.discount_service import is_valid_code_format, normalize_code
    from vpn_bot.admin_discount_service import code_exists

    code = normalize_code(update.message.text or "")
    if not is_valid_code_format(code):
        await update.message.reply_text(_discount_body(_discount_text("admin.discount.code_invalid")))
        return DISCOUNT_CODE
    if await code_exists(code):
        await update.message.reply_text(_discount_body(_discount_text("admin.discount.code_duplicate")))
        return DISCOUNT_CODE
    context.user_data["discount_wizard"]["code"] = code
    w = context.user_data["discount_wizard"]
    if w.get("discount_type") == "percent":
        await update.message.reply_text(_discount_body(_discount_text("admin.discount.prompt_percent")))
    else:
        unit = await get_currency_unit()
        await update.message.reply_text(_discount_body(_discount_text("admin.discount.prompt_fixed", unit=unit)))
    return DISCOUNT_VALUE


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
async def receive_discount_value(update: Update, context):
    w = context.user_data.get("discount_wizard", {})
    try:
        value = float((update.message.text or "").strip().replace(",", ""))
    except ValueError:
        await update.message.reply_text(_discount_body(_discount_text("admin.discount.value_invalid")))
        return DISCOUNT_VALUE
    if w.get("discount_type") == "percent":
        if not (0 < value <= 100):
            await update.message.reply_text(_discount_body(_discount_text("admin.discount.percent_invalid")))
            return DISCOUNT_VALUE
    elif value <= 0:
        await update.message.reply_text(_discount_body(_discount_text("admin.discount.value_invalid")))
        return DISCOUNT_VALUE
    w["value"] = value
    await update.message.reply_text(_discount_body(_discount_text("admin.discount.prompt_max_per_user")))
    return DISCOUNT_MAX_PER_USER


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
async def receive_discount_max_per_user(update: Update, context):
    w = context.user_data.get("discount_wizard", {})
    try:
        max_u = int((update.message.text or "").strip())
    except ValueError:
        await update.message.reply_text(_discount_body(_discount_text("admin.discount.max_invalid")))
        return DISCOUNT_MAX_PER_USER
    if max_u < 1:
        await update.message.reply_text(_discount_body(_discount_text("admin.discount.max_invalid")))
        return DISCOUNT_MAX_PER_USER

    w["max_uses_per_user"] = max_u
    text = _discount_text("admin.discount.prompt_expiry_unit")
    await update.message.reply_text(_discount_body(text), reply_markup=_expiry_unit_keyboard(), parse_mode="Markdown")
    return DISCOUNT_EXPIRY_UNIT


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
@safe_response
async def discount_expiry_unit_selected(update: Update, context):
    query = update.callback_query
    unit_key = query.data.replace("discount_expiry_", "")
    if unit_key == "none":
        return await _finalize_discount_create(update, context, valid_until=None)

    if unit_key not in EXPIRY_UNITS:
        return DISCOUNT_EXPIRY_UNIT

    context.user_data.setdefault("discount_wizard", {})["expiry_unit"] = unit_key
    lo, hi = EXPIRY_UNIT_LIMITS[unit_key]
    unit_label = _discount_text(f"admin.discount.unit_{unit_key}")
    text = _discount_text(
        "admin.discount.prompt_expiry_amount",
        unit=unit_label,
        min=lo,
        max=hi,
    )
    await _edit_discount(query, text)
    return DISCOUNT_EXPIRY_VALUE


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
async def receive_discount_expiry_value(update: Update, context):
    w = context.user_data.get("discount_wizard", {})
    unit = w.get("expiry_unit")
    if unit not in EXPIRY_UNITS:
        text = _discount_text("admin.discount.prompt_expiry_unit")
        await update.message.reply_text(_discount_body(text), reply_markup=_expiry_unit_keyboard(), parse_mode="Markdown")
        return DISCOUNT_EXPIRY_UNIT

    try:
        amount = int((update.message.text or "").strip())
    except ValueError:
        lo, hi = EXPIRY_UNIT_LIMITS[unit]
        unit_label = _discount_text(f"admin.discount.unit_{unit}")
        await update.message.reply_text(
            _discount_body(
                _discount_text(
                    "admin.discount.expiry_amount_invalid",
                    unit=unit_label,
                    min=lo,
                    max=hi,
                )
            )
        )
        return DISCOUNT_EXPIRY_VALUE

    if not validate_expiry_amount(unit, amount):
        lo, hi = EXPIRY_UNIT_LIMITS[unit]
        unit_label = _discount_text(f"admin.discount.unit_{unit}")
        await update.message.reply_text(
            _discount_body(
                _discount_text(
                    "admin.discount.expiry_amount_invalid",
                    unit=unit_label,
                    min=lo,
                    max=hi,
                )
            )
        )
        return DISCOUNT_EXPIRY_VALUE

    valid_until = compute_valid_until(unit, amount)
    return await _finalize_discount_create(update, context, valid_until=valid_until)


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
@safe_response
async def discount_toggle(update: Update, context):
    query = update.callback_query
    code_id = int(query.data.split("_")[-1])
    row = await toggle_discount_code(code_id)
    if row is None:
        context.user_data["discount_last_action_msg"] = _discount_text("admin.discount.not_found")
    else:
        if row.is_active:
            context.user_data["discount_last_action_msg"] = _discount_text("admin.discount.toggled_active")
        else:
            context.user_data["discount_last_action_msg"] = _discount_text("admin.discount.toggled_inactive")
        await audit_log(
            update.effective_user.id,
            "discount_toggle",
            target_type="discount",
            target_id=str(code_id),
            detail={"active": row.is_active},
        )
    return await _render_discount_codes_menu(update, context)


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
@safe_response
async def discount_delete(update: Update, context):
    query = update.callback_query
    code_id = int(query.data.split("_")[-1])
    result = await delete_discount_code(code_id)
    if result == "hard":
        context.user_data["discount_last_action_msg"] = _discount_text("admin.discount.deleted")
    elif result == "soft":
        context.user_data["discount_last_action_msg"] = _discount_text("admin.discount.deactivated_not_deleted")
    elif result == "already_inactive":
        context.user_data["discount_last_action_msg"] = _discount_text("admin.discount.already_inactive")
    elif result == "missing":
        context.user_data["discount_last_action_msg"] = _discount_text("admin.discount.not_found")
    if result in ("hard", "soft", "already_inactive"):
        await audit_log(
            update.effective_user.id,
            "discount_delete",
            target_type="discount",
            target_id=str(code_id),
            detail={"result": result},
        )
    return await _render_discount_codes_menu(update, context)


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
@safe_response
async def discount_edit_menu(update: Update, context):
    query = update.callback_query
    code_id = int(query.data.split("_")[-1])
    row = await get_discount_code(code_id)
    if not row:
        await query.edit_message_text(_discount_body(_discount_text("admin.discount.not_found")))
        return ConversationHandler.END
    context.user_data["discount_edit_id"] = code_id
    expiry = await _format_code_expiry_label(row)
    text = _discount_text("admin.discount.edit_title", name=row.name, code=row.code, expiry=expiry)
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton(_discount_text("admin.discount.edit_name"), callback_data="discount_ef_name")],
        [InlineKeyboardButton(_discount_text("admin.discount.edit_max"), callback_data="discount_ef_max")],
        [InlineKeyboardButton(_discount_text("common.back"), callback_data="discount_codes_menu")],
    ])
    await _edit_discount(query, text, reply_markup=keyboard)
    return DISCOUNT_EDIT_MENU


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
@safe_response
async def discount_edit_field_start(update: Update, context):
    query = update.callback_query
    field = query.data.replace("discount_ef_", "")
    context.user_data["discount_edit_field"] = field
    if field == "name":
        await reply_conv_prompt(update, _discount_text("admin.discount.prompt_name"))
    else:
        await reply_conv_prompt(update, _discount_text("admin.discount.prompt_max_per_user"))
    return DISCOUNT_EDIT_FIELD


@guard_admin_handler(perm=PERM_SALES_DISCOUNTS)
async def receive_discount_edit_value(update: Update, context):
    code_id = context.user_data.get("discount_edit_id")
    field = context.user_data.get("discount_edit_field")
    text = (update.message.text or "").strip()
    try:
        if field == "name":
            if len(text) < 2:
                raise ValueError("name")
            await update_discount_code(code_id, name=text)
        elif field == "max":
            max_u = int(text)
            if max_u < 1:
                raise ValueError("max")
            await update_discount_code(code_id, max_uses_per_user=max_u)
    except ValueError:
        await update.message.reply_text(_discount_body(_discount_text("admin.discount.value_invalid")))
        return DISCOUNT_EDIT_FIELD
    context.user_data.pop("discount_edit_field", None)
    context.user_data.pop("discount_edit_id", None)
    await update.message.reply_text(_discount_body(_discount_text("admin.discount.updated")))
    return await _render_discount_codes_menu(update, context)


async def cancel_discount_action(update: Update, context):
    context.user_data.pop("discount_wizard", None)
    context.user_data.pop("discount_edit_id", None)
    context.user_data.pop("discount_edit_field", None)
    return await _render_discount_codes_menu(update, context)


async def _admin_discount_main_menu_dispatch(update: Update, context):
    """Leave admin discount flow and route main-menu reply buttons to user handlers."""
    context.user_data.pop("discount_wizard", None)
    context.user_data.pop("discount_edit_id", None)
    context.user_data.pop("discount_edit_field", None)
    from vpn_bot.bot_handler import main_menu_text_dispatch

    return await main_menu_text_dispatch(update, context)


_DISCOUNT_LIST_CALLBACK_HANDLERS = [
    CallbackQueryHandler(discount_codes_menu, pattern="^discount_codes_menu$"),
    CallbackQueryHandler(discount_list_active, pattern="^discount_list_active$"),
    CallbackQueryHandler(discount_list_inactive, pattern="^discount_list_inactive$"),
    CallbackQueryHandler(discount_page_prev, pattern="^discount_page_prev$"),
    CallbackQueryHandler(discount_page_next, pattern="^discount_page_next$"),
    CallbackQueryHandler(discount_page_noop, pattern="^discount_page_noop$"),
    CallbackQueryHandler(discount_toggle, pattern="^discount_toggle_\\d+$"),
    CallbackQueryHandler(discount_delete, pattern="^discount_delete_\\d+$"),
    CallbackQueryHandler(discount_edit_menu, pattern="^discount_edit_\\d+$"),
    CallbackQueryHandler(sales_mgmt_exit, pattern="^sales_mgmt_menu$"),
]


admin_discount_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(discount_codes_menu, pattern="^discount_codes_menu$"),
        CallbackQueryHandler(discount_add_start, pattern="^discount_add$"),
        CallbackQueryHandler(discount_type_selected, pattern="^discount_type_(percent|fixed)$"),
        CallbackQueryHandler(discount_toggle, pattern="^discount_toggle_\\d+$"),
        CallbackQueryHandler(discount_delete, pattern="^discount_delete_\\d+$"),
        CallbackQueryHandler(discount_edit_menu, pattern="^discount_edit_\\d+$"),
        CallbackQueryHandler(discount_edit_field_start, pattern="^discount_ef_(name|max)$"),
        CallbackQueryHandler(discount_expiry_unit_selected, pattern="^discount_expiry_(minute|hour|day|week|month|year|none)$"),
    ],
    states={
        DISCOUNT_MENU: [
            CallbackQueryHandler(discount_add_start, pattern="^discount_add$"),
            *_DISCOUNT_LIST_CALLBACK_HANDLERS,
        ],
        DISCOUNT_TYPE: [
            CallbackQueryHandler(discount_type_selected, pattern="^discount_type_(percent|fixed)$"),
            CallbackQueryHandler(cancel_discount_action, pattern="^discount_codes_menu$"),
        ],
        DISCOUNT_NAME: [
            *conv_control_handlers(cancel_discount_action),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_discount_name),
        ],
        DISCOUNT_CODE: [
            *conv_control_handlers(cancel_discount_action),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_discount_code),
        ],
        DISCOUNT_VALUE: [
            *conv_control_handlers(cancel_discount_action),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_discount_value),
        ],
        DISCOUNT_MAX_PER_USER: [
            *conv_control_handlers(cancel_discount_action),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_discount_max_per_user),
        ],
        DISCOUNT_EXPIRY_UNIT: [
            CallbackQueryHandler(discount_expiry_unit_selected, pattern="^discount_expiry_(minute|hour|day|week|month|year|none)$"),
            CallbackQueryHandler(cancel_discount_action, pattern="^discount_codes_menu$"),
        ],
        DISCOUNT_EXPIRY_VALUE: [
            *conv_control_handlers(cancel_discount_action),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_discount_expiry_value),
        ],
        DISCOUNT_EDIT_MENU: [
            CallbackQueryHandler(discount_edit_field_start, pattern="^discount_ef_(name|max)$"),
            CallbackQueryHandler(discount_codes_menu, pattern="^discount_codes_menu$"),
        ],
        DISCOUNT_EDIT_FIELD: [
            *conv_control_handlers(cancel_discount_action),
            MessageHandler(filters.TEXT & ~filters.COMMAND, receive_discount_edit_value),
        ],
    },
    fallbacks=[
        *build_admin_fallback_handlers(_admin_discount_main_menu_dispatch),
        *_DISCOUNT_LIST_CALLBACK_HANDLERS,
    ],
    allow_reentry=True,
)
