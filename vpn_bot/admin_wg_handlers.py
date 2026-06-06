"""WireGuard admin handlers (split from admin_panel)."""

from __future__ import annotations

import asyncio
import logging
import os
from io import BytesIO

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.helpers import escape_markdown
from sqlalchemy import select

from vpn_bot.database import AsyncSessionLocal
from telegram.ext import (
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from vpn_bot.admin_conversation import admin_exit_to_menu
from vpn_bot.admin_wg_service import (
    add_wg_subscription_data,
    apply_wg_automation,
    apply_wg_automation_timed,
    broadcast_interface_update,
    create_wg_interface,
    create_wg_profile,
    delete_wg_interface,
    delete_wg_profile,
    delete_wg_subscription,
    extend_wg_subscription,
    fetch_address_list_names_timed,
    fetch_routing_tables_timed,
    fetch_upstream_interfaces_timed,
    get_all_wg_interfaces,
    get_all_wg_profiles,
    get_wg_interface_details,
    get_wg_profile_by_id,
    get_wg_subscription_comprehensive_info,
    get_wg_subscription_count,
    get_wg_subscription_for_config,
    migrate_wg_interface_logic,
    toggle_wg_subscription_status,
    update_wg_interface,
    update_wg_profile,
)
from vpn_bot.bot_handler import MENU_BUTTONS_FILTER
from vpn_bot.config import config
from vpn_bot.conversation_controls import is_conv_cancel
from vpn_bot.mikrotik_manager import MikroTikManager, get_mikrotik_manager
from vpn_bot.models import Server, WireGuardInterface, WireGuardProfile
from vpn_bot.utils import LanguageManager, format_currency, get_profile_price, safe_response, clear_user_processing
from vpn_bot.admin_profile_service import update_profile, get_profile_by_id
from vpn_bot.wg_delivery import deliver_wg_config, deliver_wg_subscription_by_id

from vpn_bot.admin_panel_shared import (
    WG_ADD_INT_MAN_ADDR,
    WG_ADD_INT_MAN_DNS,
    WG_ADD_INT_MAN_EP,
    WG_ADD_INT_MAN_KA,
    WG_ADD_INT_MAN_MTU,
    WG_ADD_INT_MAN_NAME,
    WG_ADD_INT_MAN_NAT,
    WG_ADD_INT_MAN_NAT_NEGATE,
    WG_ADD_INT_MAN_NATRM,
    WG_ADD_INT_MAN_PORT,
    WG_ADD_INT_MAN_RM,
    WG_ADD_INT_MAN_ROUTE_DIST,
    WG_ADD_INT_MAN_ROUTE_DST,
    WG_ADD_INT_MAN_ROUTE_GW,
    WG_ADD_INT_MAN_ROUTE_TABLE,
    WG_ADD_INT_MAN_UPSTREAM,
    WG_ADD_INT_SERVER,
    WG_DELETE_CONFIRM,
    WG_INT_ADDRESS,
    WG_INT_DNS,
    WG_INT_ENDPOINT,
    WG_INT_GATEWAY,
    WG_INT_KEEPALIVE,
    WG_INT_MAX_USERS,
    WG_INT_MTU,
    WG_INT_NAT_DST,
    WG_INT_NAT_DST_NEGATE,
    WG_INT_NAT_ROUTING_MARK,
    WG_INT_NOTIFY_ASK,
    WG_INT_PORT,
    WG_INT_ROUTE_DIST,
    WG_INT_ROUTE_DST,
    WG_INT_ROUTE_GW,
    WG_INT_ROUTE_TABLE,
    WG_INT_ROUTING_MARK,
    WG_INT_SETTINGS,
    WG_INT_UPSTREAM,
    WG_MIGRATE_CONFIRM,
    WG_MIGRATE_TARGET,
    WG_NOTIFICATION_TEMPLATE,
    WG_PROFILE_DAYS,
    WG_PROFILE_EDIT_NAME,
    WG_PROFILE_EDIT_PRICE,
    WG_PROFILE_NAME,
    WG_PROFILE_PRICE_TOMAN,
    WG_PROFILE_PRICE_USD,
    WG_PROFILE_RATE_LIMIT,
    WG_PROFILE_SERVER,
    WG_PROFILE_VOLUME,
    EDIT_PROFILE_SPEED,
    EDIT_WG_PROFILE_SPEED,
    SEARCH_WG_INPUT,
    _reply_wg_service_error,
    _with_conv_cancel,
    admin_conv_prompt,
    admin_conversation_fallbacks,
    get_admin_edit_inline_keyboard,
    universal_reply,
)

logger = logging.getLogger("vpn_bot.admin")

@safe_response
async def list_wg_profiles(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored list_wg_profiles using admin_wg_service."""
    profiles = await get_all_wg_profiles()
    text = LanguageManager.get('admin.wg.profiles_title')
    keyboard = []
    
    if not profiles:
        text += LanguageManager.get('admin.wg.no_profiles')
    else:
        for p in profiles:
            price_val = await get_profile_price(p)
            display_price = await format_currency(price_val)
            safe_name = escape_markdown(p.name, version=1)
            speed = p.rate_limit or LanguageManager.get('common.unlimited')
            text += LanguageManager.get(
                'admin.wg.profile_info_line',
                name=safe_name,
                price=display_price,
                vol=p.volume_gb or 0,
                dur=p.duration_days,
                speed=speed,
            )
            keyboard.append([
                InlineKeyboardButton(
                    LanguageManager.get('admin.wg.btn_edit_profile', name=p.name),
                    callback_data=f'edit_wg_prof_{p.id}',
                )
            ])
            keyboard.append([
                InlineKeyboardButton(
                    LanguageManager.get('admin.wg.btn_delete_profile'),
                    callback_data=f'del_wg_profile_{p.id}',
                )
            ])
            
    keyboard.append([InlineKeyboardButton(LanguageManager.get('admin.wg.btn_add_profile'), callback_data='add_wg_profile')])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='wg_mgmt_menu')])
    await universal_reply(update, text, reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END
# --- WireGuard Management ---

@safe_response
async def wg_mgmt_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Sub-menu for managing WireGuard."""
    query = update.callback_query
    if query: await query.answer()
    
    from vpn_bot.admin_menu import build_wg_mgmt_keyboard

    text = LanguageManager.get('admin.wg.mgmt_title')
    reply_markup = await build_wg_mgmt_keyboard(update.effective_user.id)
    
    if query:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(text, reply_markup=reply_markup, parse_mode='Markdown')

def _wg_int_back_hub_btn() -> InlineKeyboardButton:
    """One level up: WG interface settings hub (not admin main menu)."""
    return InlineKeyboardButton(
        LanguageManager.get("admin.wg.btn_back_hub"),
        callback_data="back_to_int_settings",
    )


def _wg_int_back_section_btn() -> InlineKeyboardButton:
    """One level up: current WG subsection (client / firewall / iface / admin)."""
    return InlineKeyboardButton(
        LanguageManager.get("admin.wg.btn_back_section"),
        callback_data="back_to_wg_section",
    )


def _wg_nav_handlers() -> list:
    """Navigation callbacks valid in every WG management conversation state."""
    return [
        CallbackQueryHandler(back_to_wg_section, pattern="^back_to_wg_section$"),
        CallbackQueryHandler(back_to_int_settings, pattern="^back_to_int_settings$"),
        CallbackQueryHandler(list_wg_interfaces_nav, pattern="^list_wg_interfaces$"),
    ]


def _with_wg_states(states: dict) -> dict:
    """Prepend WG back-navigation handlers so fallbacks never steal them."""
    nav = _wg_nav_handlers()
    return {state: [*nav, *handlers] for state, handlers in states.items()}


async def _wg_cb_loading(query, text_key: str = "admin.wg.mt_loading") -> None:
    """Answer callback and show loading (keeps Telegram from showing endless spinner)."""
    try:
        await query.answer()
    except Exception:
        pass
    try:
        await query.edit_message_text(
            LanguageManager.get(text_key),
            reply_markup=None,
            parse_mode="Markdown",
        )
    except Exception as exc:
        logger.debug("WG loading edit failed: %s", exc)


async def _wg_cb_error(query, message: str) -> int:
    try:
        await query.answer()
    except Exception:
        pass
    try:
        await query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup([[_wg_int_back_section_btn()]]),
            parse_mode="Markdown",
        )
    except Exception:
        pass
    return WG_INT_SETTINGS


async def _wg_apply_heartbeat(
    query,
    stop_event: asyncio.Event,
    *,
    interval: float = 15.0,
) -> None:
    """Refresh loading message while MikroTik apply is in progress."""
    import os

    max_retries = int(os.getenv("MIKROTIK_APPLY_RETRIES", "3"))
    attempt = 1
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval)
            break
        except asyncio.TimeoutError:
            pass
        if stop_event.is_set():
            break
        attempt = min(attempt + 1, max_retries)
        try:
            text = LanguageManager.get("admin.wg.mt_applying")
            text += "\n\n" + LanguageManager.get(
                "admin.wg.mt_apply_retry", attempt=attempt, max=max_retries
            )
            await query.edit_message_text(text, reply_markup=None, parse_mode="Markdown")
        except Exception as exc:
            logger.debug("WG apply heartbeat edit failed: %s", exc)


async def _wg_apply_firewall_and_return(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    interface_id: int,
) -> int:
    """Apply NAT/Mangle/Route on router; always leave loading state with result."""
    if await notify_user_busy(update, context):
        return WG_INT_SETTINGS

    context.user_data["is_processing"] = True
    query = update.callback_query
    heartbeat_stop = asyncio.Event()
    heartbeat_task = None
    try:
        if query:
            await _wg_cb_loading(query, "admin.wg.mt_applying")
            heartbeat_task = asyncio.create_task(
                _wg_apply_heartbeat(query, heartbeat_stop)
            )
        elif update.message:
            await update.message.reply_text(
                LanguageManager.get("admin.wg.mt_applying"),
                parse_mode="Markdown",
            )

        ok, err, route_applied = await apply_wg_automation_timed(interface_id)
        if not ok:
            if query:
                return await _wg_cb_error(
                    query, err or LanguageManager.get("admin.wg.mt_apply_failed")
                )
            if update.message:
                await update.message.reply_text(
                    err or LanguageManager.get("admin.wg.mt_apply_failed")
                )
            return WG_INT_SETTINGS

        iface, _ = await get_wg_interface_details(interface_id)
        if iface:
            context.user_data.pop(f"wg_route_tables_{iface.server_id}", None)
        notice = err
        if route_applied and not notice:
            notice = LanguageManager.get("admin.wg.route_applied_ok")
        if notice:
            context.user_data["wg_route_apply_notice"] = notice
        if update.message and not route_applied and not err:
            await update.message.reply_text(LanguageManager.get("admin.wg.success_generic"))
        return await _wg_return_after_edit(update, context, interface_id)
    finally:
        heartbeat_stop.set()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass
        clear_user_processing(context)


def _format_wg_interface_hub_text(interface, active_count: int) -> str:
    """Grouped summary: interface / client / NAT+Mangle (not one flat list)."""
    name = escape_markdown(interface.name, version=1)
    text = LanguageManager.get("admin.wg.interface_settings_title", name=name)
    text += LanguageManager.get(
        "admin.wg.hub_section_iface",
        address=interface.address,
        port=interface.listen_port,
        current=active_count,
        max_users=interface.max_users,
    )
    text += LanguageManager.get(
        "admin.wg.hub_section_client",
        dns=interface.dns or "1.1.1.1",
        endpoint=interface.endpoint_host or LanguageManager.get("common.default"),
        mtu=interface.mtu,
        keepalive=interface.keepalive,
    )
    rm = interface.routing_mark or "—"
    nat_rm = interface.nat_routing_mark or "—"
    text += LanguageManager.get(
        "admin.wg.hub_section_firewall",
        upstream=interface.upstream_interface or "—",
        routing_mark=rm,
        nat_routing_mark=nat_rm,
        nat_dst=_wg_format_nat_dst_display(interface),
        route_list=_wg_format_route_list_display(interface),
    )
    return text


def _wg_interface_hub_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_section_firewall"),
                    callback_data="wg_edit_sec_firewall",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_section_client"),
                    callback_data="wg_edit_sec_client",
                ),
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_section_iface"),
                    callback_data="wg_edit_sec_iface",
                ),
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_section_admin"),
                    callback_data="wg_edit_sec_admin",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("common.back"),
                    callback_data="list_wg_interfaces",
                )
            ],
        ]
    )


async def _wg_edit_reply(update: Update, text: str, markup: InlineKeyboardMarkup) -> None:
    from telegram.error import BadRequest

    try:
        if update.callback_query:
            await update.callback_query.edit_message_text(
                text, reply_markup=markup, parse_mode="Markdown"
            )
        elif update.message:
            await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    except BadRequest as exc:
        if "parse entities" not in str(exc).lower():
            raise
        if update.callback_query:
            await update.callback_query.edit_message_text(text, reply_markup=markup)
        elif update.message:
            await update.message.reply_text(text, reply_markup=markup)


def _wg_nat_dst_negate_flag(interface) -> bool:
    return bool(getattr(interface, "nat_dst_negate", True))


def _wg_format_nat_dst_display(interface) -> str:
    negate = _wg_nat_dst_negate_flag(interface)
    if getattr(interface, "nat_dst_address_list", None):
        lst = interface.nat_dst_address_list
        prefix = "!" if negate else ""
        return f"{prefix}list:{lst}"
    addr = interface.nat_dst_address or "127.0.0.1"
    return f"!{addr}" if negate else addr


def _wg_build_nat_dst_choices(list_names: list[str]) -> list[dict]:
    choices = [
        {
            "kind": "ip",
            "val": "127.0.0.1",
            "label": LanguageManager.get("admin.wg.nat_dst_choice_local"),
        }
    ]
    for name in list_names[:35]:
        choices.append({"kind": "list", "val": name, "label": f"📋 {name}"})
    return choices


def _wg_nat_dst_choice_keyboard(
    choices: list[dict],
    current_ip: str,
    current_list: str | None,
    *,
    callback_prefix: str = "set_wg_natdst",
    back_callback: str = "back_to_wg_section",
    show_custom: bool = True,
) -> InlineKeyboardMarkup:
    rows = []
    for idx, choice in enumerate(choices):
        mark = ""
        if choice["kind"] == "list" and choice["val"] == current_list:
            mark = "✅ "
        elif choice["kind"] == "ip" and not current_list and (current_ip or "127.0.0.1") == choice["val"]:
            mark = "✅ "
        rows.append(
            [
                InlineKeyboardButton(
                    f"{mark}{choice['label']}",
                    callback_data=f"{callback_prefix}_c_{idx}",
                )
            ]
        )
    if show_custom:
        rows.append(
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_nat_dst_custom"),
                    callback_data=f"{callback_prefix}_enter",
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.wg.btn_back_section")
                if back_callback == "back_to_wg_section"
                else LanguageManager.get("common.back"),
                callback_data=back_callback,
            )
        ]
    )
    return InlineKeyboardMarkup(rows)


def _wg_nat_dst_negate_keyboard(
    callback_prefix: str,
    *,
    back_callback: str = "back_to_wg_section",
) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_nat_dst_negate_yes"),
                    callback_data=f"{callback_prefix}_neg_1",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_nat_dst_negate_no"),
                    callback_data=f"{callback_prefix}_neg_0",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_back_section")
                    if back_callback == "back_to_wg_section"
                    else LanguageManager.get("common.back"),
                    callback_data=back_callback,
                )
            ],
        ]
    )


async def _wg_persist_nat_dst_choice(
    context: ContextTypes.DEFAULT_TYPE,
    interface_id: int,
    choice: dict,
    *,
    negate: bool | None = None,
) -> tuple[bool, str | None]:
    if negate is None:
        negate = bool(choice.get("negate", True))
    if choice["kind"] == "list":
        payload = {
            "nat_dst_address_list": choice["val"],
            "nat_dst_address": "127.0.0.1",
            "nat_dst_negate": negate,
        }
    else:
        payload = {
            "nat_dst_address": choice["val"],
            "nat_dst_address_list": None,
            "nat_dst_negate": negate,
        }
    return await update_wg_interface(interface_id, payload)


def _wg_parse_nat_dst_text(text: str, known_lists: list[str]) -> dict:
    raw = (text or "").strip()
    negate = False
    if raw.startswith("!"):
        negate = True
        raw = raw[1:].strip()
    if raw.lower() in ("none", "-", "—", ""):
        return {"kind": "ip", "val": "127.0.0.1", "negate": negate}
    if raw.startswith("@"):
        return {"kind": "list", "val": raw[1:].strip(), "negate": negate}
    if raw.startswith("list:"):
        inner = raw[5:].strip()
        if inner.startswith("!"):
            negate = True
            inner = inner[1:].strip()
        return {"kind": "list", "val": inner, "negate": negate}
    if raw in known_lists:
        return {"kind": "list", "val": raw, "negate": negate}
    return {"kind": "ip", "val": raw, "negate": negate}


async def _wg_show_nat_dst_negate_prompt(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    choice: dict,
    *,
    callback_prefix: str,
    back_callback: str,
    edit_state: int,
) -> int:
    """Ask whether RouterOS dst match should use negation (!)."""
    context.user_data["wg_pending_nat_dst"] = choice
    val = choice.get("val", "")
    kind = choice.get("kind", "ip")
    target = f"list `{val}`" if kind == "list" else f"`{val}`"
    text = LanguageManager.get("admin.wg.prompt_nat_dst_negate", target=target)
    markup = _wg_nat_dst_negate_keyboard(callback_prefix, back_callback=back_callback)
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    return edit_state


def _wg_route_dst_presets() -> list[dict]:
    return [
        {"label_key": "admin.wg.route_dst_default", "val": "0.0.0.0/0"},
        {"label_key": "admin.wg.route_dst_ipv6_default", "val": "::/0"},
        {"label_key": "admin.wg.route_dst_rfc1918", "val": "10.0.0.0/8"},
        {"label_key": "admin.wg.route_dst_custom", "val": "__custom__"},
    ]


def _wg_route_edit_mode(context: ContextTypes.DEFAULT_TYPE) -> str:
    """full = 4-step wizard; table/dst/gw/dist = single-field edit then apply."""
    return context.user_data.get("wg_route_edit_mode") or "full"


def _wg_set_route_edit_mode(context: ContextTypes.DEFAULT_TYPE, mode: str) -> None:
    context.user_data["wg_route_edit_mode"] = mode


async def _wg_route_finish_single_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> int:
    """After one Route List field change, sync MikroTik and return to firewall section."""
    context.user_data.pop("wg_route_edit_mode", None)
    interface_id = context.user_data.get("edit_wg_interface_id")
    if not interface_id:
        return ConversationHandler.END
    return await _wg_apply_firewall_and_return(update, context, interface_id)


def _wg_route_distance_presets() -> list[int]:
    return [1, 10]


def _wg_format_route_list_display(interface) -> str:
    table = escape_markdown(
        str(interface.route_table or interface.routing_mark or "—"), version=1
    )
    dst = escape_markdown(
        str(getattr(interface, "route_dst_address", None) or "0.0.0.0/0"), version=1
    )
    gw = escape_markdown(str(interface.gateway or "—"), version=1)
    dist = escape_markdown(
        str(
            interface.route_distance
            if getattr(interface, "route_distance", None) is not None
            else 1
        ),
        version=1,
    )
    return LanguageManager.get(
        "admin.wg.route_list_summary",
        table=table,
        dst=dst,
        gw=gw,
        distance=dist,
    )


def _wg_route_wizard_is_add(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return bool(context.user_data.get("new_wg_iface_data"))


def _wg_route_table_keyboard(
    marks: list[str],
    current: str | None,
    *,
    callback_prefix: str,
    back_callback: str,
) -> InlineKeyboardMarkup:
    """Index-based callbacks (set_wg_rt_p0) — Telegram callback_data max 64 bytes."""
    keyboard = []
    for i, m in enumerate(marks):
        prefix = "✅ " if current == m else ""
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"{prefix}🔀 {m}",
                    callback_data=f"{callback_prefix}_p{i}",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("common.btn_skip"),
                callback_data=f"{callback_prefix}_none",
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.wg.btn_back_section")
                if back_callback == "back_to_wg_section"
                else LanguageManager.get("common.back"),
                callback_data=back_callback,
            )
        ]
    )
    return InlineKeyboardMarkup(keyboard)


def _wg_route_dst_choice_keyboard(
    *,
    callback_prefix: str,
    back_callback: str,
) -> InlineKeyboardMarkup:
    keyboard = []
    for i, preset in enumerate(_wg_route_dst_presets()):
        if preset["val"] == "__custom__":
            cb = f"{callback_prefix}_custom"
        else:
            cb = f"{callback_prefix}_p{i}"
        keyboard.append(
            [
                InlineKeyboardButton(
                    LanguageManager.get(preset["label_key"]),
                    callback_data=cb,
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.wg.btn_back_section")
                if back_callback == "back_to_wg_section"
                else LanguageManager.get("common.back"),
                callback_data=back_callback,
            )
        ]
    )
    return InlineKeyboardMarkup(keyboard)


def _wg_route_distance_keyboard(
    *,
    callback_prefix: str,
    back_callback: str,
) -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(
                str(d),
                callback_data=f"{callback_prefix}_{d}",
            )
            for d in _wg_route_distance_presets()
        ],
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.wg.route_dist_custom"),
                callback_data=f"{callback_prefix}_custom",
            )
        ],
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.wg.btn_back_section")
                if back_callback == "back_to_wg_section"
                else LanguageManager.get("common.back"),
                callback_data=back_callback,
            )
        ],
    ]
    return InlineKeyboardMarkup(keyboard)


def _wg_validate_route_dst(text: str) -> str | None:
    import ipaddress

    raw = (text or "").strip()
    if not raw:
        return None
    try:
        if "/" in raw:
            ipaddress.ip_network(raw, strict=False)
        else:
            ipaddress.ip_address(raw)
        return raw
    except ValueError:
        return None


async def _wg_show_route_table_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    is_add: bool,
) -> int:
    """Route List > Routing Table — load tables from MikroTik."""
    query = update.callback_query
    clear_user_processing(context)

    if is_add:
        data = context.user_data.get("new_wg_iface_data") or {}
        server_id = data.get("server_id")
        current = data.get("route_table")
        back_callback = "list_wg_interfaces"
        cb_prefix = "man_wg_rt"
        next_state = WG_ADD_INT_MAN_ROUTE_TABLE
    else:
        interface_id = context.user_data.get("edit_wg_interface_id")
        if not interface_id:
            return ConversationHandler.END
        interface, _ = await get_wg_interface_details(interface_id)
        if not interface:
            if query:
                return await _wg_cb_error(query, LanguageManager.get("common.error"))
            return ConversationHandler.END
        server_id = interface.server_id
        current = interface.route_table
        back_callback = "back_to_wg_section"
        cb_prefix = "set_wg_rt"
        next_state = WG_INT_ROUTE_TABLE

    server = await get_server_by_id(server_id)
    if not server:
        if query:
            return await _wg_cb_error(query, LanguageManager.get("common.error"))
        return ConversationHandler.END

    if await notify_user_busy(update, context):
        return next_state

    context.user_data["is_processing"] = True
    try:
        if query:
            await _wg_cb_loading(query, "admin.wg.mt_loading")

        marks, err = await _wg_cached_routing_tables(context, server_id, server)
    finally:
        clear_user_processing(context)

    if err:
        msg = f"{err}\n\n{LanguageManager.get('admin.wg.mt_list_failed_hint')}"
        if query:
            return await _wg_cb_error(query, msg)
        if update.message:
            await update.message.reply_text(msg)
        return ConversationHandler.END if not is_add else WG_ADD_INT_MAN_ROUTE_TABLE
    marks = list(marks or [])
    if "main" not in marks:
        marks.insert(0, "main")
    context.user_data["wg_route_table_choices"] = marks

    text = LanguageManager.get("admin.wg.prompt_route_table")
    markup = _wg_route_table_keyboard(
        marks, current, callback_prefix=cb_prefix, back_callback=back_callback
    )
    if query:
        await query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    return next_state


async def _wg_show_route_dst_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    is_add: bool,
) -> int:
    context.user_data["wg_route_dst_choices"] = _wg_route_dst_presets()
    if is_add:
        cb_prefix = "man_wg_rdst"
        back_callback = "list_wg_interfaces"
        next_state = WG_ADD_INT_MAN_ROUTE_DST
    else:
        cb_prefix = "set_wg_rdst"
        back_callback = "back_to_wg_section"
        next_state = WG_INT_ROUTE_DST
    text = LanguageManager.get("admin.wg.prompt_route_dst")
    if not is_add and _wg_route_edit_mode(context) == "full":
        text += "\n\n" + LanguageManager.get("admin.wg.route_saved_step_hint")
    markup = _wg_route_dst_choice_keyboard(
        callback_prefix=cb_prefix, back_callback=back_callback
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=markup, parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    return next_state


async def _wg_route_gateway_presets(
    context: ContextTypes.DEFAULT_TYPE,
) -> list[str]:
    """Suggested gateway IPs (existing DB value + common LAN gateways)."""
    seen: set[str] = set()
    presets: list[str] = []
    interface_id = context.user_data.get("edit_wg_interface_id")
    if interface_id:
        iface, _ = await get_wg_interface_details(interface_id)
        if iface and (iface.gateway or "").strip():
            g = iface.gateway.strip()
            seen.add(g)
            presets.append(g)
    data = context.user_data.get("new_wg_iface_data") or {}
    if data.get("gateway"):
        g = str(data["gateway"]).strip()
        if g and g not in seen:
            seen.add(g)
            presets.append(g)
    for candidate in ("192.168.88.1", "192.168.1.1", "10.0.0.1"):
        if candidate not in seen:
            seen.add(candidate)
            presets.append(candidate)
    return presets[:5]


async def _wg_show_route_gateway_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    is_add: bool,
) -> int:
    cb_gw = "man_wg_rgw" if is_add else "set_wg_rgw"
    presets = await _wg_route_gateway_presets(context)
    context.user_data["wg_route_gw_choices"] = presets
    keyboard: list[list[InlineKeyboardButton]] = []
    for i, gw in enumerate(presets):
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"🌐 {gw}",
                    callback_data=f"{cb_gw}_p{i}",
                )
            ]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.wg.btn_route_gw_skip"),
                callback_data=f"{cb_gw}_none",
            )
        ]
    )
    back_cb = "list_wg_interfaces" if is_add else "back_to_wg_section"
    keyboard.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.wg.btn_back_section")
                if back_cb == "back_to_wg_section"
                else LanguageManager.get("common.back"),
                callback_data=back_cb,
            )
        ]
    )
    markup = InlineKeyboardMarkup(keyboard)
    text = LanguageManager.get("admin.wg.prompt_route_gateway")
    text += LanguageManager.get("admin.wg.prompt_route_gateway_help")
    if not is_add and _wg_route_edit_mode(context) == "full":
        text += "\n\n" + LanguageManager.get("admin.wg.route_saved_step_hint")
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=markup, parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    return WG_ADD_INT_MAN_ROUTE_GW if is_add else WG_INT_ROUTE_GW


async def _wg_show_route_distance_step(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    is_add: bool,
) -> int:
    cb_prefix = "man_wg_rdist" if is_add else "set_wg_rdist"
    back_callback = "list_wg_interfaces" if is_add else "back_to_wg_section"
    text = LanguageManager.get("admin.wg.prompt_route_distance")
    markup = _wg_route_distance_keyboard(
        callback_prefix=cb_prefix, back_callback=back_callback
    )
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=markup, parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(text, reply_markup=markup, parse_mode="Markdown")
    return WG_ADD_INT_MAN_ROUTE_DIST if is_add else WG_INT_ROUTE_DIST


async def _wg_section_begin(update: Update) -> None:
    """Ack inline button; no-op for MessageHandler returns (e.g. gateway text input)."""
    if update.callback_query:
        await update.callback_query.answer()


async def _wg_cached_routing_tables(
    context: ContextTypes.DEFAULT_TYPE,
    server_id: int,
    server,
) -> tuple[list | None, str | None]:
    """Per-session cache so Route List wizard does not hammer MikroTik on each step."""
    cache_key = f"wg_route_tables_{server_id}"
    cached = context.user_data.get(cache_key)
    if cached is not None:
        return list(cached), None
    marks, err = await fetch_routing_tables_timed(server)
    if err:
        return None, err
    marks = list(marks or [])
    context.user_data[cache_key] = marks
    return marks, None


async def wg_interface_settings(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    interface_id: int | None = None,
):
    """WireGuard interface settings hub (grouped sub-menus)."""
    clear_user_processing(context)
    query = update.callback_query

    if interface_id is None:
        if query and query.data and query.data.startswith("edit_wg_interface_"):
            interface_id = int(query.data.rsplit("_", 1)[-1])
        else:
            interface_id = context.user_data.get("edit_wg_interface_id")
        if not interface_id:
            return ConversationHandler.END

    if query:
        await query.answer()

    interface, active_count = await get_wg_interface_details(interface_id)
    if not interface:
        return ConversationHandler.END
    interface.current_users = active_count

    context.user_data["edit_wg_interface_id"] = interface_id
    context.user_data.pop("wg_edit_section", None)

    await _wg_edit_reply(
        update,
        _format_wg_interface_hub_text(interface, active_count),
        _wg_interface_hub_keyboard(),
    )
    return WG_INT_SETTINGS


async def wg_edit_section_firewall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """NAT + Mangle + Route settings (Mangle assign vs NAT match routing-mark)."""
    await _wg_section_begin(update)
    interface_id = context.user_data.get("edit_wg_interface_id")
    interface, _ = await get_wg_interface_details(interface_id)
    if not interface:
        return ConversationHandler.END

    context.user_data["wg_edit_section"] = "firewall"
    name = escape_markdown(interface.name, version=1)
    text = LanguageManager.get("admin.wg.section_firewall_title", name=name)
    apply_notice = (context.user_data.pop("wg_route_apply_notice", None) or "").strip()
    if apply_notice:
        text += f"\n\n{apply_notice}"
    text += LanguageManager.get("admin.wg.section_firewall_help")
    text += LanguageManager.get(
        "admin.wg.section_firewall_values",
        upstream=escape_markdown(str(interface.upstream_interface or "—"), version=1),
        routing_mark=escape_markdown(str(interface.routing_mark or "—"), version=1),
        nat_routing_mark=escape_markdown(str(interface.nat_routing_mark or "—"), version=1),
        nat_dst=escape_markdown(_wg_format_nat_dst_display(interface), version=1),
        route_list=_wg_format_route_list_display(interface),
    )

    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_nat_out_iface"),
                    callback_data="set_wg_upstream_start",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_mangle_routing_mark"),
                    callback_data="set_wg_rm_start",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_nat_routing_mark"),
                    callback_data="set_wg_natrm_start",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_nat_dst_addr"),
                    callback_data="set_wg_natdst_start",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_route_list"),
                    callback_data="wg_edit_sec_route",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_reapply_firewall"),
                    callback_data="wg_reapply_firewall",
                )
            ],
            [_wg_int_back_hub_btn()],
        ]
    )
    await _wg_edit_reply(update, text, markup)
    return WG_INT_SETTINGS


def _wg_route_section_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_route_list_wizard"),
                    callback_data="set_wg_route_list_start",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_route_table"),
                    callback_data="set_wg_route_step_table",
                ),
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_route_dst"),
                    callback_data="set_wg_route_step_dst",
                ),
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_route_gateway"),
                    callback_data="set_wg_route_step_gw",
                ),
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_route_distance"),
                    callback_data="set_wg_route_step_dist",
                ),
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_back_section"),
                    callback_data="wg_edit_sec_firewall",
                )
            ],
        ]
    )


async def wg_edit_section_route(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route List sub-menu (table, dst, gateway, distance, full wizard)."""
    await _wg_section_begin(update)
    interface_id = context.user_data.get("edit_wg_interface_id")
    interface, _ = await get_wg_interface_details(interface_id)
    if not interface:
        return ConversationHandler.END

    context.user_data["wg_edit_section"] = "route"
    name = escape_markdown(interface.name, version=1)
    text = LanguageManager.get("admin.wg.section_route_title", name=name)
    apply_notice = (context.user_data.pop("wg_route_apply_notice", None) or "").strip()
    if apply_notice:
        text += f"\n\n{apply_notice}"
    text += LanguageManager.get(
        "admin.wg.section_route_values",
        route_list=_wg_format_route_list_display(interface),
    )
    await _wg_edit_reply(update, text, _wg_route_section_keyboard())
    return WG_INT_SETTINGS


async def wg_edit_section_client(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _wg_section_begin(update)
    interface_id = context.user_data.get("edit_wg_interface_id")
    interface, _ = await get_wg_interface_details(interface_id)
    if not interface:
        return ConversationHandler.END

    context.user_data["wg_edit_section"] = "client"
    name = escape_markdown(interface.name, version=1)
    text = LanguageManager.get("admin.wg.section_client_title", name=name)
    text += LanguageManager.get(
        "admin.wg.section_client_values",
        dns=interface.dns or "1.1.1.1",
        endpoint=interface.endpoint_host or LanguageManager.get("common.default"),
        mtu=interface.mtu,
        keepalive=interface.keepalive,
    )
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_set_dns"),
                    callback_data="set_wg_dns",
                ),
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_set_endpoint"),
                    callback_data="set_wg_endpoint",
                ),
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_set_mtu"),
                    callback_data="set_wg_mtu",
                ),
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_set_keepalive"),
                    callback_data="set_wg_keepalive",
                ),
            ],
            [_wg_int_back_hub_btn()],
        ]
    )
    await _wg_edit_reply(update, text, markup)
    return WG_INT_SETTINGS


async def wg_edit_section_iface(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _wg_section_begin(update)
    interface_id = context.user_data.get("edit_wg_interface_id")
    interface, active_count = await get_wg_interface_details(interface_id)
    if not interface:
        return ConversationHandler.END

    context.user_data["wg_edit_section"] = "iface"
    name = escape_markdown(interface.name, version=1)
    text = LanguageManager.get("admin.wg.section_iface_title", name=name)
    text += LanguageManager.get(
        "admin.wg.section_iface_values",
        address=interface.address,
        port=interface.listen_port,
        current=active_count,
        max_users=interface.max_users,
    )
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_set_address"),
                    callback_data="set_wg_address",
                ),
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_set_port"),
                    callback_data="set_wg_port",
                ),
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_set_max_users"),
                    callback_data="set_wg_max_users",
                )
            ],
            [_wg_int_back_hub_btn()],
        ]
    )
    await _wg_edit_reply(update, text, markup)
    return WG_INT_SETTINGS


async def wg_edit_section_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _wg_section_begin(update)
    interface_id = context.user_data.get("edit_wg_interface_id")
    interface, active_count = await get_wg_interface_details(interface_id)
    if not interface:
        return ConversationHandler.END

    context.user_data["wg_edit_section"] = "admin"
    name = escape_markdown(interface.name, version=1)
    text = LanguageManager.get("admin.wg.section_admin_title", name=name)
    text += LanguageManager.get(
        "admin.wg.section_admin_values", current=active_count, max_users=interface.max_users
    )
    markup = InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_edit_notif_tpl"),
                    callback_data="set_wg_notif_tpl",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_broadcast_update"),
                    callback_data="manual_wg_notify",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_migrate"),
                    callback_data="wg_migrate_start",
                )
            ],
            [
                InlineKeyboardButton(
                    LanguageManager.get("admin.wg.btn_delete"),
                    callback_data="wg_delete_start",
                )
            ],
            [_wg_int_back_hub_btn()],
        ]
    )
    await _wg_edit_reply(update, text, markup)
    return WG_INT_SETTINGS


async def _wg_return_after_edit(update: Update, context: ContextTypes.DEFAULT_TYPE, interface_id: int):
    """Return to the sub-menu the admin came from, or the main hub."""
    section = context.user_data.get("wg_edit_section")
    if section == "route":
        return await wg_edit_section_route(update, context)
    if section == "firewall":
        return await wg_edit_section_firewall(update, context)
    if section == "client":
        return await wg_edit_section_client(update, context)
    if section == "iface":
        return await wg_edit_section_iface(update, context)
    if section == "admin":
        return await wg_edit_section_admin(update, context)
    return await wg_interface_settings(update, context, interface_id=interface_id)


async def wg_reapply_firewall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-push NAT/Mangle/Route rules from DB to MikroTik."""
    interface_id = context.user_data.get("edit_wg_interface_id")
    if not interface_id:
        return ConversationHandler.END
    context.user_data["wg_edit_section"] = "firewall"
    return await _wg_apply_firewall_and_return(update, context, interface_id)

async def set_wg_dns_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(LanguageManager.get('admin.wg.dns_prompt'), 
        reply_markup=InlineKeyboardMarkup([[_wg_int_back_section_btn()]]))
    return WG_INT_DNS

async def get_wg_dns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored get_wg_dns using service."""
    new_dns = update.message.text.strip()
    interface_id = context.user_data.get('edit_wg_interface_id')
    
    success, error = await update_wg_interface(interface_id, {'dns': new_dns})
    if not success:
        return await _reply_wg_service_error(update, error, return_state=WG_INT_SETTINGS)
        
    return await ask_to_notify_users(update, context)

async def set_wg_endpoint_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(LanguageManager.get('admin.wg.endpoint_prompt'), 
        reply_markup=InlineKeyboardMarkup([[_wg_int_back_section_btn()]]))
    return WG_INT_ENDPOINT

async def get_wg_endpoint(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored get_wg_endpoint using service."""
    new_endpoint = update.message.text.strip()
    interface_id = context.user_data.get('edit_wg_interface_id')
    
    success, error = await update_wg_interface(interface_id, {'endpoint_host': new_endpoint})
    if not success:
        return await _reply_wg_service_error(update, error, return_state=WG_INT_SETTINGS)
        
    return await ask_to_notify_users(update, context)

async def search_wg_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start searching for a WireGuard user/subscription."""
    await universal_reply(update, LanguageManager.get('admin.wg.search_prompt'),
                          reply_markup=get_admin_edit_inline_keyboard('wg_mgmt_menu'))
    return SEARCH_WG_INPUT

async def search_wg_receive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Redirect WG identifier search to user management hub."""
    query_text = update.message.text.strip()
    user = await find_user_by_query(query_text)
    if not user:
        await update.message.reply_text(
            LanguageManager.get("admin.wg_config.not_found", query=query_text),
            reply_markup=get_admin_edit_inline_keyboard("wg_mgmt_menu"),
        )
        return SEARCH_WG_INPUT
    context.user_data["user_ovpn_page"] = 0
    context.user_data["user_wg_page"] = 0
    await show_user_hub(update, context, user.id)
    return ConversationHandler.END

async def set_wg_port_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(LanguageManager.get('admin.wg.port_prompt'), 
        reply_markup=InlineKeyboardMarkup([[_wg_int_back_section_btn()]]))
    return WG_INT_PORT

async def get_wg_port(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored get_wg_port using service."""
    try:
        new_port = int(update.message.text.strip())
    except:
        await update.message.reply_text(LanguageManager.get('admin.wg.error_invalid_number'))
        return WG_INT_PORT
        
    interface_id = context.user_data.get('edit_wg_interface_id')
    success, error = await update_wg_interface(interface_id, {'listen_port': new_port})
    if not success:
        return await _reply_wg_service_error(update, error, return_state=WG_INT_SETTINGS)
                
    return await ask_to_notify_users(update, context)

async def set_wg_mtu_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(LanguageManager.get('admin.wg.mtu_prompt'), 
        reply_markup=InlineKeyboardMarkup([[_wg_int_back_section_btn()]]))
    return WG_INT_MTU

async def get_wg_mtu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored get_wg_mtu using service."""
    try:
        new_mtu = int(update.message.text.strip())
    except:
        await update.message.reply_text(LanguageManager.get('admin.wg.error_invalid_number'))
        return WG_INT_MTU
        
    interface_id = context.user_data.get('edit_wg_interface_id')
    success, error = await update_wg_interface(interface_id, {'mtu': new_mtu})
    if not success:
        return await _reply_wg_service_error(update, error, return_state=WG_INT_SETTINGS)
                
    return await ask_to_notify_users(update, context)

async def set_wg_keepalive_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(LanguageManager.get('admin.wg.keepalive_prompt'), 
        reply_markup=InlineKeyboardMarkup([[_wg_int_back_section_btn()]]))
    return WG_INT_KEEPALIVE

async def get_wg_keepalive(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored get_wg_keepalive using service."""
    try:
        new_ka = int(update.message.text.strip())
    except:
        await update.message.reply_text(LanguageManager.get('admin.wg.error_invalid_number'))
        return WG_INT_KEEPALIVE
        
    interface_id = context.user_data.get('edit_wg_interface_id')
    success, error = await update_wg_interface(interface_id, {'keepalive': new_ka})
    if not success:
        return await _reply_wg_service_error(update, error, return_state=WG_INT_SETTINGS)
                
    return await ask_to_notify_users(update, context)
async def set_wg_address_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(LanguageManager.get('admin.wg.prompt_address'), 
        reply_markup=InlineKeyboardMarkup([[_wg_int_back_section_btn()]]), parse_mode='Markdown')
    return WG_INT_ADDRESS

async def get_wg_address(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored get_wg_address using service."""
    new_addr = update.message.text.strip()
    interface_id = context.user_data.get('edit_wg_interface_id')
    
    success, error = await update_wg_interface(interface_id, {'address': new_addr})
    if not success:
        return await _reply_wg_service_error(update, error, return_state=WG_INT_SETTINGS)

    context.user_data["wg_edit_section"] = "iface"
    return await _wg_apply_firewall_and_return(update, context, interface_id)


async def set_wg_upstream_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored set_wg_upstream_start using services."""
    query = update.callback_query
    interface_id = context.user_data.get('edit_wg_interface_id')
    if not interface_id:
        return ConversationHandler.END

    await _wg_cb_loading(query)
    interface, _ = await get_wg_interface_details(interface_id)
    if not interface:
        return await _wg_cb_error(query, LanguageManager.get("common.error"))

    server = await get_server_by_id(interface.server_id)
    ifaces, err = await fetch_upstream_interfaces_timed(server)
    if err:
        return await _wg_cb_error(query, err)
    if not ifaces:
        return await _wg_cb_error(query, LanguageManager.get("admin.wg.mt_upstream_empty"))

    keyboard = []
    for iface in ifaces:
        running = iface.get("running") is True or iface.get("running") == "true"
        status = "🟢" if running else "🔴"
        name = iface["name"]
        keyboard.append(
            [InlineKeyboardButton(f"{status} {name}", callback_data=f"set_wg_up_{name}")]
        )
    keyboard.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.wg.btn_upstream_none"),
                callback_data="set_wg_up_none",
            )
        ]
    )
    keyboard.append([_wg_int_back_section_btn()])
    await query.edit_message_text(
        LanguageManager.get("admin.wg.prompt_upstream"),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return WG_INT_UPSTREAM


async def set_wg_upstream_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored set_wg_upstream_callback using update_wg_interface."""
    query = update.callback_query
    await query.answer()
    iface_name = query.data.replace("set_wg_up_", "", 1)
    if iface_name == "none":
        iface_name = None
    interface_id = context.user_data.get("edit_wg_interface_id")

    success, error = await update_wg_interface(interface_id, {"upstream_interface": iface_name})
    if not success:
        return await _wg_cb_error(query, error or LanguageManager.get("common.error"))

    return await _wg_apply_firewall_and_return(update, context, interface_id)

async def set_wg_rm_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show routing mark selector with inline buttons fetched from MikroTik."""
    query = update.callback_query
    interface_id = context.user_data.get('edit_wg_interface_id')
    if not interface_id:
        return ConversationHandler.END

    await _wg_cb_loading(query)
    interface, _ = await get_wg_interface_details(interface_id)
    if not interface:
        return await _wg_cb_error(query, LanguageManager.get("common.error"))

    server = await get_server_by_id(interface.server_id)
    marks, err = await fetch_routing_tables_timed(server)
    if err:
        return await _wg_cb_error(query, err)
    marks = marks or []
    
    keyboard = []
    # Add 'main' always as first option if not in list
    if 'main' not in marks:
        marks.insert(0, 'main')
    
    for m in marks:
        # Highlight current selection
        prefix = "✅ " if interface.routing_mark == m else ""
        keyboard.append([InlineKeyboardButton(f"{prefix}🔀 {m}", callback_data=f"set_wg_rm_{m}")])
    
    keyboard.append([_wg_int_back_section_btn()])
    await query.edit_message_text(
        LanguageManager.get('admin.wg.prompt_routing_mark'),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return WG_INT_ROUTING_MARK

async def get_wg_rm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle routing mark selection from inline buttons or text input."""
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        new_val = query.data.replace('set_wg_rm_', '')
    else:
        new_val = update.message.text.strip()
    
    interface_id = context.user_data.get('edit_wg_interface_id')
    
    success, error = await update_wg_interface(interface_id, {'routing_mark': new_val})
    if not success:
        return await ask_to_notify_users(update, context)

    return await _wg_apply_firewall_and_return(update, context, interface_id)


async def set_wg_nat_rm_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """NAT General tab: routing-mark match (distinct from Mangle new-routing-mark)."""
    query = update.callback_query
    interface_id = context.user_data.get("edit_wg_interface_id")
    if not interface_id:
        return ConversationHandler.END

    await _wg_cb_loading(query)
    interface, _ = await get_wg_interface_details(interface_id)
    if not interface:
        return await _wg_cb_error(query, LanguageManager.get("common.error"))

    server = await get_server_by_id(interface.server_id)
    marks, err = await fetch_routing_tables_timed(server)
    if err:
        return await _wg_cb_error(query, err)
    marks = marks or []
    if "main" not in marks:
        marks.insert(0, "main")

    keyboard = []
    mangle_rm = interface.routing_mark
    if mangle_rm:
        same_label = LanguageManager.get("admin.wg.btn_nat_rm_same_mangle", mark=mangle_rm)
        prefix = "✅ " if interface.nat_routing_mark == mangle_rm else ""
        keyboard.append(
            [
                InlineKeyboardButton(
                    f"{prefix}{same_label}",
                    callback_data="set_wg_natrm_same",
                )
            ]
        )
    none_prefix = "✅ " if interface.nat_routing_mark in (None, "") else ""
    keyboard.append(
        [
            InlineKeyboardButton(
                f"{none_prefix}{LanguageManager.get('admin.wg.btn_nat_rm_none')}",
                callback_data="set_wg_natrm_none",
            )
        ]
    )
    for m in marks:
        prefix = "✅ " if interface.nat_routing_mark == m else ""
        keyboard.append(
            [InlineKeyboardButton(f"{prefix}🔀 {m}", callback_data=f"set_wg_natrm_{m}")]
        )
    keyboard.append([_wg_int_back_section_btn()])
    await query.edit_message_text(
        LanguageManager.get("admin.wg.prompt_nat_routing_mark"),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )
    return WG_INT_NAT_ROUTING_MARK


async def get_wg_nat_rm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        query = update.callback_query
        await query.answer()
        raw = query.data.replace("set_wg_natrm_", "", 1)
        if raw == "same":
            interface_id = context.user_data.get("edit_wg_interface_id")
            interface, _ = await get_wg_interface_details(interface_id)
            new_val = interface.routing_mark
        elif raw == "none":
            new_val = None
        else:
            new_val = raw
    else:
        text = update.message.text.strip()
        new_val = None if not text or text.lower() in ("none", "-", "—") else text

    interface_id = context.user_data.get("edit_wg_interface_id")
    success, error = await update_wg_interface(interface_id, {"nat_routing_mark": new_val})
    if not success:
        return await ask_to_notify_users(update, context)

    return await _wg_apply_firewall_and_return(update, context, interface_id)


async def set_wg_natdst_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    interface_id = context.user_data.get("edit_wg_interface_id")
    if not interface_id:
        return ConversationHandler.END

    await _wg_cb_loading(query)
    interface, _ = await get_wg_interface_details(interface_id)
    if not interface:
        return await _wg_cb_error(query, LanguageManager.get("common.error"))

    server = await get_server_by_id(interface.server_id)
    list_names, err = await fetch_address_list_names_timed(server)
    if err:
        return await _wg_cb_error(query, err)

    choices = _wg_build_nat_dst_choices(list_names or [])
    context.user_data["wg_nat_dst_choices"] = choices
    markup = _wg_nat_dst_choice_keyboard(
        choices,
        interface.nat_dst_address,
        interface.nat_dst_address_list,
    )
    current = _wg_format_nat_dst_display(interface)
    text = LanguageManager.get("admin.wg.nat_dst_current", current=current)
    text += "\n\n" + LanguageManager.get("admin.wg.prompt_nat_dst")
    await query.edit_message_text(text, reply_markup=markup, parse_mode="Markdown")
    return WG_INT_NAT_DST


async def get_wg_natdst(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pick NAT dst (IP or address-list); optional custom entry; then negate (!) step."""
    interface_id = context.user_data.get("edit_wg_interface_id")
    choice = None
    explicit_negate = None

    if update.callback_query:
        query = update.callback_query
        await query.answer()
        data = query.data or ""
        if data == "set_wg_natdst_enter":
            await query.edit_message_text(
                LanguageManager.get("admin.wg.prompt_nat_dst_custom"),
                reply_markup=InlineKeyboardMarkup([[_wg_int_back_section_btn()]]),
                parse_mode="Markdown",
            )
            return WG_INT_NAT_DST
        if data.startswith("set_wg_natdst_c_"):
            idx = int(data.rsplit("_", 1)[-1])
            choices = context.user_data.get("wg_nat_dst_choices") or []
            if 0 <= idx < len(choices):
                choice = dict(choices[idx])
    else:
        list_names = [
            c["val"] for c in (context.user_data.get("wg_nat_dst_choices") or []) if c["kind"] == "list"
        ]
        parsed = _wg_parse_nat_dst_text(update.message.text, list_names)
        if not parsed:
            await update.message.reply_text(LanguageManager.get("admin.wg.nat_dst_invalid"))
            return WG_INT_NAT_DST
        raw = (update.message.text or "").strip()
        if raw.startswith("!") or raw.lower().startswith("list:!") or raw.lower().startswith("@!"):
            choice = parsed
            explicit_negate = parsed.get("negate", True)
        else:
            choice = parsed

    if not choice:
        if update.message:
            await update.message.reply_text(LanguageManager.get("admin.wg.nat_dst_invalid"))
        return WG_INT_NAT_DST

    if explicit_negate is not None:
        success, error = await _wg_persist_nat_dst_choice(
            context, interface_id, choice, negate=explicit_negate
        )
        if not success:
            return await ask_to_notify_users(update, context)
        return await _wg_apply_firewall_and_return(update, context, interface_id)

    return await _wg_show_nat_dst_negate_prompt(
        update,
        context,
        choice,
        callback_prefix="set_wg_natdst",
        back_callback="back_to_wg_section",
        edit_state=WG_INT_NAT_DST_NEGATE,
    )


async def get_wg_natdst_negate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Apply negate choice for pending NAT dst and sync to MikroTik."""
    interface_id = context.user_data.get("edit_wg_interface_id")
    choice = context.user_data.pop("wg_pending_nat_dst", None)
    if not choice or not interface_id:
        return ConversationHandler.END

    query = update.callback_query
    await query.answer()
    negate = query.data.endswith("_neg_1")

    success, error = await _wg_persist_nat_dst_choice(
        context, interface_id, choice, negate=negate
    )
    if not success:
        return await ask_to_notify_users(update, context)

    return await _wg_apply_firewall_and_return(update, context, interface_id)


async def set_wg_route_list_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route List wizard — step 1: Routing Table."""
    clear_user_processing(context)
    _wg_set_route_edit_mode(context, "full")
    return await _wg_show_route_table_step(update, context, is_add=False)


async def set_wg_route_step_table(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit only Route List > Routing Table."""
    _wg_set_route_edit_mode(context, "table")
    return await _wg_show_route_table_step(update, context, is_add=False)


async def set_wg_route_step_dst(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit only Route List > Dst. Address."""
    if update.callback_query:
        await update.callback_query.answer()
    _wg_set_route_edit_mode(context, "dst")
    return await _wg_show_route_dst_step(update, context, is_add=False)


async def set_wg_route_step_gw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit only Route List > Gateway."""
    if update.callback_query:
        await update.callback_query.answer()
    _wg_set_route_edit_mode(context, "gw")
    return await _wg_show_route_gateway_step(update, context, is_add=False)


async def set_wg_route_step_dist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Edit only Route List > Distance."""
    if update.callback_query:
        await update.callback_query.answer()
    _wg_set_route_edit_mode(context, "dist")
    return await _wg_show_route_distance_step(update, context, is_add=False)


def _wg_route_cb_suffix(query_data: str, prefix: str) -> str:
    return query_data.replace(prefix, "", 1)


def _wg_parse_route_table_choice(
    context: ContextTypes.DEFAULT_TYPE, data: str, *, is_man: bool
) -> str | None:
    """Parse routing table from callback; None = skip."""
    prefix = "man_wg_rt_" if is_man else "set_wg_rt_"
    if data.endswith("_none") or data == f"{prefix}none":
        return None
    if "_rt_p" in data or (data.startswith(prefix) and "_p" in data):
        try:
            idx = int(data.rsplit("p", 1)[-1])
            choices = context.user_data.get("wg_route_table_choices") or []
            if 0 <= idx < len(choices):
                return choices[idx]
        except ValueError:
            pass
        return None
    return _wg_route_cb_suffix(data, prefix)


async def get_wg_route_table(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route List > Routing Table selection."""
    clear_user_processing(context)
    query = update.callback_query
    if query:
        await query.answer()
        data = query.data
        is_man = data.startswith("man_wg_rt_")
        val = _wg_parse_route_table_choice(context, data, is_man=is_man)
        if val is None and not data.endswith("_none") and "_p" not in data:
            return (
                WG_ADD_INT_MAN_ROUTE_TABLE
                if _wg_route_wizard_is_add(context)
                else WG_INT_ROUTE_TABLE
            )
    else:
        val = (update.message.text or "").strip()
    route_table = None if val in ("none", "") else val

    if _wg_route_wizard_is_add(context):
        data = context.user_data.setdefault("new_wg_iface_data", {})
        data["route_table"] = route_table
        return await _wg_show_route_dst_step(update, context, is_add=True)

    interface_id = context.user_data.get("edit_wg_interface_id")
    success, error = await update_wg_interface(interface_id, {"route_table": route_table})
    if not success:
        if query:
            return await _wg_cb_error(query, error or LanguageManager.get("common.error"))
        await update.message.reply_text(error or LanguageManager.get("common.error"))
        return WG_INT_SETTINGS
    if _wg_route_edit_mode(context) == "table":
        return await _wg_route_finish_single_step(update, context)
    return await _wg_show_route_dst_step(update, context, is_add=False)


async def get_wg_route_dst(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route List > Dst. Address."""
    clear_user_processing(context)
    query = update.callback_query
    presets = context.user_data.get("wg_route_dst_choices") or _wg_route_dst_presets()
    if query:
        await query.answer()
        data_cb = query.data
        if data_cb.endswith("_rdst_custom") or data_cb in ("set_wg_rdst_custom", "man_wg_rdst_custom"):
            back_btn = (
                InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="list_wg_interfaces")
                if _wg_route_wizard_is_add(context)
                else _wg_int_back_section_btn()
            )
            await query.edit_message_text(
                LanguageManager.get("admin.wg.prompt_route_dst_custom"),
                reply_markup=InlineKeyboardMarkup([[back_btn]]),
                parse_mode="Markdown",
            )
            return WG_ADD_INT_MAN_ROUTE_DST if _wg_route_wizard_is_add(context) else WG_INT_ROUTE_DST
        if "_rdst_p" in data_cb:
            idx = int(data_cb.rsplit("p", 1)[-1])
            route_dst = presets[idx]["val"]
        else:
            return WG_ADD_INT_MAN_ROUTE_DST if _wg_route_wizard_is_add(context) else WG_INT_ROUTE_DST
    else:
        route_dst = _wg_validate_route_dst(update.message.text)
        if not route_dst:
            await update.message.reply_text(LanguageManager.get("admin.wg.error_invalid_route_dst"))
            return WG_ADD_INT_MAN_ROUTE_DST if _wg_route_wizard_is_add(context) else WG_INT_ROUTE_DST

    if _wg_route_wizard_is_add(context):
        context.user_data["new_wg_iface_data"]["route_dst_address"] = route_dst
        return await _wg_show_route_gateway_step(update, context, is_add=True)

    interface_id = context.user_data.get("edit_wg_interface_id")
    success, error = await update_wg_interface(interface_id, {"route_dst_address": route_dst})
    if not success:
        if query:
            return await _wg_cb_error(query, error or LanguageManager.get("common.error"))
        await update.message.reply_text(error or LanguageManager.get("common.error"))
        return WG_INT_SETTINGS
    if _wg_route_edit_mode(context) == "dst":
        return await _wg_route_finish_single_step(update, context)
    return await _wg_show_route_gateway_step(update, context, is_add=False)


async def get_wg_route_gw(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route List > Gateway."""
    clear_user_processing(context)
    query = update.callback_query
    if query:
        await query.answer()
        data_cb = query.data
        if data_cb.endswith("_none"):
            gw = None
        elif "_rgw_p" in data_cb:
            presets = context.user_data.get("wg_route_gw_choices") or []
            try:
                idx = int(data_cb.rsplit("p", 1)[-1])
                gw = presets[idx] if 0 <= idx < len(presets) else None
            except (ValueError, IndexError):
                return WG_ADD_INT_MAN_ROUTE_GW if _wg_route_wizard_is_add(context) else WG_INT_ROUTE_GW
        else:
            return WG_ADD_INT_MAN_ROUTE_GW if _wg_route_wizard_is_add(context) else WG_INT_ROUTE_GW
    else:
        raw = (update.message.text or "").strip()
        gw = None if not raw or raw.lower() in ("none", "skip", "-") else raw

    if _wg_route_wizard_is_add(context):
        context.user_data["new_wg_iface_data"]["gateway"] = gw
        return await _wg_show_route_distance_step(update, context, is_add=True)

    interface_id = context.user_data.get("edit_wg_interface_id")
    success, error = await update_wg_interface(interface_id, {"gateway": gw})
    if not success:
        if query:
            return await _wg_cb_error(query, error or LanguageManager.get("common.error"))
        await update.message.reply_text(error or LanguageManager.get("common.error"))
        return WG_INT_SETTINGS
    if _wg_route_edit_mode(context) == "gw":
        return await _wg_route_finish_single_step(update, context)
    return await _wg_show_route_distance_step(update, context, is_add=False)


async def get_wg_route_dist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Route List > Distance — final step; sync to MikroTik."""
    clear_user_processing(context)
    query = update.callback_query
    if query:
        await query.answer()
        if query.data.endswith("_custom") or query.data.endswith("_rdist_custom"):
            back_btn = (
                InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="list_wg_interfaces")
                if _wg_route_wizard_is_add(context)
                else _wg_int_back_section_btn()
            )
            await query.edit_message_text(
                LanguageManager.get("admin.wg.prompt_route_distance_custom"),
                reply_markup=InlineKeyboardMarkup([[back_btn]]),
                parse_mode="Markdown",
            )
            return WG_ADD_INT_MAN_ROUTE_DIST if _wg_route_wizard_is_add(context) else WG_INT_ROUTE_DIST
        suffix = query.data.rsplit("_", 1)[-1]
        try:
            distance = int(suffix)
        except ValueError:
            return WG_ADD_INT_MAN_ROUTE_DIST if _wg_route_wizard_is_add(context) else WG_INT_ROUTE_DIST
    else:
        try:
            distance = int((update.message.text or "").strip())
        except ValueError:
            await update.message.reply_text(LanguageManager.get("admin.wg.error_invalid_number"))
            return WG_ADD_INT_MAN_ROUTE_DIST if _wg_route_wizard_is_add(context) else WG_INT_ROUTE_DIST

    if _wg_route_wizard_is_add(context):
        context.user_data["new_wg_iface_data"]["route_distance"] = distance
        return await _wg_add_man_complete_create(update, context)

    interface_id = context.user_data.get("edit_wg_interface_id")
    success, error = await update_wg_interface(interface_id, {"route_distance": distance})
    if not success:
        if query:
            return await _wg_cb_error(query, error or LanguageManager.get("common.error"))
        await update.message.reply_text(error or LanguageManager.get("common.error"))
        return WG_INT_SETTINGS
    context.user_data.pop("wg_route_edit_mode", None)
    return await _wg_apply_firewall_and_return(update, context, interface_id)


async def set_wg_max_users_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.callback_query.answer()
    await update.callback_query.edit_message_text(LanguageManager.get('admin.wg.prompt_max_users'), 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data="back_to_int_settings")]]), parse_mode='Markdown')
    return WG_INT_MAX_USERS

async def get_wg_max_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored get_wg_max_users using service."""
    try:
        new_max = int(update.message.text.strip())
    except:
        await update.message.reply_text(LanguageManager.get('admin.wg.error_invalid_number'))
        return WG_INT_MAX_USERS
        
    interface_id = context.user_data.get('edit_wg_interface_id')
    success, error = await update_wg_interface(interface_id, {'max_users': new_max})
    if not success:
        return await _reply_wg_service_error(update, error, return_state=WG_INT_SETTINGS)
    
    return await wg_interface_settings(update, context, interface_id=interface_id)


async def int_settings_panel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    interface_id: int,
):
    """Alias for returning to interface settings after a text input step."""
    return await wg_interface_settings(update, context, interface_id=interface_id)


# --- Notification & Broadcast ---

async def back_to_int_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to WG interface settings hub (main edit menu for one interface)."""
    query = update.callback_query
    if query:
        await query.answer()
    interface_id = context.user_data.get("edit_wg_interface_id")
    if not interface_id:
        return ConversationHandler.END
    context.user_data.pop("wg_edit_section", None)
    return await wg_interface_settings(update, context, interface_id=interface_id)


async def back_to_wg_section(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return one step: subsection (client/firewall/…) or hub if none."""
    query = update.callback_query
    if query:
        await query.answer()
    interface_id = context.user_data.get("edit_wg_interface_id")
    if not interface_id:
        return ConversationHandler.END
    section = context.user_data.get("wg_edit_section")
    if not section:
        return await wg_interface_settings(update, context, interface_id=interface_id)
    return await _wg_return_after_edit(update, context, interface_id)

async def ask_to_notify_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = LanguageManager.get('admin.wg.notify_ask')
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.wg.btn_notify_yes'), callback_data="wg_notify_yes")],
        [InlineKeyboardButton(LanguageManager.get('admin.wg.btn_notify_no'), callback_data="wg_notify_no")],
        [InlineKeyboardButton(LanguageManager.get('common.back'), callback_data="back_to_int_settings")]
    ]
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return WG_INT_NOTIFY_ASK

async def broadcast_wg_interface_update(context: ContextTypes.DEFAULT_TYPE, interface_id: int, custom_msg: str = None) -> int:
    """Standardized function to broadcast WireGuard updates with buttons and templates."""
    return await broadcast_interface_update(interface_id, context.bot, custom_msg)

async def process_wg_notification(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Notify users about WireGuard interface updates."""
    query = update.callback_query
    interface_id = context.user_data.get('edit_wg_interface_id')
    await query.answer()
    
    if query.data == "wg_notify_no":
        keyboard = [[InlineKeyboardButton(LanguageManager.get('admin.btn_back'), callback_data="admin_start")]]
        await query.edit_message_text(LanguageManager.get('admin.wg.save_no_notify'), reply_markup=InlineKeyboardMarkup(keyboard))
        return ConversationHandler.END
        
    await query.edit_message_text(LanguageManager.get('admin.wg.notify_sending'))
    
    count = await broadcast_wg_interface_update(context, interface_id)
    
    keyboard = [[InlineKeyboardButton(LanguageManager.get('admin.btn_back'), callback_data="admin_start")]]
    await query.edit_message_text(LanguageManager.get('admin.wg.notify_complete', count=count), reply_markup=InlineKeyboardMarkup(keyboard))
    return ConversationHandler.END

# --- WireGuard Interface Migration ---

async def wg_migrate_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show migration target options: create new or pick existing interface."""
    query = update.callback_query
    await query.answer()
    
    source_id = context.user_data.get('edit_wg_interface_id')
    source, active_count = await get_wg_interface_details(source_id)
    if not source:
        await query.edit_message_text(LanguageManager.get('common.error'))
        return ConversationHandler.END
    
    # List other interfaces on the same server
    all_ifaces = await get_all_wg_interfaces()
    other_interfaces = [i for i in all_ifaces if i.server_id == source.server_id and i.id != source_id and i.is_active]
    
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('admin.wg.migrate_new'), callback_data='wg_migrate_new')]
    ]
    for iface in other_interfaces:
        keyboard.append([InlineKeyboardButton(
            f"📋 {iface.name} ({iface.current_users}/{iface.max_users})",
            callback_data=f'wg_migrate_to_{iface.id}'
        )])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='back_to_int_settings')])
    
    escaped_source_name = escape_markdown(source.name, version=1)
    text = LanguageManager.get('admin.wg.migrate_prompt', name=escaped_source_name, count=active_count)
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return WG_MIGRATE_TARGET

async def wg_migrate_select_target(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process target selection, show confirmation."""
    query = update.callback_query
    await query.answer()
    
    source_id = context.user_data.get('edit_wg_interface_id')
    
    if query.data == 'wg_migrate_new':
        source, _ = await get_wg_interface_details(source_id)
        server = await get_server_by_id(source.server_id)
        mgr = get_mikrotik_manager(server)
        
        params = await asyncio.to_thread(mgr.find_available_wg_interface_params)
        if not params:
            await query.edit_message_text(LanguageManager.get('admin.wg.migrate_error'))
            return WG_MIGRATE_TARGET
        
        context.user_data['wg_migrate_target'] = {
            'type': 'new', 'params': params, 'server_id': source.server_id
        }
        target_name, target_addr = params['name'], params['address']
    else:
        target_id = int(query.data.split('_')[3])
        target, _ = await get_wg_interface_details(target_id)
        if not target:
            await query.edit_message_text(LanguageManager.get('common.error'))
            return WG_MIGRATE_TARGET
        context.user_data['wg_migrate_target'] = {
            'type': 'existing', 'interface_id': target_id, 'name': target.name, 'address': target.address
        }
        target_name, target_addr = target.name, target.address
    
    text = LanguageManager.get('admin.wg.migrate_confirm', target=target_name, address=target_addr)
    keyboard = [[InlineKeyboardButton(LanguageManager.get('common.confirm'), callback_data='wg_migrate_exec')],
                [InlineKeyboardButton(LanguageManager.get('common.cancel'), callback_data='back_to_int_settings')]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return WG_MIGRATE_CONFIRM

async def wg_migrate_confirm_exec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored wg_migrate_confirm_exec using migrate_wg_interface_logic."""
    query = update.callback_query
    await query.answer()
    
    source_id = context.user_data.get('edit_wg_interface_id')
    target_info = context.user_data.get('wg_migrate_target')
    
    if not source_id or not target_info:
        await query.edit_message_text(LanguageManager.get('common.error'))
        return ConversationHandler.END
    
    await query.edit_message_text(LanguageManager.get('admin.wg.migrate_progress'))
    
    success, result_data = await migrate_wg_interface_logic(source_id, target_info)
    
    if success:
        target_id, mt_res = result_data
        context.user_data['edit_wg_interface_id'] = target_id
        
        # Standardized notification after migration
        notify_count = await broadcast_wg_interface_update(context, target_id)
        
        # Result text
        source_name = context.user_data.get('wg_delete_interface_name', 'Source') # Fallback
        target_name = target_info.get('name', 'Target')
        
        text = LanguageManager.get('admin.wg.migrate_success',
            source=source_name, target=target_name,
            added=mt_res.get('added', 0), failed=mt_res.get('failed', 0), notified=notify_count
        )
        keyboard = [[InlineKeyboardButton(LanguageManager.get('admin.btn_back'), callback_data="admin_start")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        return ConversationHandler.END
    else:
        await query.edit_message_text(f"{LanguageManager.get('common.error')}: {result_data}")
        return ConversationHandler.END

# --- WireGuard Interface Deletion ---

async def wg_delete_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored wg_delete_start using get_wg_interface_details."""
    query = update.callback_query
    await query.answer()
    
    interface_id = context.user_data.get('edit_wg_interface_id')
    interface, active_count = await get_wg_interface_details(interface_id)
    if not interface:
        await query.edit_message_text(LanguageManager.get('common.error'))
        return ConversationHandler.END
    
    context.user_data['wg_delete_interface_name'] = interface.name
    context.user_data['wg_delete_active_count'] = active_count
    
    text = LanguageManager.get('admin.wg.delete_confirm', name=interface.name, count=active_count)
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('common.confirm'), callback_data='wg_delete_exec')],
        [InlineKeyboardButton(LanguageManager.get('common.cancel'), callback_data='back_to_int_settings')]
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return WG_DELETE_CONFIRM

async def wg_delete_confirm_exec(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored wg_delete_confirm_exec using services."""
    from vpn_bot.admin_wg_service import delete_wg_interface_on_router_timed

    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass

    interface_id = context.user_data.get('edit_wg_interface_id')
    iface_name = context.user_data.get('wg_delete_interface_name', f"Interface {interface_id}")

    await query.edit_message_text(
        LanguageManager.get('admin.wg.deleting_interface', name=iface_name),
        parse_mode='Markdown',
    )

    router_ok = True
    router_err = None
    interface, _ = await get_wg_interface_details(interface_id)
    if interface:
        server = await get_server_by_id(interface.server_id)
        if server:
            router_ok, router_err = await delete_wg_interface_on_router_timed(server, iface_name)

    await delete_wg_interface(interface_id)

    for key in (
        'edit_wg_interface_id',
        'wg_delete_interface_name',
        'wg_delete_active_count',
    ):
        context.user_data.pop(key, None)

    notice = LanguageManager.get('admin.wg.delete_success', name=iface_name)
    if not router_ok:
        notice += "\n\n⚠️ " + (router_err or LanguageManager.get('admin.wg.router_delete_failed'))
    context.user_data['wg_list_notice'] = notice
    await _render_wg_interfaces_list(
        update, context, page=0, answer_callback=False, skip_router_sync=True
    )
    context.user_data.pop('wg_list_notice', None)
    return ConversationHandler.END

async def edit_wg_notif_template_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored edit_wg_notif_template_start using service."""
    await update.callback_query.answer()
    current = await get_custom_message('wg_update_template', LanguageManager.get('common.default'))
    
    await update.callback_query.edit_message_text(LanguageManager.get('admin.wg.edit_tpl_title', current=current), 
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data="back_to_int_settings")]]),
        parse_mode='Markdown')
    return WG_NOTIFICATION_TEMPLATE

async def get_wg_notif_template(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored get_wg_notif_template using service."""
    new_tpl = update.message.text.strip()
    await set_custom_message('wg_update_template', new_tpl)
    await update.message.reply_text(LanguageManager.get('admin.wg.tpl_saved'))
    interface_id = context.user_data.get("edit_wg_interface_id")
    return await wg_interface_settings(update, context, interface_id=interface_id)

# The following list_wg_profiles function is removed as it was a duplicate.
# The one at line 200 is kept.

# --- WireGuard Profile Management Conversation ---

@safe_response
async def add_wg_profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored add_wg_profile_start."""
    query = update.callback_query
    await query.answer()
    context.user_data['new_wg_profile'] = {}
    
    await admin_conv_prompt(
        update,
        LanguageManager.get('admin.wg_profile.add_prompt_name', current=""),
        reply_markup=get_admin_edit_inline_keyboard("list_wg_profiles"),
    )
    return WG_PROFILE_NAME

async def edit_wg_profile_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored edit_wg_profile_start."""
    query = update.callback_query
    pid = int(query.data.split('_')[3])
    await query.answer()
    
    profile = await get_wg_profile_by_id(pid)
    if not profile:
        await query.message.reply_text(LanguageManager.get('common.error'))
        return ConversationHandler.END
        
    context.user_data['new_wg_profile'] = {
        'id': profile.id,
        'name': profile.name,
        'volume': profile.volume_gb or 0,
        'days': profile.duration_days,
        'price_usd': profile.price_usd,
        'price_toman': profile.price_toman,
        'rate_limit': profile.rate_limit
    }
    
    current = LanguageManager.get('admin.wg_profile.current_name', val=profile.name)
    await admin_conv_prompt(
        update,
        LanguageManager.get('admin.wg_profile.add_prompt_name', current=current),
        reply_markup=get_admin_edit_inline_keyboard("list_wg_profiles"),
    )
    return WG_PROFILE_NAME

async def get_wg_profile_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if is_conv_cancel(update):
        return await admin_exit_to_menu(update, context)
    if text == '/edit':
        return await list_wg_profiles(update, context)
    
    context.user_data['new_wg_profile']['name'] = text
    data = context.user_data['new_wg_profile']
    current = LanguageManager.get('admin.wg_profile.current_volume', val=data.get('volume', 'N/A')) if 'id' in data else ""
    
    await admin_conv_prompt(
        update,
        LanguageManager.get('admin.wg_profile.add_prompt_volume', current=current),
        reply_markup=get_admin_edit_inline_keyboard("wg_prof_back_name"),
    )
    return WG_PROFILE_VOLUME

async def handle_wg_prof_back(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = context.user_data.get('new_wg_profile')
    if not data: return await list_wg_profiles(update, context)

    step = query.data.split('_')[3]
    if step == "list":
         return await list_wg_profiles(update, context)
    elif step == "name":
        current = LanguageManager.get('admin.wg_profile.current_name', val=data.get('name', 'N/A'))
        await query.edit_message_text(LanguageManager.get('admin.wg_profile.add_prompt_name', current=current), 
                                     reply_markup=get_admin_edit_inline_keyboard("list_wg_profiles"), parse_mode='Markdown')
        return WG_PROFILE_NAME
    elif step == "volume":
        current = LanguageManager.get('admin.wg_profile.current_volume', val=data.get('volume', 'N/A'))
        await query.edit_message_text(LanguageManager.get('admin.wg_profile.add_prompt_volume', current=current), 
                                     reply_markup=get_admin_edit_inline_keyboard("wg_prof_back_name"), parse_mode='Markdown')
        return WG_PROFILE_VOLUME
    elif step == "days":
        current = LanguageManager.get('admin.wg_profile.current_days', val=data.get('days', 'N/A'))
        await query.edit_message_text(LanguageManager.get('admin.wg_profile.add_prompt_days', current=current), 
                                     reply_markup=get_admin_edit_inline_keyboard("wg_prof_back_volume"), parse_mode='Markdown')
        return WG_PROFILE_DAYS
    elif step == "price_usd":
        current = LanguageManager.get('admin.wg_profile.current_price_usd', val=data.get('price_usd', 'N/A'))
        await query.edit_message_text(LanguageManager.get('admin.wg_profile.add_prompt_price_usd', current=current), 
                                     reply_markup=get_admin_edit_inline_keyboard("wg_prof_back_days"), parse_mode='Markdown')
        return WG_PROFILE_PRICE_USD
    elif step == "price_toman":
        current = LanguageManager.get('admin.wg_profile.current_price', val=data.get('price_toman', 'N/A'))
        await query.edit_message_text(LanguageManager.get('admin.wg_profile.add_prompt_price_toman', current=current), 
                                     reply_markup=get_admin_edit_inline_keyboard("wg_prof_back_price_usd"), parse_mode='Markdown')
        return WG_PROFILE_PRICE_TOMAN
    elif step == "rate":
        current = LanguageManager.get('admin.wg_profile.current_rate', val=data.get('rate_limit', 'Unlimited'))
        await query.edit_message_text(LanguageManager.get('admin.wg_profile.add_prompt_rate_limit', current=current), 
                                     reply_markup=get_admin_edit_inline_keyboard("wg_prof_back_price_toman"), parse_mode='Markdown')
        return WG_PROFILE_RATE_LIMIT
    return await list_wg_profiles(update, context)

async def get_wg_profile_volume(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if is_conv_cancel(update):
        return await admin_exit_to_menu(update, context)
    if text == '/edit':
        data = context.user_data['new_wg_profile']
        current = LanguageManager.get('admin.wg_profile.current_name', val=data.get('name', 'N/A'))
        await update.message.reply_text(LanguageManager.get('admin.wg_profile.add_prompt_name', current=current), 
                                       reply_markup=get_admin_edit_inline_keyboard("list_wg_profiles"), parse_mode='Markdown')
        return WG_PROFILE_NAME

    if not text.isdigit():
        await update.message.reply_text(LanguageManager.get('admin.profile.error_invalid_gb'))
        return WG_PROFILE_VOLUME
        
    context.user_data['new_wg_profile']['volume'] = int(text)
    data = context.user_data['new_wg_profile']
    current = LanguageManager.get('admin.wg_profile.current_days', val=data.get('days', 'N/A')) if 'id' in data else ""
    
    await update.message.reply_text(
        LanguageManager.get('admin.wg_profile.add_prompt_days', current=current),
        reply_markup=get_admin_edit_inline_keyboard("wg_prof_back_volume"),
        parse_mode='Markdown'
    )
    return WG_PROFILE_DAYS

async def get_wg_profile_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if is_conv_cancel(update):
        return await admin_exit_to_menu(update, context)
    if text == '/edit':
        data = context.user_data['new_wg_profile']
        current = LanguageManager.get('admin.wg_profile.current_volume', val=data.get('volume', 'N/A'))
        await update.message.reply_text(LanguageManager.get('admin.wg_profile.add_prompt_volume', current=current), 
                                       reply_markup=get_admin_edit_inline_keyboard("wg_prof_back_name"), parse_mode='Markdown')
        return WG_PROFILE_VOLUME

    if not text.isdigit():
        await update.message.reply_text(LanguageManager.get('admin.profile.error_invalid_days'))
        return WG_PROFILE_DAYS
        
    context.user_data['new_wg_profile']['days'] = int(text)
    data = context.user_data['new_wg_profile']
    current = LanguageManager.get('admin.wg_profile.current_price', val=data.get('price', 'N/A')) if 'id' in data else ""
    
    await update.message.reply_text(
        LanguageManager.get('admin.wg_profile.add_prompt_price', current=current),
        reply_markup=get_admin_edit_inline_keyboard("wg_prof_back_days"),
        parse_mode='Markdown'
    )
    return WG_PROFILE_PRICE_USD

async def get_wg_profile_price_usd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if is_conv_cancel(update):
        return await admin_exit_to_menu(update, context)
    if text == '/edit':
        data = context.user_data['new_wg_profile']
        current = LanguageManager.get('admin.wg_profile.current_days', val=data.get('days', 'N/A'))
        await update.message.reply_text(LanguageManager.get('admin.wg_profile.add_prompt_days', current=current), 
                                       reply_markup=get_admin_edit_inline_keyboard("wg_prof_back_volume"), parse_mode='Markdown')
        return WG_PROFILE_DAYS

    try:
        context.user_data['new_wg_profile']['price_usd'] = float(text)
        data = context.user_data['new_wg_profile']
        current = LanguageManager.get('admin.wg_profile.current_price', val=data.get('price_toman', 'N/A'))
        await update.message.reply_text(LanguageManager.get('admin.wg_profile.add_prompt_price_toman', current=current), 
                                       reply_markup=get_admin_edit_inline_keyboard("wg_prof_back_price_usd"), parse_mode='Markdown')
        return WG_PROFILE_PRICE_TOMAN
    except ValueError:
        await update.message.reply_text(LanguageManager.get('admin.profile.error_invalid_price'))
        return WG_PROFILE_PRICE_USD

async def get_wg_profile_price_toman(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if is_conv_cancel(update):
        return await admin_exit_to_menu(update, context)
    if text == '/edit':
        data = context.user_data['new_wg_profile']
        current = LanguageManager.get('admin.wg_profile.current_price_usd', val=data.get('price_usd', 'N/A'))
        await update.message.reply_text(LanguageManager.get('admin.wg_profile.add_prompt_price_usd', current=current), 
                                       reply_markup=get_admin_edit_inline_keyboard("wg_prof_back_days"), parse_mode='Markdown')
        return WG_PROFILE_PRICE_USD

    if not text.isdigit():
        await update.message.reply_text(LanguageManager.get('admin.profile.error_invalid_price'))
        return WG_PROFILE_PRICE_TOMAN
        
    context.user_data['new_wg_profile']['price_toman'] = int(text)
    data = context.user_data['new_wg_profile']
    current = LanguageManager.get('admin.wg_profile.current_rate', val=data.get('rate_limit', 'Unlimited')) if 'id' in data else ""
    
    await update.message.reply_text(
        LanguageManager.get('admin.wg_profile.add_prompt_rate_limit', current=current),
        reply_markup=get_admin_edit_inline_keyboard("wg_prof_back_price_toman"),
        parse_mode='Markdown'
    )
    return WG_PROFILE_RATE_LIMIT

async def get_wg_profile_rate_limit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    data = context.user_data.get('new_wg_profile')
    if not data: return ConversationHandler.END

    if is_conv_cancel(update):
        return await admin_exit_to_menu(update, context)
    if text == '/edit':
        data = context.user_data.get('new_wg_profile')
        current = LanguageManager.get('admin.wg_profile.current_price', val=data.get('price_toman', 'N/A'))
        await update.message.reply_text(LanguageManager.get('admin.wg_profile.add_prompt_price_toman', current=current), 
                                       reply_markup=get_admin_edit_inline_keyboard("wg_prof_back_price_usd"), parse_mode='Markdown')
        return WG_PROFILE_PRICE_TOMAN

    import re
    rate = text
    if rate.lower() == 'unlimited': 
        rate = None
    elif not re.match(r'^(\d+[kMG])?(/(\d+[kMG]))?$', rate, re.IGNORECASE):
        await update.message.reply_text(LanguageManager.get('admin.wg_profile.error_invalid_rate'))
        return WG_PROFILE_RATE_LIMIT
    
    data['rate_limit'] = rate
    
    servers = await get_servers_for_admin_list()
    
    keyboard = [[InlineKeyboardButton(LanguageManager.get('admin.wg_profile.all_servers'), callback_data='wg_prof_srv_all')]]
    for s in servers:
        btn_text = LanguageManager.get('admin.server.btn_item', name=s.name)
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f'wg_prof_srv_{s.id}')])
    
    # Add inline back button for server selection too
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data="wg_prof_back_rate")])
    
    current = LanguageManager.get('admin.wg_profile.current_server', val=data.get('server_id', LanguageManager.get('admin.wg_profile.all_servers'))) if 'id' in data else ""
    await update.message.reply_text(LanguageManager.get('admin.wg_profile.add_prompt_server', current=current), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return WG_PROFILE_SERVER

async def get_wg_profile_server_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    if is_conv_cancel(update):
        return await admin_exit_to_menu(update, context)
    if text == '/edit':
        data = context.user_data.get('new_wg_profile')
        current = LanguageManager.get('admin.wg_profile.current_rate', val=data.get('rate_limit', 'Unlimited'))
        await update.message.reply_text(LanguageManager.get('admin.wg_profile.add_prompt_rate_limit', current=current), 
                                       reply_markup=get_admin_edit_inline_keyboard("wg_prof_back_price"), parse_mode='Markdown')
        return WG_PROFILE_RATE_LIMIT
    return WG_PROFILE_SERVER

async def select_wg_profile_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored select_wg_profile_server using admin_wg_service."""
    query = update.callback_query
    await query.answer()
    server_id = None if query.data == 'wg_prof_srv_all' else int(query.data.split('_')[3])
    data = context.user_data.get('new_wg_profile')
    
    if not data:
         await query.edit_message_text(LanguageManager.get('admin.access_denied'))
         return ConversationHandler.END

    if 'id' in data:
        success = await update_wg_profile(data['id'], {
            'name': data['name'],
            'volume': data['volume'],
            'days': data['days'],
            'price_usd': data.get('price_usd', 0),
            'price_toman': data.get('price_toman', 0),
            'rate_limit': data.get('rate_limit'),
            'server_id': server_id
        })
    else:
        new_prof = await create_wg_profile({
            'name': data['name'],
            'volume': data['volume'],
            'days': data['days'],
            'price_usd': data.get('price_usd', 0),
            'price_toman': data.get('price_toman', 0),
            'rate_limit': data.get('rate_limit'),
            'server_id': server_id
        })
        success = True if new_prof else False
        
    if success:
        await query.edit_message_text(LanguageManager.get('common.success_update' if 'id' in data else 'admin.profile.success', name=data['name'], server="ALL" if server_id is None else "Router"))
    else:
        await query.edit_message_text(LanguageManager.get('common.error'))
        
    context.user_data.pop('new_wg_profile', None)
    return ConversationHandler.END

async def edit_wg_profile_name_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pid = int(query.data.split('_')[4])
    context.user_data['edit_wg_profile_id'] = pid
    await query.answer()
    await query.edit_message_text(LanguageManager.get('admin.profile.prompt_edit_name'))
    return WG_PROFILE_EDIT_NAME

async def edit_wg_profile_name_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored edit_wg_profile_name_finish using admin_wg_service."""
    new_name = update.message.text.strip()
    pid = context.user_data.get('edit_wg_profile_id')
    
    success = await update_wg_profile(pid, {'name': new_name})
    if success:
        await update.message.reply_text(LanguageManager.get('common.success_update'))
    else:
        await update.message.reply_text(LanguageManager.get('common.error'))
        
    return ConversationHandler.END

async def edit_wg_profile_price_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    pid = int(query.data.split('_')[4])
    context.user_data['edit_wg_profile_id'] = pid
    await query.answer()
    await query.edit_message_text(LanguageManager.get('admin.profile.prompt_edit_price'))
    return WG_PROFILE_EDIT_PRICE

async def edit_wg_profile_price_finish(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored edit_wg_profile_price_finish using admin_wg_service."""
    try:
        new_price = int(update.message.text.strip())
        pid = context.user_data.get('edit_wg_profile_id')
        
        success = await update_wg_profile(pid, {'price_toman': new_price})
        if success:
            await update.message.reply_text(LanguageManager.get('common.success_update'))
        else:
            await update.message.reply_text(LanguageManager.get('common.error'))
    except ValueError:
        await update.message.reply_text(LanguageManager.get('admin.profile.error_invalid_price'))
        return WG_PROFILE_EDIT_PRICE
        
    return ConversationHandler.END

async def delete_wg_profile_flow(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored delete_wg_profile_flow using admin_wg_service."""
    query = update.callback_query
    pid = int(query.data.split('_')[3])
    
    success, msg_detail = await delete_wg_profile(pid)
    
    if success:
        if "Archived" in msg_detail:
            await query.answer(LanguageManager.get('admin.profile.archived_success'), show_alert=True)
        else:
            await query.answer(LanguageManager.get('admin.profile.success_delete'), show_alert=True)
        await list_wg_profiles(update, context) # Refresh list
    else:
        await query.answer(LanguageManager.get('common.error'), show_alert=True)
        
    return ConversationHandler.END

admin_wg_profile_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(list_wg_profiles, pattern='^list_wg_profiles$'), 
        CallbackQueryHandler(add_wg_profile_start, pattern='^add_wg_profile$'),
        CallbackQueryHandler(edit_wg_profile_start, pattern='^edit_wg_prof_\\d+$'),
        CallbackQueryHandler(edit_wg_profile_name_start, pattern='^edit_wg_prof_name_'),
        CallbackQueryHandler(edit_wg_profile_price_start, pattern='^edit_wg_prof_price_'),
        CallbackQueryHandler(delete_wg_profile_flow, pattern='^del_wg_profile_'),
        CallbackQueryHandler(wg_mgmt_menu, pattern='^wg_mgmt_menu$'),
    ],
    states=_with_conv_cancel({
        WG_PROFILE_NAME: [
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, get_wg_profile_name),
            CallbackQueryHandler(handle_wg_prof_back, pattern='^wg_prof_back_')
        ],
        WG_PROFILE_VOLUME: [
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, get_wg_profile_volume),
            CallbackQueryHandler(handle_wg_prof_back, pattern='^wg_prof_back_')
        ],
        WG_PROFILE_DAYS: [
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, get_wg_profile_days),
            CallbackQueryHandler(handle_wg_prof_back, pattern='^wg_prof_back_')
        ],
        WG_PROFILE_PRICE_USD: [
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, get_wg_profile_price_usd),
            CallbackQueryHandler(handle_wg_prof_back, pattern='^wg_prof_back_')
        ],
        WG_PROFILE_PRICE_TOMAN: [
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, get_wg_profile_price_toman),
            CallbackQueryHandler(handle_wg_prof_back, pattern='^wg_prof_back_')
        ],
        WG_PROFILE_RATE_LIMIT: [
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, get_wg_profile_rate_limit),
            CallbackQueryHandler(handle_wg_prof_back, pattern='^wg_prof_back_')
        ],
        WG_PROFILE_SERVER: [
            CallbackQueryHandler(select_wg_profile_server, pattern='^wg_prof_srv_'),
            CallbackQueryHandler(handle_wg_prof_back, pattern='^wg_prof_back_'),
            MessageHandler(filters.TEXT & ~MENU_BUTTONS_FILTER, get_wg_profile_server_text)
        ],
        WG_PROFILE_EDIT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_wg_profile_name_finish)],
        WG_PROFILE_EDIT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_wg_profile_price_finish)],
    }),
    fallbacks=admin_conversation_fallbacks(),
)


# --- Speed Editing Handlers ---


async def edit_speed_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    profile_id = int(query.data.split('_')[2])
    await query.answer()
    
    from vpn_bot.admin_profile_service import get_profile_by_id
    profile = await get_profile_by_id(profile_id)
    if not profile: return ConversationHandler.END
    context.user_data['edit_profile_id'] = profile_id
    await query.edit_message_text(LanguageManager.get('admin.profile.edit_speed_prompt', name=profile.name), parse_mode='Markdown')
    return EDIT_PROFILE_SPEED

async def get_new_speed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored get_new_speed using admin_profile_service."""
    new_speed = update.message.text.strip()
    if new_speed.lower() == 'unlimited': new_speed = None
    profile_id = context.user_data.get('edit_profile_id')
    
    success = await update_profile(profile_id, {'rate_limit': new_speed})
    if success:
        await update.message.reply_text(LanguageManager.get('admin.profile.edit_speed_success', speed=new_speed or 'Unlimited'))
    else:
        await update.message.reply_text(LanguageManager.get('common.error'))
        
    return ConversationHandler.END

async def edit_wg_speed_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    profile_id = int(query.data.split('_')[3])
    await query.answer()
    
    profile = await get_wg_profile_by_id(profile_id)
    if not profile: return ConversationHandler.END
    context.user_data['edit_wg_profile_id'] = profile_id
    await query.edit_message_text(LanguageManager.get('admin.profile.edit_speed_prompt', name=profile.name), parse_mode='Markdown')
    return EDIT_WG_PROFILE_SPEED

async def get_new_wg_speed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Refactored get_new_wg_speed using admin_wg_service."""
    new_speed = update.message.text.strip()
    if new_speed.lower() == 'unlimited': new_speed = None
    profile_id = context.user_data.get('edit_wg_profile_id')
    
    success = await update_wg_profile(profile_id, {'rate_limit': new_speed})
    if success:
        await update.message.reply_text(LanguageManager.get('admin.profile.edit_speed_success', speed=new_speed or 'Unlimited'))
    else:
        await update.message.reply_text(LanguageManager.get('common.error'))
        
    return ConversationHandler.END

admin_edit_speed_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(edit_speed_start, pattern='^edit_speed_'),
        CallbackQueryHandler(edit_wg_speed_start, pattern='^edit_wg_speed_')
    ],
    states=_with_conv_cancel({
        EDIT_PROFILE_SPEED: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_new_speed)],
        EDIT_WG_PROFILE_SPEED: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_new_wg_speed)],
    }),
    fallbacks=admin_conversation_fallbacks(),
)

async def add_wg_interface_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start manual interface addition: select server."""
    query = update.callback_query
    await query.answer()
    
    servers = await get_servers_for_admin_list()
    
    if not servers:
        await query.edit_message_text(LanguageManager.get('admin.server.no_servers'), 
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='list_wg_interfaces')]]))
        return ConversationHandler.END
        
    keyboard = []
    for s in servers:
        btn_text = LanguageManager.get('admin.server.btn_host_item', name=s.name)
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"add_wg_if_srv_{s.id}")])
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.back'), callback_data='list_wg_interfaces')])
    
    await query.edit_message_text(LanguageManager.get('admin.wg.prompt_select_server'), reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    return WG_ADD_INT_SERVER

async def get_wg_interface_server(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process server selection and create interface with inheritance."""
    from vpn_bot.admin_wg_service import sync_wg_interfaces_from_router

    query = update.callback_query
    await query.answer()
    server_id = int(query.data.split("_")[4])

    server = await get_server_by_id(server_id)
    if not server:
        await query.edit_message_text(LanguageManager.get("common.error"))
        return ConversationHandler.END

    mgr = get_mikrotik_manager(server)
    router_ifaces = await asyncio.to_thread(mgr.get_wg_interfaces) or []

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WireGuardInterface).where(
                WireGuardInterface.server_id == server_id,
                WireGuardInterface.is_active == True,
            )
        )
        existing_ifaces_db = list(res.scalars().all())

    if router_ifaces and not existing_ifaces_db:
        await sync_wg_interfaces_from_router(server_id)
        async with AsyncSessionLocal() as session:
            res = await session.execute(
                select(WireGuardInterface).where(
                    WireGuardInterface.server_id == server_id,
                    WireGuardInterface.is_active == True,
                )
            )
            existing_ifaces_db = list(res.scalars().all())

    if not router_ifaces and not existing_ifaces_db:
        context.user_data["new_wg_iface_data"] = {"server_id": server_id}
        await query.edit_message_text(
            LanguageManager.get("admin.wg.first_interface_prompt"),
            reply_markup=get_admin_edit_inline_keyboard("list_wg_interfaces"),
            parse_mode="Markdown",
        )
        return WG_ADD_INT_MAN_NAME

    await query.edit_message_text(LanguageManager.get("admin.wg.add_interface_progress"))

    params = await asyncio.to_thread(mgr.find_available_wg_interface_params)
    if not params:
        await query.edit_message_text(
            LanguageManager.get("common.error"),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="list_wg_interfaces")]]
            ),
        )
        return ConversationHandler.END

    keys = await asyncio.to_thread(
        mgr.create_wg_interface, params["name"], params["listen_port"], params["address"]
    )
    if not keys:
        await query.edit_message_text(
            LanguageManager.get("common.error"),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="list_wg_interfaces")]]
            ),
        )
        return ConversationHandler.END

    async with AsyncSessionLocal() as session:
        new_iface = WireGuardInterface(
            server_id=server.id,
            name=params["name"],
            public_key=keys["public_key"],
            private_key=keys.get("private_key", "managed-by-router"),
            address=params["address"],
            listen_port=keys.get("listen_port", params["listen_port"]),
            is_active=True,
        )

        res_other = await session.execute(
            select(WireGuardInterface)
            .where(
                WireGuardInterface.server_id == server.id,
                WireGuardInterface.is_active == True,
            )
            .order_by(WireGuardInterface.id.asc())
            .limit(1)
        )
        other = res_other.scalars().first()
        if other and other.name != new_iface.name:
            new_iface.upstream_interface = other.upstream_interface
            new_iface.routing_mark = other.routing_mark
            new_iface.nat_routing_mark = other.nat_routing_mark
            new_iface.nat_dst_address = other.nat_dst_address
            new_iface.nat_dst_address_list = other.nat_dst_address_list
            new_iface.nat_dst_negate = other.nat_dst_negate
            new_iface.gateway = other.gateway
            new_iface.route_table = other.route_table
            new_iface.route_dst_address = other.route_dst_address
            new_iface.route_distance = other.route_distance
            new_iface.dns = other.dns
            new_iface.mtu = other.mtu
            new_iface.keepalive = other.keepalive
            new_iface.max_users = other.max_users

        session.add(new_iface)
        await session.flush()

        if not _wg_has_firewall_template(other):
            await session.commit()
            context.user_data["new_wg_iface_data"] = {
                "server_id": server.id,
                "interface_id": new_iface.id,
                "name": new_iface.name,
                "post_create_firewall": True,
            }
            await query.edit_message_text(
                LanguageManager.get(
                    "admin.wg.prompt_firewall_after_create", name=new_iface.name
                ),
                parse_mode="Markdown",
            )
            return await wg_add_show_upstream_prompt(update, context)

        await session.commit()
        iface_id = new_iface.id

    ok, mt_err, _route_applied = await apply_wg_automation_timed(iface_id)
    if not ok:
        await query.edit_message_text(
            LanguageManager.get("admin.wg.add_interface_success", name=params["name"])
            + "\n\n⚠️ "
            + (mt_err or LanguageManager.get("admin.wg.mt_apply_failed")),
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="list_wg_interfaces")]]
            ),
            parse_mode="Markdown",
        )
        return ConversationHandler.END

    context.user_data["wg_list_notice"] = LanguageManager.get(
        "admin.wg.add_interface_success", name=params["name"]
    )
    await _render_wg_interfaces_list(
        update, context, page=0, answer_callback=False
    )
    context.user_data.pop("wg_list_notice", None)
    return ConversationHandler.END

# --- Manual WG Interface Addition Flow ---


def _wg_has_firewall_template(iface) -> bool:
    """True when parent iface already has NAT/Mangle template (upstream or routing mark)."""
    if not iface:
        return False
    return bool(iface.upstream_interface or iface.routing_mark)


async def wg_add_show_upstream_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """NAT out-interface (Upstream) step — shared by manual add and post-create wizard."""
    data = context.user_data.get("new_wg_iface_data") or {}
    server = await get_server_by_id(data["server_id"])
    interfaces, err = await fetch_upstream_interfaces_timed(server)
    if err or not interfaces:
        msg = err or LanguageManager.get("admin.wg.mt_upstream_empty")
        if update.callback_query:
            await update.callback_query.edit_message_text(msg, parse_mode="Markdown")
        elif update.message:
            await update.message.reply_text(msg)
        return ConversationHandler.END

    keyboard = []
    for iface in interfaces:
        status = "🟢" if iface.get("running") else "⚪️"
        keyboard.append(
            [InlineKeyboardButton(f"{status} {iface['name']}", callback_data=f"man_wg_up_{iface['name']}")]
        )
    keyboard.append(
        [InlineKeyboardButton(LanguageManager.get("common.btn_skip"), callback_data="man_wg_up_none")]
    )
    keyboard.append(
        [InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="list_wg_interfaces")]
    )

    text = LanguageManager.get("admin.wg.prompt_upstream_select")
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    elif update.message:
        await update.message.reply_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown"
        )
    return WG_ADD_INT_MAN_UPSTREAM

async def get_wg_add_man_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name: return WG_ADD_INT_MAN_NAME
    context.user_data['new_wg_iface_data']['name'] = name
    await update.message.reply_text(LanguageManager.get('admin.wg.prompt_listen_port'), parse_mode='Markdown')
    return WG_ADD_INT_MAN_PORT

async def get_wg_add_man_port(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    try:
        port = int(text)
    except:
        await update.message.reply_text(LanguageManager.get('admin.wg.error_invalid_port'))
        return WG_ADD_INT_MAN_PORT
    context.user_data['new_wg_iface_data']['port'] = port
    await update.message.reply_text(LanguageManager.get('admin.wg.prompt_interface_address_manual'), parse_mode='Markdown')
    return WG_ADD_INT_MAN_ADDR

async def get_wg_add_man_addr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    addr = update.message.text.strip()
    if '/' not in addr:
        await update.message.reply_text(LanguageManager.get('admin.wg.error_invalid_address'))
        return WG_ADD_INT_MAN_ADDR
    context.user_data['new_wg_iface_data']['address'] = addr
    await update.message.reply_text(LanguageManager.get('admin.wg.prompt_dns_manual'), parse_mode='Markdown')
    return WG_ADD_INT_MAN_DNS

async def get_wg_add_man_dns(update: Update, context: ContextTypes.DEFAULT_TYPE):
    dns = update.message.text.strip()
    context.user_data['new_wg_iface_data']['dns'] = dns
    await update.message.reply_text(LanguageManager.get('admin.wg.prompt_endpoint_manual'), parse_mode='Markdown')
    return WG_ADD_INT_MAN_EP

async def get_wg_add_man_ep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ep = update.message.text.strip()
    context.user_data['new_wg_iface_data']['endpoint'] = ep
    await update.message.reply_text(LanguageManager.get('admin.wg.prompt_mtu_manual'), parse_mode='Markdown')
    return WG_ADD_INT_MAN_MTU

async def get_wg_add_man_mtu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        mtu = int(update.message.text.strip())
    except:
        mtu = 1420
    context.user_data['new_wg_iface_data']['mtu'] = mtu
    await update.message.reply_text(LanguageManager.get('admin.wg.prompt_keepalive_manual'), parse_mode='Markdown')
    return WG_ADD_INT_MAN_KA

async def get_wg_add_man_ka(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        ka = int(update.message.text.strip())
    except:
        ka = 25
    context.user_data['new_wg_iface_data']['keepalive'] = ka
    return await wg_add_show_upstream_prompt(update, context)

async def get_wg_add_man_upstream(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    val = query.data.replace("man_wg_up_", "")
    context.user_data['new_wg_iface_data']['upstream'] = None if val == "none" else val
    
    server = await get_server_by_id(context.user_data['new_wg_iface_data']['server_id'])
    await query.edit_message_text(LanguageManager.get("admin.wg.mt_loading"), parse_mode="Markdown")
    marks, err = await fetch_routing_tables_timed(server)
    if err:
        await query.edit_message_text(err, parse_mode="Markdown")
        return WG_ADD_INT_MAN_UPSTREAM
    marks = marks or []
    if 'main' not in marks:
        marks.insert(0, 'main')

    keyboard = []
    
    for m in marks:
        btn_text = LanguageManager.get('wg.btn_routing_mark', mark=m)
        keyboard.append([InlineKeyboardButton(btn_text, callback_data=f"man_wg_rm_{m}")])
    
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.btn_skip'), callback_data="man_wg_rm_none")])
    
    await query.edit_message_text(
        LanguageManager.get('admin.wg.prompt_routing_mark_select'),
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )
    return WG_ADD_INT_MAN_RM

async def get_wg_add_man_rm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        val = query.data.replace("man_wg_rm_", "")
        rm = None if val == "none" else val
    else:
        # Fallback for manual text input if someone types it
        rm = update.message.text.strip()
        rm = rm if rm else None

    context.user_data['new_wg_iface_data']['routing_mark'] = rm

    keyboard = []
    if rm:
        keyboard.append(
            [
                InlineKeyboardButton(
                    LanguageManager.get('admin.wg.btn_nat_rm_same_mangle', mark=rm),
                    callback_data='man_wg_natrm_same',
                )
            ]
        )
    keyboard.append(
        [InlineKeyboardButton(LanguageManager.get('admin.wg.btn_nat_rm_none'), callback_data='man_wg_natrm_none')]
    )
    server = await get_server_by_id(context.user_data['new_wg_iface_data']['server_id'])
    marks, err = await fetch_routing_tables_timed(server)
    if err:
        msg_text = err
        if query:
            await query.edit_message_text(msg_text, parse_mode='Markdown')
        else:
            await update.message.reply_text(msg_text)
        return WG_ADD_INT_MAN_RM
    marks = marks or []
    if 'main' not in marks:
        marks.insert(0, 'main')
    for m in marks:
        keyboard.append(
            [InlineKeyboardButton(LanguageManager.get('wg.btn_routing_mark', mark=m), callback_data=f'man_wg_natrm_{m}')]
        )
    msg_text = LanguageManager.get('admin.wg.prompt_nat_routing_mark_select')

    if query:
        await query.edit_message_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
    else:
        await update.message.reply_text(msg_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

    return WG_ADD_INT_MAN_NATRM


async def get_wg_add_man_natrm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query:
        await query.answer()
        val = query.data.replace('man_wg_natrm_', '')
        data = context.user_data['new_wg_iface_data']
        if val == 'same':
            nat_rm = data.get('routing_mark')
        elif val == 'none':
            nat_rm = None
        else:
            nat_rm = val
    else:
        text = update.message.text.strip()
        nat_rm = None if not text or text.lower() == 'none' else text

    context.user_data['new_wg_iface_data']['nat_routing_mark'] = nat_rm

    data = context.user_data['new_wg_iface_data']
    server = await get_server_by_id(data['server_id'])
    list_names, err = await fetch_address_list_names_timed(server)
    if err:
        msg_text = err
        if query:
            await query.edit_message_text(msg_text, parse_mode='Markdown')
        else:
            await update.message.reply_text(msg_text)
        return WG_ADD_INT_MAN_NATRM

    choices = _wg_build_nat_dst_choices(list_names or [])
    context.user_data['wg_nat_dst_choices'] = choices
    markup = _wg_nat_dst_choice_keyboard(
        choices,
        "127.0.0.1",
        None,
        callback_prefix="man_wg_natdst",
        back_callback="list_wg_interfaces",
    )
    msg_text = LanguageManager.get('admin.wg.prompt_nat_dst_select')

    if query:
        await query.edit_message_text(msg_text, reply_markup=markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(msg_text, reply_markup=markup, parse_mode='Markdown')

    return WG_ADD_INT_MAN_NAT


async def get_wg_add_man_nat(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = context.user_data['new_wg_iface_data']
    choice = None

    if query:
        await query.answer()
        raw = query.data or ""
        if raw == "man_wg_natdst_enter":
            await query.edit_message_text(
                LanguageManager.get("admin.wg.prompt_nat_dst_custom"),
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="list_wg_interfaces")]]
                ),
                parse_mode="Markdown",
            )
            return WG_ADD_INT_MAN_NAT
        if raw.startswith("man_wg_natdst_c_"):
            idx = int(raw.rsplit("_", 1)[-1])
            choices = context.user_data.get("wg_nat_dst_choices") or []
            if 0 <= idx < len(choices):
                choice = choices[idx]
        elif raw == "man_wg_nat_none":
            choice = {"kind": "ip", "val": "127.0.0.1"}
    else:
        list_names = [
            c["val"] for c in (context.user_data.get("wg_nat_dst_choices") or []) if c["kind"] == "list"
        ]
        choice = _wg_parse_nat_dst_text(update.message.text, list_names)

    if not choice:
        if update.message:
            await update.message.reply_text(LanguageManager.get("admin.wg.nat_dst_invalid"))
        return WG_ADD_INT_MAN_NAT

    context.user_data["wg_pending_nat_dst"] = choice
    raw_text = (update.message.text or "").strip() if update.message else ""
    if raw_text.startswith("!") or raw_text.lower().startswith("list:!") or raw_text.lower().startswith("@!"):
        data["nat_dst_negate"] = choice.get("negate", True)
        if choice["kind"] == "list":
            data["nat_dst"] = "127.0.0.1"
            data["nat_dst_address_list"] = choice["val"]
        else:
            data["nat_dst"] = choice["val"]
            data["nat_dst_address_list"] = None
        context.user_data.pop("wg_pending_nat_dst", None)
        return await _wg_show_route_table_step(update, context, is_add=True)

    return await _wg_show_nat_dst_negate_prompt(
        update,
        context,
        choice,
        callback_prefix="man_wg_natdst",
        back_callback="list_wg_interfaces",
        edit_state=WG_ADD_INT_MAN_NAT_NEGATE,
    )


async def get_wg_add_man_nat_negate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = context.user_data.pop("wg_pending_nat_dst", None)
    data = context.user_data.get("new_wg_iface_data") or {}
    if not choice:
        return WG_ADD_INT_MAN_NAT

    data["nat_dst_negate"] = query.data.endswith("_neg_1")
    if choice["kind"] == "list":
        data["nat_dst"] = "127.0.0.1"
        data["nat_dst_address_list"] = choice["val"]
    else:
        data["nat_dst"] = choice["val"]
        data["nat_dst_address_list"] = None

    return await _wg_show_route_table_step(update, context, is_add=True)

def _wg_clear_add_wizard_data(context: ContextTypes.DEFAULT_TYPE) -> None:
    for key in ('new_wg_iface_data', 'wg_nat_dst_choices', 'wg_route_dst_choices'):
        context.user_data.pop(key, None)


async def _wg_finish_add_to_interface_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    notice: str,
) -> int:
    """Show notice on the WG interface list and end the add conversation."""
    _wg_clear_add_wizard_data(context)
    context.user_data['wg_list_notice'] = notice
    await _render_wg_interfaces_list(
        update, context, page=0, answer_callback=False
    )
    context.user_data.pop('wg_list_notice', None)
    return ConversationHandler.END


async def _wg_add_man_complete_create(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Finish manual WG add after Route List wizard."""
    query = update.callback_query
    if query:
        await query.edit_message_text(
            LanguageManager.get('admin.wg.creating_interface'), parse_mode='Markdown'
        )
    elif update.message:
        await update.message.reply_text(
            LanguageManager.get('admin.wg.creating_interface'), parse_mode='Markdown'
        )

    data = context.user_data['new_wg_iface_data']
    chat_id = update.effective_chat.id
    iface_name = data.get('name', '')

    if data.get('post_create_firewall'):
        iface_id = data['interface_id']
        payload = {
            'upstream_interface': data.get('upstream'),
            'routing_mark': data.get('routing_mark'),
            'nat_routing_mark': data.get('nat_routing_mark'),
            'nat_dst_address': data.get('nat_dst', '127.0.0.1'),
            'nat_dst_address_list': data.get('nat_dst_address_list'),
            'nat_dst_negate': data.get('nat_dst_negate', True),
            'gateway': data.get('gateway'),
            'route_table': data.get('route_table'),
            'route_dst_address': data.get('route_dst_address', '0.0.0.0/0'),
            'route_distance': data.get('route_distance', 1),
        }
        success, error = await update_wg_interface(iface_id, payload)
        if not success:
            await context.bot.send_message(chat_id, f"❌ {error or LanguageManager.get('common.error')}")
            return ConversationHandler.END
        ok, mt_err, _route_applied = await apply_wg_automation_timed(iface_id)
        if not ok:
            await context.bot.send_message(chat_id, mt_err or LanguageManager.get("admin.wg.mt_apply_failed"))
            return ConversationHandler.END
        return await _wg_finish_add_to_interface_list(
            update,
            context,
            notice=LanguageManager.get('admin.wg.success_firewall_configured', name=iface_name),
        )

    server = await get_server_by_id(data['server_id'])
    mgr = get_mikrotik_manager(server)

    keys = await asyncio.to_thread(mgr.create_wg_interface, data['name'], data['port'], data['address'])
    if not keys:
        await context.bot.send_message(chat_id, LanguageManager.get('admin.wg.error_create_interface'))
        return ConversationHandler.END

    iface_data = {
        'server_id': server.id,
        'name': data['name'],
        'public_key': keys['public_key'],
        'private_key': keys.get('private_key', 'managed-by-router'),
        'address': data['address'],
        'listen_port': data['port'],
        'dns': data['dns'],
        'endpoint_host': data['endpoint'],
        'mtu': data['mtu'],
        'keepalive': data['keepalive'],
        'upstream_interface': data.get('upstream'),
        'routing_mark': data.get('routing_mark'),
        'nat_routing_mark': data.get('nat_routing_mark'),
        'nat_dst_address': data.get('nat_dst'),
        'nat_dst_address_list': data.get('nat_dst_address_list'),
        'nat_dst_negate': data.get('nat_dst_negate', True),
        'gateway': data.get('gateway'),
        'route_table': data.get('route_table'),
        'route_dst_address': data.get('route_dst_address', '0.0.0.0/0'),
        'route_distance': data.get('route_distance', 1),
    }
    new_iface = await create_wg_interface(iface_data)
    ok, mt_err, _route_applied = await apply_wg_automation_timed(new_iface.id)
    if not ok:
        await context.bot.send_message(
            chat_id,
            LanguageManager.get('admin.wg.success_create_interface', name=iface_name)
            + "\n\n⚠️ "
            + (mt_err or LanguageManager.get("admin.wg.mt_apply_failed")),
        )
        return ConversationHandler.END

    return await _wg_finish_add_to_interface_list(
        update,
        context,
        notice=LanguageManager.get('admin.wg.success_create_interface', name=iface_name),
    )

WG_IFACES_PER_PAGE = 10


async def _render_wg_interfaces_list(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    page: int = 0,
    answer_callback: bool = True,
    skip_router_sync: bool = False,
) -> None:
    from vpn_bot.admin_wg_service import get_all_wg_interfaces, sync_wg_interfaces_from_router

    if update.callback_query and answer_callback:
        try:
            await update.callback_query.answer()
        except Exception:
            pass

    interfaces = await get_all_wg_interfaces()
    sync_note = ""
    if not interfaces and not skip_router_sync:
        for srv in await get_servers_for_admin_list():
            n = await sync_wg_interfaces_from_router(srv.id)
            if n:
                sync_note = LanguageManager.get("admin.wg.sync_imported", count=n)
        interfaces = await get_all_wg_interfaces()

    keyboard: list[list[InlineKeyboardButton]] = []

    notice = (context.user_data.get("wg_list_notice") or "").strip()
    notice_block = f"{notice}\n\n" if notice else ""

    if not interfaces:
        text = notice_block + sync_note + (
            LanguageManager.get("admin.wg.interfaces_title")
            + LanguageManager.get("admin.wg.no_interfaces")
        )
    else:
        per_page = WG_IFACES_PER_PAGE
        total = len(interfaces)
        total_pages = max(1, (total + per_page - 1) // per_page)
        page = max(0, min(page, total_pages - 1))
        context.user_data["wg_interfaces_page"] = page
        chunk = interfaces[page * per_page : (page + 1) * per_page]

        text = notice_block + sync_note + LanguageManager.get(
            "admin.wg.interfaces_list",
            count=total,
            page=page + 1,
            pages=total_pages,
        )

        for iface in chunk:
            label = LanguageManager.get(
                "admin.wg.btn_iface_item",
                name=iface.name[:28],
                current=iface.current_users,
                max=iface.max_users,
                port=iface.listen_port,
            )
            keyboard.append(
                [
                    InlineKeyboardButton(
                        label,
                        callback_data=f"edit_wg_interface_{iface.id}",
                    )
                ]
            )

        if total_pages > 1:
            nav: list[InlineKeyboardButton] = []
            if page > 0:
                nav.append(
                    InlineKeyboardButton(
                        LanguageManager.get("admin.wg.ifaces_btn_prev"),
                        callback_data=f"wg_ifaces_page_{page - 1}",
                    )
                )
            nav.append(
                InlineKeyboardButton(
                    LanguageManager.get(
                        "admin.wg.ifaces_page_indicator",
                        current=page + 1,
                        total=total_pages,
                    ),
                    callback_data="wg_ifaces_noop",
                )
            )
            if page < total_pages - 1:
                nav.append(
                    InlineKeyboardButton(
                        LanguageManager.get("admin.wg.ifaces_btn_next"),
                        callback_data=f"wg_ifaces_page_{page + 1}",
                    )
                )
            keyboard.append(nav)

    keyboard.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("admin.wg.btn_add_interface"),
                callback_data="add_wg_interface_start",
            )
        ]
    )
    keyboard.append(
        [
            InlineKeyboardButton(
                LanguageManager.get("common.back"),
                callback_data="wg_mgmt_menu",
            )
        ]
    )

    markup = InlineKeyboardMarkup(keyboard)
    try:
        await universal_reply(update, text, reply_markup=markup, parse_mode="Markdown")
    except Exception as exc:
        if "too long" not in str(exc).lower():
            raise
        short = LanguageManager.get(
            "admin.wg.interfaces_list",
            count=len(interfaces),
            page=1,
            pages=1,
        )
        await universal_reply(update, short, reply_markup=markup, parse_mode="Markdown")


async def list_wg_interfaces_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Back to WG list from inside ConversationHandler (no safe_loading overlay)."""
    query = update.callback_query
    if query:
        await query.answer()
    clear_user_processing(context)
    await _render_wg_interfaces_list(update, context, page=0)
    return ConversationHandler.END


@safe_response
async def list_wg_interfaces(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List WireGuard interfaces (paginated, compact buttons)."""
    if context.user_data.get("edit_wg_interface_id") or context.user_data.get("new_wg_iface_data"):
        return await list_wg_interfaces_nav(update, context)
    await _render_wg_interfaces_list(update, context, page=0)
    return ConversationHandler.END


@safe_response
async def wg_interfaces_page_nav(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Paginate WireGuard interface list."""
    query = update.callback_query
    if query.data == "wg_ifaces_noop":
        await query.answer()
        return ConversationHandler.END
    page = int(query.data.rsplit("_", 1)[-1])
    await _render_wg_interfaces_list(update, context, page=page)
    return ConversationHandler.END

async def list_wg_users(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List WireGuard users (paginated search)."""
    query = update.callback_query
    await query.answer()
    
    # For now, just show a sumary and provide search
    total = await get_wg_subscription_count()
        
    text = LanguageManager.get('admin.wg.users_title', total=total)
    
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get("admin.btn_user_mgmt"), callback_data="search_user")],
        [InlineKeyboardButton(LanguageManager.get("common.back"), callback_data="wg_mgmt_menu")],
    ]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

admin_wg_mgmt_handler = ConversationHandler(
    entry_points=[
        CallbackQueryHandler(wg_interface_settings, pattern='^edit_wg_interface_'),
        CallbackQueryHandler(add_wg_interface_start, pattern='^add_wg_interface_start$'),
        CallbackQueryHandler(search_wg_start, pattern='^search_wg_start$'),
        CallbackQueryHandler(list_wg_users, pattern='^list_wg_users$'),
        CallbackQueryHandler(list_wg_profiles, pattern='^list_wg_profiles$')
    ],
    states=_with_conv_cancel(_with_wg_states({
        SEARCH_WG_INPUT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, search_wg_receive)],
        WG_INT_SETTINGS: [
            CallbackQueryHandler(wg_edit_section_firewall, pattern='^wg_edit_sec_firewall$'),
            CallbackQueryHandler(wg_edit_section_route, pattern='^wg_edit_sec_route$'),
            CallbackQueryHandler(wg_edit_section_client, pattern='^wg_edit_sec_client$'),
            CallbackQueryHandler(wg_edit_section_iface, pattern='^wg_edit_sec_iface$'),
            CallbackQueryHandler(wg_edit_section_admin, pattern='^wg_edit_sec_admin$'),
            CallbackQueryHandler(wg_reapply_firewall, pattern='^wg_reapply_firewall$'),
            CallbackQueryHandler(set_wg_address_start, pattern='^set_wg_address'),
            CallbackQueryHandler(set_wg_upstream_start, pattern='^set_wg_upstream_start'),
            CallbackQueryHandler(set_wg_rm_start, pattern='^set_wg_rm_start'),
            CallbackQueryHandler(set_wg_nat_rm_start, pattern='^set_wg_natrm_start'),
            CallbackQueryHandler(set_wg_natdst_start, pattern='^set_wg_natdst_start'),
            CallbackQueryHandler(set_wg_route_list_start, pattern='^set_wg_route_list_start$'),
            CallbackQueryHandler(set_wg_route_step_table, pattern='^set_wg_route_step_table$'),
            CallbackQueryHandler(set_wg_route_step_dst, pattern='^set_wg_route_step_dst$'),
            CallbackQueryHandler(set_wg_route_step_gw, pattern='^set_wg_route_step_gw$'),
            CallbackQueryHandler(set_wg_route_step_dist, pattern='^set_wg_route_step_dist$'),
            CallbackQueryHandler(set_wg_max_users_start, pattern='^set_wg_max_users'),
            CallbackQueryHandler(set_wg_dns_start, pattern='^set_wg_dns'),
            CallbackQueryHandler(set_wg_endpoint_start, pattern='^set_wg_endpoint'),
            CallbackQueryHandler(set_wg_port_start, pattern='^set_wg_port'),
            CallbackQueryHandler(set_wg_mtu_start, pattern='^set_wg_mtu'),
            CallbackQueryHandler(set_wg_keepalive_start, pattern='^set_wg_keepalive'),
            CallbackQueryHandler(edit_wg_notif_template_start, pattern='^set_wg_notif_tpl'),
            CallbackQueryHandler(ask_to_notify_users, pattern='^manual_wg_notify$'),
            CallbackQueryHandler(wg_migrate_start, pattern='^wg_migrate_start$'),
            CallbackQueryHandler(wg_delete_start, pattern='^wg_delete_start$'),
            CallbackQueryHandler(wg_interface_settings, pattern='^edit_wg_interface_'), # Allow switching interfaces
            CallbackQueryHandler(list_wg_interfaces_nav, pattern='^list_wg_interfaces$')
        ],
        WG_INT_DNS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_dns),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_INT_ENDPOINT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_endpoint),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_INT_PORT: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_port),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_INT_MTU: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_mtu),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_INT_KEEPALIVE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_keepalive),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_INT_ADDRESS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_address),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_INT_UPSTREAM: [
            CallbackQueryHandler(set_wg_upstream_callback, pattern='^set_wg_up_'),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_INT_ROUTING_MARK: [
            CallbackQueryHandler(get_wg_rm, pattern='^set_wg_rm_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_rm),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_INT_NAT_ROUTING_MARK: [
            CallbackQueryHandler(get_wg_nat_rm, pattern='^set_wg_natrm_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_nat_rm),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_INT_NAT_DST: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_natdst),
            CallbackQueryHandler(get_wg_natdst, pattern='^set_wg_natdst_'),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_INT_NAT_DST_NEGATE: [
            CallbackQueryHandler(get_wg_natdst_negate, pattern='^set_wg_natdst_neg_[01]$'),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$'),
        ],
        WG_INT_ROUTE_TABLE: [
            CallbackQueryHandler(get_wg_route_table, pattern='^set_wg_rt_'),
            CallbackQueryHandler(back_to_wg_section, pattern='^back_to_wg_section$'),
        ],
        WG_INT_ROUTE_DST: [
            CallbackQueryHandler(get_wg_route_dst, pattern='^set_wg_rdst_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_route_dst),
            CallbackQueryHandler(back_to_wg_section, pattern='^back_to_wg_section$'),
        ],
        WG_INT_ROUTE_GW: [
            CallbackQueryHandler(get_wg_route_gw, pattern='^set_wg_rgw_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_route_gw),
            CallbackQueryHandler(back_to_wg_section, pattern='^back_to_wg_section$'),
        ],
        WG_INT_ROUTE_DIST: [
            CallbackQueryHandler(get_wg_route_dist, pattern='^set_wg_rdist_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_route_dist),
            CallbackQueryHandler(back_to_wg_section, pattern='^back_to_wg_section$'),
        ],
        WG_INT_MAX_USERS: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_max_users),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_INT_NOTIFY_ASK: [
            CallbackQueryHandler(process_wg_notification, pattern='^wg_notify_'),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_NOTIFICATION_TEMPLATE: [
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_notif_template),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_MIGRATE_TARGET: [
            CallbackQueryHandler(wg_migrate_select_target, pattern='^wg_migrate_'),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_MIGRATE_CONFIRM: [
            CallbackQueryHandler(wg_migrate_confirm_exec, pattern='^wg_migrate_exec$'),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_DELETE_CONFIRM: [
            CallbackQueryHandler(wg_delete_confirm_exec, pattern='^wg_delete_exec$'),
            CallbackQueryHandler(back_to_int_settings, pattern='^back_to_int_settings$')
        ],
        WG_ADD_INT_SERVER: [
            CallbackQueryHandler(get_wg_interface_server, pattern='^add_wg_if_srv_'),
            CallbackQueryHandler(list_wg_interfaces_nav, pattern='^list_wg_interfaces$')
        ],
        WG_ADD_INT_MAN_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_add_man_name)],
        WG_ADD_INT_MAN_PORT: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_add_man_port)],
        WG_ADD_INT_MAN_ADDR: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_add_man_addr)],
        WG_ADD_INT_MAN_DNS: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_add_man_dns)],
        WG_ADD_INT_MAN_EP: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_add_man_ep)],
        WG_ADD_INT_MAN_MTU: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_add_man_mtu)],
        WG_ADD_INT_MAN_KA: [MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_add_man_ka)],
        WG_ADD_INT_MAN_UPSTREAM: [CallbackQueryHandler(get_wg_add_man_upstream, pattern='^man_wg_up_')],
        WG_ADD_INT_MAN_RM: [
            CallbackQueryHandler(get_wg_add_man_rm, pattern='^man_wg_rm_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_add_man_rm)
        ],
        WG_ADD_INT_MAN_NATRM: [
            CallbackQueryHandler(get_wg_add_man_natrm, pattern='^man_wg_natrm_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_add_man_natrm)
        ],
        WG_ADD_INT_MAN_NAT: [
            CallbackQueryHandler(get_wg_add_man_nat, pattern='^man_wg_natdst_'),
            CallbackQueryHandler(get_wg_add_man_nat, pattern='^man_wg_nat_none$'),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_add_man_nat)
        ],
        WG_ADD_INT_MAN_NAT_NEGATE: [
            CallbackQueryHandler(get_wg_add_man_nat_negate, pattern='^man_wg_natdst_neg_[01]$'),
            CallbackQueryHandler(list_wg_interfaces_nav, pattern='^list_wg_interfaces$'),
        ],
        WG_ADD_INT_MAN_ROUTE_TABLE: [
            CallbackQueryHandler(get_wg_route_table, pattern='^man_wg_rt_'),
        ],
        WG_ADD_INT_MAN_ROUTE_DST: [
            CallbackQueryHandler(get_wg_route_dst, pattern='^man_wg_rdst_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_route_dst),
        ],
        WG_ADD_INT_MAN_ROUTE_GW: [
            CallbackQueryHandler(get_wg_route_gw, pattern='^man_wg_rgw_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_route_gw),
        ],
        WG_ADD_INT_MAN_ROUTE_DIST: [
            CallbackQueryHandler(get_wg_route_dist, pattern='^man_wg_rdist_'),
            MessageHandler(filters.TEXT & ~filters.COMMAND & ~MENU_BUTTONS_FILTER, get_wg_route_dist),
        ],
    })),
    fallbacks=admin_conversation_fallbacks(),
    per_chat=True,
    per_user=True,
    # per_message=True breaks MessageHandler steps (gateway text, DNS, etc.)
)
