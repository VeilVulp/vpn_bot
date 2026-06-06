"""Shared WireGuard config build and Telegram delivery (user purchase, resend, admin)."""

from io import BytesIO

from sqlalchemy import select
from sqlalchemy.orm import joinedload
from telegram import Bot, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.helpers import escape_markdown

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import Server, WireGuardSubscription
from vpn_bot.utils import (
    LanguageManager,
    ensure_telegram_text,
    format_datetime,
    generate_wg_conf,
    generate_wg_qr,
    generate_wg_url,
    logger,
)


def _interface_wg_params(interface) -> dict:
    return {
        "dns": interface.dns or "1.1.1.1",
        "mtu": interface.mtu or 1420,
        "keepalive": interface.keepalive or 25,
    }


def build_wg_delivery_payload(wg_sub, server, volume_gb=None):
    """
    Build .conf text, QR/file buffers, and wg:// import URL for a subscription.
    Returns None if interface/server data is incomplete.
    """
    interface = wg_sub.interface
    if not interface or not interface.public_key or not server:
        return None

    if not wg_sub.peer_private_key or not wg_sub.assigned_ip:
        return None

    endpoint_host = interface.endpoint_host or server.host
    full_endpoint = f"{endpoint_host}:{interface.listen_port}"
    params = _interface_wg_params(interface)
    address = f"{wg_sub.assigned_ip}/24"

    config_text = generate_wg_conf(
        wg_sub.peer_private_key,
        address,
        interface.public_key,
        full_endpoint,
        **params,
    )
    wg_url = generate_wg_url(
        wg_sub.peer_private_key,
        address,
        interface.public_key,
        full_endpoint,
        name=wg_sub.unique_identifier,
        **params,
    )

    qr_bio = generate_wg_qr(config_text)
    qr_bio.seek(0)
    qr_bio.name = f"{wg_sub.unique_identifier}.png"

    file_bio = BytesIO(config_text.encode())
    file_bio.name = f"{wg_sub.unique_identifier}.conf"

    if volume_gb is None:
        volume_gb = wg_sub.profile.volume_gb if getattr(wg_sub, "profile", None) else 0

    return {
        "config_text": config_text,
        "qr_bio": qr_bio,
        "file_bio": file_bio,
        "wg_url": wg_url,
        "unique_id": wg_sub.unique_identifier,
        "volume_gb": volume_gb or 0,
        "filename": file_bio.name,
        "uid": wg_sub.unique_identifier,
        "qr_bytes": qr_bio,
    }


def _wg_config_caption(unique_id: str, formatted_expiry: str, volume_gb, wg_url: str) -> str:
    """Build caption with escaped dynamic fields (safe for Telegram Markdown)."""
    return LanguageManager.get(
        "subs.wg_config_caption",
        unique_id=escape_markdown(str(unique_id), version=1),
        expiry=escape_markdown(str(formatted_expiry), version=1),
        volume=volume_gb,
        link=escape_markdown(str(wg_url), version=1),
    )


async def deliver_wg_config(
    bot: Bot,
    chat_id: int,
    wg_sub,
    server,
    *,
    volume_gb=None,
    send_purchase_success: bool = False,
    show_main_menu: bool = False,
) -> bool:
    """
    Deliver WG config like a new purchase: optional success text, QR photo, .conf with caption.
    """
    payload = build_wg_delivery_payload(wg_sub, server, volume_gb=volume_gb)
    if not payload:
        logger.error(
            "WG delivery: cannot build payload (sub=%s, iface=%s, server=%s)",
            getattr(wg_sub, "id", None),
            getattr(getattr(wg_sub, "interface", None), "id", None),
            getattr(server, "id", None),
        )
        return False

    formatted_expiry = await format_datetime(wg_sub.expiry_date, include_time=True)

    try:
        if send_purchase_success:
            success_msg = ensure_telegram_text(
                LanguageManager.get(
                    "buy.wg_success",
                    unique_id=payload["unique_id"],
                    expiry=formatted_expiry,
                ),
                fallback_key="buy.wg_success",
                unique_id=payload["unique_id"],
                expiry=formatted_expiry,
            )
            await bot.send_message(chat_id, success_msg, parse_mode="Markdown")

        payload["qr_bio"].seek(0)
        await bot.send_photo(chat_id, photo=payload["qr_bio"])

        caption = ensure_telegram_text(
            _wg_config_caption(
                payload["unique_id"],
                formatted_expiry,
                payload["volume_gb"],
                payload["wg_url"],
            ),
            fallback_key="subs.wg_config_caption",
            unique_id=payload["unique_id"],
            expiry=formatted_expiry,
            volume=payload["volume_gb"],
            link=payload["wg_url"],
        )

        reply_markup = None
        if show_main_menu:
            reply_markup = InlineKeyboardMarkup(
                [[InlineKeyboardButton(LanguageManager.get("common.main_menu"), callback_data="main_menu")]]
            )

        payload["file_bio"].seek(0)
        await bot.send_document(
            chat_id,
            document=payload["file_bio"],
            filename=payload["filename"],
            caption=caption,
            reply_markup=reply_markup,
            parse_mode="Markdown",
        )
        return True
    except Exception as exc:
        logger.error("WG delivery failed for chat %s sub %s: %s", chat_id, getattr(wg_sub, "id", None), exc)
        return False


async def deliver_wg_subscription_by_id(
    bot: Bot,
    chat_id: int,
    wg_sub_id: int,
    **kwargs,
) -> bool:
    """Load subscription (with interface + profile) and deliver config."""
    async with AsyncSessionLocal() as session:
        wg_sub = (
            await session.execute(
                select(WireGuardSubscription)
                .options(
                    joinedload(WireGuardSubscription.interface),
                    joinedload(WireGuardSubscription.profile),
                )
                .where(WireGuardSubscription.id == wg_sub_id)
            )
        ).scalars().first()

        if not wg_sub or not wg_sub.interface:
            return False

        server = await session.get(Server, wg_sub.interface.server_id)
        if not server:
            return False

        return await deliver_wg_config(bot, chat_id, wg_sub, server, **kwargs)
