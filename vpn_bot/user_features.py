"""
Additional User Panel Features:
- Subscription Renewal
- Purchase History
"""

import logging
import asyncio
from io import BytesIO
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.error import BadRequest
from telegram.ext import ContextTypes, ConversationHandler
from telegram.helpers import escape_markdown
from sqlalchemy import select, desc, asc, update
from sqlalchemy.orm import joinedload
from datetime import datetime, timedelta

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import User, Subscription, Profile, Transaction, Server, WireGuardSubscription, WireGuardProfile, OvpnConfig, PaymentReceipt, DiscountCode
from vpn_bot.wallet_manager import WalletManager
from vpn_bot.settings_utils import get_admin_setting
from vpn_bot.mikrotik_manager import MikroTikManager, get_mikrotik_manager
from vpn_bot.utils import (
    logger,
    LanguageManager,
    ensure_telegram_text,
    format_currency,
    get_profile_price,
    get_wg_profile_price,
    format_datetime,
    RLM,
    to_base36,
    generate_wg_keys,
)
import random, string, time
import re
from dataclasses import dataclass

# --- Coupon pricing helper ---

async def _resolve_checkout_pricing(
    session,
    user,
    profile,
    *,
    is_wg: bool = False,
    context: str = "purchase_ovpn",
    coupon_id: int | None = None,
):
    from vpn_bot.discount_service import apply_coupon_to_amount
    from vpn_bot.utils import get_currency_unit

    base = await (get_wg_profile_price(profile) if is_wg else get_profile_price(profile))
    unit = await get_currency_unit()
    if not coupon_id:
        return base, 0.0, base, unit, None
    pricing = await apply_coupon_to_amount(
        session,
        user_id=user.id,
        base_amount=base,
        currency=unit,
        context=context,
        coupon_id=coupon_id,
    )
    return (
        pricing.base_amount,
        pricing.discount_amount,
        pricing.final_amount,
        unit,
        pricing.discount_code_id,
    )

# --- Subscription Renewal ---

from vpn_bot.purchase_terms import (
    TERMS_SESSION_KEY,
    maybe_gate_terms,
    assert_terms_accepted_for_checkout,
)


async def show_renew_ovpn_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, sub_id: int):
    """Display OVPN renewal confirmation screen."""
    query = update.callback_query
    user_id = update.effective_user.id
    context.user_data["_renew_sub_id"] = sub_id
    context.user_data["_coupon_scope"] = "renew_ovpn"
    context.user_data["_coupon_resume"] = "renew_ovpn_confirm"
    from vpn_bot.coupon_flow import coupon_id_from_context

    coupon_id = coupon_id_from_context(context)

    async with AsyncSessionLocal() as session:
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        subscription = await session.get(Subscription, sub_id)
        if not subscription or not user or subscription.user_id != user.id:
            if query:
                await query.edit_message_text(LanguageManager.get('renew.sub_not_found'))
            return ConversationHandler.END

        profile = await session.get(Profile, subscription.profile_id)
        if not profile:
            if query:
                await query.edit_message_text(LanguageManager.get('renew.plan_gone'))
            return ConversationHandler.END

        base, disc, p_price, unit, _ = await _resolve_checkout_pricing(
            session, user, profile, is_wg=False, context="renew_ovpn", coupon_id=coupon_id
        )
        from vpn_bot.utils import utc_now as _utc_now

        exp = subscription.expiry_date
        if exp and exp.tzinfo is None:
            from datetime import timezone

            exp = exp.replace(tzinfo=timezone.utc)
        now = _utc_now()
        new_expiry = exp + timedelta(days=profile.validity_days) if exp else now + timedelta(days=profile.validity_days)
        if exp and exp < now:
            new_expiry = now + timedelta(days=profile.validity_days)

        formatted_price = await format_currency(p_price)
        formatted_balance = await format_currency(user.wallet_balance)
        formatted_remaining = await format_currency(user.wallet_balance - p_price)

        if disc > 0:
            from vpn_bot.coupon_flow import format_confirm_discount_line_html
            from vpn_bot.utils import markdown_bold_to_html

            text = markdown_bold_to_html(
                LanguageManager.get(
                    'renew.confirm_title',
                    plan=profile.name,
                    days=profile.validity_days,
                    gb=profile.data_limit_gb,
                    price=formatted_price,
                    balance=formatted_balance,
                    remaining=formatted_remaining,
                    expiry=new_expiry.strftime('%Y-%m-%d'),
                )
            )
            text += "\n" + format_confirm_discount_line_html(
                await format_currency(base),
                await format_currency(disc),
                formatted_price,
            )
        else:
            text = LanguageManager.get(
                'renew.confirm_title',
                plan=profile.name,
                days=profile.validity_days,
                gb=profile.data_limit_gb,
                price=formatted_price,
                balance=formatted_balance,
                remaining=formatted_remaining,
                expiry=new_expiry.strftime('%Y-%m-%d'),
            )
        keyboard = [
            [InlineKeyboardButton(LanguageManager.get('renew.btn_confirm'), callback_data=f'renew_confirm_{sub_id}')],
        ]
        if not coupon_id:
            keyboard.append([
                InlineKeyboardButton(
                    LanguageManager.get('coupon.btn_enter'),
                    callback_data='coupon_enter_inline_renew_ovpn',
                )
            ])
        keyboard.append([InlineKeyboardButton(LanguageManager.get('common.cancel'), callback_data='my_subs')])

    from vpn_bot.utils import send_localized_text

    await send_localized_text(
        update,
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        query=query,
        parse_mode="HTML" if disc > 0 else "Markdown",
    )
    return ConversationHandler.END


async def show_renew_wg_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE, sub_id: int):
    """Display WireGuard renewal confirmation screen."""
    query = update.callback_query
    user_id = update.effective_user.id
    context.user_data["_renew_sub_id"] = sub_id
    context.user_data["_coupon_scope"] = "renew_wg"
    context.user_data["_coupon_resume"] = "renew_wg_confirm"
    from vpn_bot.coupon_flow import coupon_id_from_context

    coupon_id = coupon_id_from_context(context)

    async with AsyncSessionLocal() as session:
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        s_res = await session.execute(
            select(WireGuardSubscription)
            .options(joinedload(WireGuardSubscription.profile))
            .where(WireGuardSubscription.id == sub_id)
        )
        subscription = s_res.scalars().first()
        if not subscription or not user or subscription.user_id != user.id:
            if query:
                await query.edit_message_text(LanguageManager.get('renew.sub_not_found'))
            return ConversationHandler.END

        profile = subscription.profile
        if not profile:
            if query:
                await query.edit_message_text(LanguageManager.get('renew.plan_gone'))
            return ConversationHandler.END

        from vpn_bot.utils import utc_now as _utc_now

        exp = subscription.expiry_date
        if exp and exp.tzinfo is None:
            from datetime import timezone

            exp = exp.replace(tzinfo=timezone.utc)
        now = _utc_now()
        new_expiry = exp + timedelta(days=profile.duration_days) if exp else now + timedelta(days=profile.duration_days)
        if exp and exp < now:
            new_expiry = now + timedelta(days=profile.duration_days)

        base, disc, p_price, unit, _ = await _resolve_checkout_pricing(
            session, user, profile, is_wg=True, context="renew_wg", coupon_id=coupon_id
        )
        formatted_price = await format_currency(p_price)
        formatted_balance = await format_currency(user.wallet_balance)
        formatted_remaining = await format_currency(user.wallet_balance - p_price)

        if disc > 0:
            from vpn_bot.coupon_flow import format_confirm_discount_line_html
            from vpn_bot.utils import markdown_bold_to_html

            text = markdown_bold_to_html(
                LanguageManager.get(
                    'renew.wg_confirm_title',
                    plan=profile.name,
                    days=profile.duration_days,
                    gb=profile.volume_gb,
                    price=formatted_price,
                    balance=formatted_balance,
                    remaining=formatted_remaining,
                    expiry=new_expiry.strftime('%Y-%m-%d'),
                )
            )
            text += "\n" + format_confirm_discount_line_html(
                await format_currency(base),
                await format_currency(disc),
                formatted_price,
            )
        else:
            text = LanguageManager.get(
                'renew.wg_confirm_title',
                plan=profile.name,
                days=profile.duration_days,
                gb=profile.volume_gb,
                price=formatted_price,
                balance=formatted_balance,
                remaining=formatted_remaining,
                expiry=new_expiry.strftime('%Y-%m-%d'),
            )
        keyboard = [
            [InlineKeyboardButton(LanguageManager.get('renew.btn_confirm'), callback_data=f'renew_wg_confirm_{sub_id}')],
        ]
        if not coupon_id:
            keyboard.append([
                InlineKeyboardButton(
                    LanguageManager.get('coupon.btn_enter'),
                    callback_data='coupon_enter_inline_renew_wg',
                )
            ])
        keyboard.append([InlineKeyboardButton(LanguageManager.get('common.cancel'), callback_data='my_subs')])

    from vpn_bot.utils import send_localized_text

    await send_localized_text(
        update,
        text,
        reply_markup=InlineKeyboardMarkup(keyboard),
        query=query,
        parse_mode="HTML" if disc > 0 else "Markdown",
    )
    return ConversationHandler.END


async def renew_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle subscription renewal."""
    query = update.callback_query
    sub_id = int(query.data.split('_')[1])
    
    await query.answer()

    if not context.user_data.pop('_from_terms_resume', False):
        context.user_data.pop(TERMS_SESSION_KEY, None)

    user_id = update.effective_user.id
    from vpn_bot.utils import LanguageManager
    
    async with AsyncSessionLocal() as session:
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        if not user:
            await query.edit_message_text(LanguageManager.get('renew.user_not_found'))
            return ConversationHandler.END
        
        subscription = await session.get(Subscription, sub_id)
        if not subscription or subscription.user_id != user.id:
            await query.edit_message_text(LanguageManager.get('renew.sub_not_found'))
            return ConversationHandler.END
            
        server = await session.get(Server, subscription.server_id)
        if not server or not server.is_active:
            await query.edit_message_text(LanguageManager.get('admin.sales.maintenance_mode_msg'), parse_mode='Markdown')
            return ConversationHandler.END

        profile = await session.get(Profile, subscription.profile_id)
        from vpn_bot.renewal_policy import check_renewal_eligibility

        allowed, block_msg = await check_renewal_eligibility(
            subscription, "um", profile=profile
        )
        if not allowed:
            await query.edit_message_text(block_msg, parse_mode='Markdown')
            return ConversationHandler.END

        if not profile:
            await query.edit_message_text(LanguageManager.get('renew.plan_gone'))
            return ConversationHandler.END
        
        p_res = await session.execute(
            select(Profile).where(
                Profile.name.like(f"{profile.name.split('_v')[0]}%"),
                Profile.server_id == profile.server_id
            ).order_by(desc(Profile.version))
        )
        latest_profile = p_res.scalars().first()
        
        if latest_profile and latest_profile.id != profile.id:
            profile = latest_profile

    gate = await maybe_gate_terms(
        update, context, user, resume={"type": "renew_ovpn", "sub_id": sub_id}
    )
    if gate is not None:
        return gate

    context.user_data["_renew_sub_id"] = sub_id
    context.user_data["_coupon_scope"] = "renew_ovpn"
    context.user_data["_coupon_resume"] = "renew_ovpn_confirm"
    return await show_renew_ovpn_confirm(update, context, sub_id)

async def confirm_renewal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process renewal confirmation with atomic transaction."""
    query = update.callback_query
    sub_id = int(query.data.split('_')[2])
    from vpn_bot.utils import LanguageManager
    from vpn_bot.coupon_flow import coupon_id_from_context, clear_active_coupon
    
    await query.answer(LanguageManager.get('renew.processing'))
    
    user_id = update.effective_user.id
    coupon_id = coupon_id_from_context(context)
    
    async with AsyncSessionLocal() as session:
        try:
            # 1. Fetch data
            u_res = await session.execute(select(User).where(User.telegram_id == user_id))
            user = u_res.scalars().first()
            
            sub_res = await session.execute(
                select(Subscription).where(Subscription.id == sub_id).with_for_update()
            )
            subscription = sub_res.scalars().first()
            if not subscription or not user or subscription.user_id != user.id:
                await query.edit_message_text(LanguageManager.get('renew.sub_not_found'))
                return ConversationHandler.END

            if user.is_banned:
                await query.edit_message_text(LanguageManager.get('common.account_banned'))
                return ConversationHandler.END

            profile = await session.get(Profile, subscription.profile_id)
            if not profile:
                 await query.edit_message_text(LanguageManager.get('renew.plan_gone'))
                 return ConversationHandler.END

            from vpn_bot.renewal_policy import check_renewal_eligibility
            allowed, block_msg = await check_renewal_eligibility(subscription, "um", profile=profile)
            if not allowed:
                await query.edit_message_text(block_msg, parse_mode='Markdown')
                return ConversationHandler.END

            terms_ok, terms_err = await assert_terms_accepted_for_checkout(user)
            if not terms_ok:
                await query.edit_message_text(terms_err, parse_mode='Markdown')
                return ConversationHandler.END

            base, disc, p_price, unit, code_id = await _resolve_checkout_pricing(
                session, user, profile, is_wg=False, context="renew_ovpn", coupon_id=coupon_id
            )
            deduct_desc = LanguageManager.get('desc.renewal', name=profile.name, user=subscription.mikrotik_username)
            txn = await WalletManager._deduct_logic(
                session,
                user.id,
                p_price,
                deduct_desc,
                discount_code_id=code_id,
                original_amount=base if code_id else None,
                discount_amount=disc if code_id else None,
            )
            if not txn:
                await query.edit_message_text(LanguageManager.get('renew.error_insufficient'))
                return ConversationHandler.END

            if code_id:
                from vpn_bot.discount_service import redeem_coupon_atomic
                await redeem_coupon_atomic(
                    session,
                    code_id=code_id,
                    user_id=user.id,
                    context="renew_ovpn",
                    original_amount=base,
                    final_amount=p_price,
                    discount_amount=disc,
                    currency=unit,
                    transaction_id=txn.id,
                )

            # 3. Server Logic (Connect)
            server = await session.get(Server, profile.server_id)

            # 3. Update Expiry in DB
            from vpn_bot.utils import utc_now as _utc_now

            now = _utc_now()
            exp = subscription.expiry_date
            if exp and exp.tzinfo is None:
                from datetime import timezone

                exp = exp.replace(tzinfo=timezone.utc)
                subscription.expiry_date = exp
            is_currently_expired = not exp or exp < now

            if is_currently_expired:
                subscription.expiry_date = now + timedelta(days=profile.validity_days)
            else:
                subscription.expiry_date = exp + timedelta(days=profile.validity_days)

            subscription.status = 'active'
            subscription.used_bytes = 0
            subscription.total_limit_bytes = int(profile.data_limit_gb or 0) * 1024**3
            
            # 4. MikroTik Synchronization
            server = await session.get(Server, subscription.server_id)
            
            if server:
                try:
                    mgr = get_mikrotik_manager(server)
                    await asyncio.to_thread(mgr.enable_user, subscription.mikrotik_username)
                    # Use the absolute expiry already computed in DB so that stacked
                    # renewal days are faithfully reflected on the router.
                    mt_success = await asyncio.to_thread(
                        mgr.set_user_expiry,
                        subscription.mikrotik_username,
                        subscription.expiry_date,
                    )
                    if not mt_success:
                        logging.warning(
                            f"MikroTik set_user_expiry might have failed for {subscription.mikrotik_username}"
                        )
                    if profile.data_limit_gb:
                        await asyncio.to_thread(
                            mgr.set_user_data_limit,
                            subscription.mikrotik_username,
                            profile.data_limit_gb,
                        )
                except Exception as mt_err:
                    logging.error(f"MikroTik renewal sync failed: {mt_err}")
                    raise Exception(f"VPN Server sync error: {mt_err}") from mt_err

            # 5. Commit everything
            await session.commit()
            
            p_price = await get_profile_price(profile)
            formatted_price = await format_currency(p_price)
            text = LanguageManager.get('renew.success',
                username=subscription.mikrotik_username,
                plan=profile.name,
                days=profile.validity_days,
                expiry=subscription.expiry_date.strftime('%Y-%m-%d'),
                price=formatted_price
            )
            
            keyboard = [
                [InlineKeyboardButton(LanguageManager.get('menu.my_subs'), callback_data='my_subs')],
                [InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')]
            ]
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            context.user_data.pop(TERMS_SESSION_KEY, None)
            clear_active_coupon(context.user_data)
            return ConversationHandler.END

        except Exception as e:
            await session.rollback()
            logging.error(f"Renewal flow error: {e}")
            await query.edit_message_text(LanguageManager.get('renew.error_generic'), parse_mode='Markdown')
            return ConversationHandler.END

async def renew_wg_subscription(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle WireGuard subscription renewal."""
    query = update.callback_query
    sub_id = int(query.data.split('_')[2])
    
    await query.answer()

    if not context.user_data.pop('_from_terms_resume', False):
        context.user_data.pop(TERMS_SESSION_KEY, None)

    user_id = update.effective_user.id
    from vpn_bot.utils import LanguageManager
    
    async with AsyncSessionLocal() as session:
        # Get user
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        if not user:
            await query.edit_message_text(LanguageManager.get('renew.user_not_found'))
            return ConversationHandler.END
        
        # Get subscription
        subscription = await session.execute(
            select(WireGuardSubscription)
            .options(joinedload(WireGuardSubscription.profile), joinedload(WireGuardSubscription.interface))
            .where(WireGuardSubscription.id == sub_id)
        )
        subscription = subscription.scalars().first()

        if not subscription or subscription.user_id != user.id:
            await query.edit_message_text(LanguageManager.get('renew.sub_not_found'))
            return ConversationHandler.END
            
        # 1. Check for Maintenance Lock
        interface = subscription.interface
        if not interface or not interface.is_active:
            await query.edit_message_text(LanguageManager.get('admin.sales.maintenance_mode_msg'), parse_mode='Markdown')
            return ConversationHandler.END

        profile = subscription.profile
        from vpn_bot.renewal_policy import check_renewal_eligibility

        allowed, block_msg = await check_renewal_eligibility(
            subscription, "wg", profile=profile
        )
        if not allowed:
            await query.edit_message_text(block_msg, parse_mode='Markdown')
            return ConversationHandler.END

        if not profile:
            await query.edit_message_text(LanguageManager.get('renew.plan_gone'))
            return ConversationHandler.END
        
        # No versioning for WG profiles yet
        # p_res = await session.execute(
        #     select(WireGuardProfile).where(
        #         WireGuardProfile.name.like(f"{profile.name.split('_v')[0]}%"),
        #         WireGuardProfile.server_id == profile.server_id
        #     ).order_by(desc(WireGuardProfile.version))
        # )
        # latest_profile = p_res.scalars().first()
        # if latest_profile and latest_profile.id != profile.id:
        #     profile = latest_profile
        
    gate = await maybe_gate_terms(
        update, context, user, resume={"type": "renew_wg", "sub_id": sub_id}
    )
    if gate is not None:
        return gate

    context.user_data["_renew_sub_id"] = sub_id
    context.user_data["_coupon_scope"] = "renew_wg"
    context.user_data["_coupon_resume"] = "renew_wg_confirm"
    return await show_renew_wg_confirm(update, context, sub_id)

async def confirm_wg_renewal(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Process WireGuard renewal confirmation."""
    query = update.callback_query
    sub_id = int(query.data.split('_')[3])
    from vpn_bot.utils import LanguageManager
    
    await query.answer(LanguageManager.get('renew.processing'))
    
    user_id = update.effective_user.id
    
    async with AsyncSessionLocal() as session:
        try:
            # 1. Fetch data
            u_res = await session.execute(select(User).where(User.telegram_id == user_id))
            user = u_res.scalars().first()
            
            s_res = await session.execute(
                select(WireGuardSubscription)
                .where(WireGuardSubscription.id == sub_id)
                .with_for_update()
            )
            subscription = s_res.scalars().first()
            if subscription:
                await session.refresh(subscription, attribute_names=["profile", "interface"])

            if not subscription or not user or subscription.user_id != user.id:
                await query.edit_message_text(LanguageManager.get('renew.sub_not_found'))
                return ConversationHandler.END

            if user.is_banned:
                await query.edit_message_text(LanguageManager.get('common.account_banned'))
                return ConversationHandler.END

            profile = subscription.profile
            if not profile:
                 await query.edit_message_text(LanguageManager.get('renew.plan_gone'))
                 return ConversationHandler.END

            from vpn_bot.renewal_policy import check_renewal_eligibility
            allowed, block_msg = await check_renewal_eligibility(subscription, "wg", profile=profile)
            if not allowed:
                await query.edit_message_text(block_msg, parse_mode='Markdown')
                return ConversationHandler.END

            terms_ok, terms_err = await assert_terms_accepted_for_checkout(user)
            if not terms_ok:
                await query.edit_message_text(terms_err, parse_mode='Markdown')
                return ConversationHandler.END

            from vpn_bot.coupon_flow import coupon_id_from_context, clear_active_coupon
            coupon_id = coupon_id_from_context(context)
            base, disc, p_price, unit, code_id = await _resolve_checkout_pricing(
                session, user, profile, is_wg=True, context="renew_wg", coupon_id=coupon_id
            )
            desc = LanguageManager.get('wg.renewal_desc', name=profile.name, uid=subscription.unique_identifier)
            txn = await WalletManager._deduct_logic(
                session,
                user.id,
                p_price,
                desc,
                discount_code_id=code_id,
                original_amount=base if code_id else None,
                discount_amount=disc if code_id else None,
            )
            if not txn:
                await query.edit_message_text(LanguageManager.get('renew.error_insufficient'))
                return ConversationHandler.END

            if code_id:
                from vpn_bot.discount_service import redeem_coupon_atomic
                await redeem_coupon_atomic(
                    session,
                    code_id=code_id,
                    user_id=user.id,
                    context="renew_wg",
                    original_amount=base,
                    final_amount=p_price,
                    discount_amount=disc,
                    currency=unit,
                    transaction_id=txn.id,
                )

            # Renewal reuses existing peer — sales_ovpn_limit / sales_wg_limit do not apply.

            # 3. Update Expiry in DB
            from vpn_bot.utils import utc_now as _utc_now

            now = _utc_now()
            exp = subscription.expiry_date
            if exp and exp.tzinfo is None:
                from datetime import timezone

                exp = exp.replace(tzinfo=timezone.utc)
                subscription.expiry_date = exp
            is_currently_expired = exp < now if exp else True

            if is_currently_expired:
                subscription.expiry_date = now + timedelta(days=profile.duration_days)
            else:
                subscription.expiry_date = exp + timedelta(days=profile.duration_days)
            
            # Reset traffic usage on renewal? (Logic decision: yes, usually)
            # Actually, MikroTik traffic is cumulative, so we can't easily reset it on the router 
            # without deleting/readding, but we can reset our RX/TX counters in DB.
            subscription.total_bytes_rx = 0
            subscription.total_bytes_tx = 0
            subscription.bytes_remaining = (profile.volume_gb or 0) * 1024**3
            subscription.status = 'active'

            expiry_str = subscription.expiry_date.strftime('%Y-%m-%d')
            subscription.comment_text = (
                f"{subscription.unique_identifier} | "
                f"{user.username or user.telegram_id} | {expiry_str}"
            )
            
            # 4. MikroTik Synchronization — remove/re-add peer to reset router quota
            try:
                from vpn_bot.mikrotik_manager import get_mikrotik_manager
                interface = subscription.interface
                if not interface:
                    raise ValueError("WG subscription has no interface")
                server = await session.get(Server, interface.server_id)
                mt = get_mikrotik_manager(server)
                allowed = subscription.assigned_ip
                if allowed and '/' not in allowed:
                    allowed = f"{allowed}/32"
                mt_ok = await asyncio.to_thread(
                    mt.reset_wg_peer_for_renewal,
                    interface.name,
                    subscription.peer_public_key,
                    allowed,
                    subscription.comment_text,
                )
                if not mt_ok:
                    raise ValueError("MikroTik peer quota reset failed")
                subscription.last_router_rx = 0
                subscription.last_router_tx = 0
                logger.info(
                    f"Reset WG peer quota for {subscription.unique_identifier} on MikroTik during renewal."
                )
            except Exception as e:
                logger.error(f"Failed to reset WG peer on MikroTik during renewal: {e}")
                raise Exception(f"VPN server sync error: {e}") from e

            # 5. Commit everything
            await session.commit()
            
            formatted_price = await format_currency(p_price)
            formatted_expiry_str = await format_datetime(subscription.expiry_date, include_time=True)
            
            text = LanguageManager.get('renew.wg_success',
                unique_id=subscription.unique_identifier,
                plan=profile.name,
                days=profile.duration_days,
                expiry=formatted_expiry_str,
                price=formatted_price
            )
            
            await query.edit_message_text(text, parse_mode='Markdown')
            
            # 6. Send Files (Mirror initial delivery)
            from vpn_bot.bot_handler import send_wg_config_again
            # We want to use the same logic but without duplicating the success message.
            # send_wg_config_again does its own session.get, so it's safe to call.
            await send_wg_config_again(update, context, sub_id=sub_id)

            context.user_data.pop(TERMS_SESSION_KEY, None)
            clear_active_coupon(context.user_data)
            return ConversationHandler.END

        except Exception as e:
            await session.rollback()
            logging.error(f"WG Renewal flow error: {e}")
            if "connection" in str(e).lower() or "timeout" in str(e).lower():
                await query.edit_message_text(LanguageManager.get('renew.error_server'))
            else:
                await query.edit_message_text(LanguageManager.get('renew.error_generic'), parse_mode='Markdown')
            return ConversationHandler.END

# --- Purchase History ---

_LEGACY_RECEIPT_ID_RE = re.compile(r"#(\d+)")


@dataclass
class _WalletLot:
    remaining: float
    receipt_display_id: str | None


def _escape_history_desc(description: str | None) -> str:
    return escape_markdown(description or "", version=1)


def _parse_legacy_receipt_id(description: str | None) -> int | None:
    if not description:
        return None
    match = _LEGACY_RECEIPT_ID_RE.search(description)
    return int(match.group(1)) if match else None


def _resolve_receipt_display_id(
    txn: Transaction,
    receipts_by_id: dict[int, PaymentReceipt],
) -> str | None:
    from vpn_bot.admin_receipt_service import receipt_display_id

    rid = getattr(txn, "receipt_id", None) or _parse_legacy_receipt_id(txn.description)
    if not rid:
        return None
    receipt = receipts_by_id.get(rid)
    if receipt:
        return receipt_display_id(receipt)
    return f"RCP-{rid:05d}"


def _attribute_wallet_receipts(
    transactions_asc: list[Transaction],
    receipts_by_id: dict[int, PaymentReceipt],
) -> dict[int, str]:
    lots: list[_WalletLot] = []
    purchase_map: dict[int, str] = {}

    for txn in transactions_asc:
        if txn.amount > 0:
            display = None
            if txn.type == "deposit_card":
                display = _resolve_receipt_display_id(txn, receipts_by_id)
            lots.append(_WalletLot(remaining=float(txn.amount), receipt_display_id=display))
            continue

        if txn.type != "purchase" and txn.amount >= 0:
            continue

        need = abs(float(txn.amount))
        linked: str | None = None
        for lot in lots:
            if lot.remaining <= 0:
                continue
            take = min(lot.remaining, need)
            lot.remaining -= take
            need -= take
            if lot.receipt_display_id:
                linked = lot.receipt_display_id
            if need <= 0:
                break
        if linked:
            purchase_map[txn.id] = linked

    return purchase_map


def _localize_txn_type(txn_type: str) -> str:
    type_key = f"history.type_{txn_type}"
    localized = LanguageManager.get(type_key)
    if localized == f"[{type_key}]":
        return txn_type.replace("_", " ").title()
    return localized


async def _format_coupon_lines(
    txn: Transaction,
    codes_by_id: dict,
    *,
    coupon_key: str = "history.coupon_line",
    breakdown_key: str = "history.discount_breakdown",
) -> str:
    code_id = getattr(txn, "discount_code_id", None)
    if not code_id:
        return ""
    dc = codes_by_id.get(code_id)
    code_str = dc.code if dc else str(code_id)
    lines = LanguageManager.get(coupon_key, code=code_str)
    orig = getattr(txn, "original_amount", None)
    disc = getattr(txn, "discount_amount", None)
    if orig is not None and disc is not None and disc > 0:
        unit = getattr(txn, "currency_unit", None)
        original_fmt = await format_currency(orig, unit=unit)
        discount_fmt = await format_currency(disc, unit=unit)
        if txn.type == "purchase" or txn.amount < 0:
            paid = abs(txn.amount)
        else:
            paid = max(0.0, float(orig) - float(disc))
        paid_fmt = await format_currency(paid, unit=unit)
        lines += LanguageManager.get(
            breakdown_key,
            original=original_fmt,
            discount=discount_fmt,
            paid=paid_fmt,
        )
    return lines


async def format_transaction_summary(
    txn: Transaction,
    codes_by_id: dict,
) -> str:
    """Multi-line transaction block for admin user hub."""
    localized_type = _localize_txn_type(txn.type)
    formatted_amount = await format_currency(
        abs(txn.amount), unit=getattr(txn, "currency_unit", None)
    )
    formatted_date = await format_datetime(txn.created_at, include_time=True)
    sign = "+" if txn.amount >= 0 else "-"
    desc = _escape_history_desc(txn.description) or LanguageManager.get("common.na")
    coupon_line = await _format_coupon_lines(
        txn,
        codes_by_id,
        coupon_key="admin.user.txn_coupon",
        breakdown_key="admin.user.txn_discount",
    )
    return LanguageManager.get(
        "admin.user.txn_item",
        type_label=localized_type,
        date=formatted_date,
        desc=desc,
        coupon_line=coupon_line,
        sign=sign,
        amount=formatted_amount,
    )


async def _format_history_item(
    txn: Transaction,
    purchase_receipt_map: dict[int, str],
    receipts_by_id: dict[int, PaymentReceipt],
    codes_by_id: dict | None = None,
) -> str:
    localized_type = _localize_txn_type(txn.type)
    formatted_amount = await format_currency(
        abs(txn.amount), unit=getattr(txn, "currency_unit", None)
    )
    formatted_date = await format_datetime(txn.created_at, include_time=True)
    desc = _escape_history_desc(txn.description)
    coupon_line = await _format_coupon_lines(txn, codes_by_id or {})

    if txn.type == "deposit_card":
        receipt_id = _resolve_receipt_display_id(txn, receipts_by_id) or LanguageManager.get("common.na")
        return LanguageManager.get(
            "history.item_deposit_card",
            type=localized_type,
            receipt_id=receipt_id,
            coupon_line=coupon_line,
            amount=formatted_amount,
            date=formatted_date,
        )

    if txn.type == "purchase":
        linked = purchase_receipt_map.get(txn.id)
        receipt_line = (
            LanguageManager.get("history.receipt_linked", receipt_id=linked)
            if linked
            else LanguageManager.get("history.receipt_linked_empty")
        )
        return LanguageManager.get(
            "history.item_purchase",
            type=localized_type,
            desc=desc,
            receipt_line=receipt_line,
            coupon_line=coupon_line,
            amount=formatted_amount,
            date=formatted_date,
        )

    if txn.type == "deposit":
        icon, sign = "✅", "+"
    elif txn.type == "refund":
        icon, sign = "🔄", "+"
    else:
        icon = "⚙️"
        sign = "+" if txn.amount >= 0 else "-"

    return LanguageManager.get(
        "history.item_default",
        icon=icon,
        type=localized_type,
        sign=sign,
        amount=formatted_amount,
        date=formatted_date,
        desc=desc,
        receipt_line=LanguageManager.get("history.receipt_linked_empty"),
        coupon_line=coupon_line,
    )


_HISTORY_SEPARATOR_RE = re.compile(r"^[━➖\-]+$")


def _format_history_separators_rtl(text: str) -> str:
    """RTL-mark separator-only lines; leave date/amount lines unchanged."""
    if LanguageManager._current_lang != "fa":
        return text
    lines = []
    for line in text.split("\n"):
        core = line.strip().lstrip(RLM)
        if core and _HISTORY_SEPARATOR_RE.fullmatch(core):
            lines.append(RLM + core)
        else:
            lines.append(line)
    return "\n".join(lines)


async def _history_send(
    update: Update,
    query,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    body = ensure_telegram_text(_format_history_separators_rtl(text))
    try:
        if query:
            await query.edit_message_text(
                body, reply_markup=reply_markup, parse_mode="Markdown"
            )
        else:
            await update.message.reply_text(
                body, reply_markup=reply_markup, parse_mode="Markdown"
            )
    except BadRequest as exc:
        if "parse entities" not in str(exc).lower():
            raise
        if query:
            await query.edit_message_text(body, reply_markup=reply_markup)
        else:
            await update.message.reply_text(body, reply_markup=reply_markup)


async def purchase_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display purchase history."""
    query = update.callback_query
    if query:
        await query.answer()
        user_id = update.effective_user.id
    else:
        user_id = update.effective_user.id
        # Reset page if called from menu
        context.user_data['history_page'] = 0
    
    page = context.user_data.get('history_page', 0)
    per_page = 10
    from vpn_bot.utils import LanguageManager
    
    async with AsyncSessionLocal() as session:
        # Get user
        u_res = await session.execute(select(User).where(User.telegram_id == user_id))
        user = u_res.scalars().first()
        
        if not user:
            msg = LanguageManager.get('renew.user_not_found')
            if query:
                await query.edit_message_text(msg)
            else:
                await update.message.reply_text(msg)
            return ConversationHandler.END
        
        # Paginated transactions (newest first)
        t_res = await session.execute(
            select(Transaction)
            .where(Transaction.user_id == user.id)
            .order_by(desc(Transaction.created_at))
            .limit(per_page)
            .offset(page * per_page)
        )
        transactions = t_res.scalars().all()

        # All transactions for FIFO receipt attribution
        all_res = await session.execute(
            select(Transaction)
            .where(Transaction.user_id == user.id)
            .order_by(asc(Transaction.created_at))
        )
        all_txns = all_res.scalars().all()

        receipt_ids: set[int] = set()
        for txn in all_txns:
            if getattr(txn, "receipt_id", None):
                receipt_ids.add(txn.receipt_id)
            legacy_id = _parse_legacy_receipt_id(txn.description)
            if legacy_id:
                receipt_ids.add(legacy_id)

        receipts_by_id: dict[int, PaymentReceipt] = {}
        if receipt_ids:
            r_res = await session.execute(
                select(PaymentReceipt).where(PaymentReceipt.id.in_(receipt_ids))
            )
            for receipt in r_res.scalars().all():
                receipts_by_id[receipt.id] = receipt

        purchase_receipt_map = _attribute_wallet_receipts(all_txns, receipts_by_id)

        code_ids = {
            t.discount_code_id
            for t in transactions
            if getattr(t, "discount_code_id", None)
        }
        codes_by_id: dict = {}
        if code_ids:
            from vpn_bot.models import DiscountCode
            c_res = await session.execute(
                select(DiscountCode).where(DiscountCode.id.in_(code_ids))
            )
            for code_row in c_res.scalars().all():
                codes_by_id[code_row.id] = code_row
        
        # Count total
        count_res = await session.execute(
            select(Transaction).where(Transaction.user_id == user.id)
        )
        total = len(count_res.scalars().all())
    
    formatted_balance = await format_currency(user.wallet_balance)
    text = LanguageManager.get('history.title', balance=formatted_balance)
    
    if not transactions:
        text += LanguageManager.get('history.empty')
    else:
        for txn in transactions:
            text += await _format_history_item(
                txn, purchase_receipt_map, receipts_by_id, codes_by_id
            )
    
    keyboard = []
    
    # Pagination
    nav_row = []
    if page > 0:
        nav_row.append(InlineKeyboardButton(LanguageManager.get('history.btn_prev'), callback_data='history_prev'))
    if (page + 1) * per_page < total:
        nav_row.append(InlineKeyboardButton(LanguageManager.get('history.btn_next'), callback_data='history_next'))
    
    if nav_row:
        keyboard.append(nav_row)
    
    # Only show 'Back to Main Menu' if inside a flow, but here it's fine always
    keyboard.append([InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')])
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    await _history_send(update, query, text, reply_markup)

    return ConversationHandler.END

async def history_navigate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle history pagination."""
    query = update.callback_query
    await query.answer()
    
    current_page = context.user_data.get('history_page', 0)
    
    if query.data == 'history_prev':
        context.user_data['history_page'] = max(0, current_page - 1)
    elif query.data == 'history_next':
        context.user_data['history_page'] = current_page + 1
        
    return await purchase_history(update, context)

# --- Tutorials & Resources ---

async def tutorials_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display platform selection for tutorials."""
    query = update.callback_query
    if query:
        await query.answer()
    
    from vpn_bot.admin_settings import get_admin_setting
    from vpn_bot.utils import LanguageManager
    
    # Check for custom text or use localized default
    custom_text = await get_admin_setting('tutorial_text', LanguageManager.get('tutorial.menu_text'))
    
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('tutorial.btn_android'), callback_data='tutorial_android'),
         InlineKeyboardButton(LanguageManager.get('tutorial.btn_ios'), callback_data='tutorial_ios')],
        [InlineKeyboardButton(LanguageManager.get('tutorial.btn_windows'), callback_data='tutorial_windows'),
         InlineKeyboardButton(LanguageManager.get('tutorial.btn_mac'), callback_data='tutorial_mac')],
        [InlineKeyboardButton(LanguageManager.get('tutorial.btn_apps'), callback_data='download_apps')],
        [InlineKeyboardButton(LanguageManager.get('common.main_menu'), callback_data='main_menu')]
    ]
    
    reply_markup = InlineKeyboardMarkup(keyboard)
    
    if query:
        await query.edit_message_text(custom_text, reply_markup=reply_markup, parse_mode='Markdown')
    else:
        await update.message.reply_text(custom_text, reply_markup=reply_markup, parse_mode='Markdown')

async def show_tutorial(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show specific platform tutorial from admin settings."""
    query = update.callback_query
    platform = query.data.split('_')[1]
    await query.answer()
    
    from vpn_bot.admin_settings import get_admin_setting
    from vpn_bot.utils import LanguageManager
    
    # Default tutorials (used if not customized)
    default_tutorials = {
        'android': LanguageManager.get('tutorial.text_android'),
        'ios': LanguageManager.get('tutorial.text_ios'),
        'windows': LanguageManager.get('tutorial.text_windows'),
        'mac': LanguageManager.get('tutorial.text_mac')
    }
    
    # Try to get customized tutorial from admin settings
    setting_key = f'tutorial_{platform}'
    
    # Note: Admin settings currently don't support per-language custom text nicely without dicts.
    # We fallback to localized default if setting missing.
    # If admin creates a setting, it overrides all languages unless we handle dicts there.
    # For now, we assume if admin set it, they set it for current primary language or we use Logic in get_admin_setting to handle dicts?
    # get_admin_setting just returns JSON value.
    # We should stick to what bot_handler.start did: check if dict.
    
    val = await get_admin_setting(setting_key)
    if val:
        if isinstance(val, dict):
            lang = LanguageManager._current_lang
            text = val.get(lang, val.get('en', str(val)))
        else:
            text = str(val)
    else:
        text = default_tutorials.get(platform, "Tutorial not found.")
    
    keyboard = [[InlineKeyboardButton(LanguageManager.get('tutorial.btn_back_tut'), callback_data='tutorials')]]
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def download_apps_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display download links for VPN apps."""
    query = update.callback_query
    await query.answer()
    
    from vpn_bot.admin_settings import get_admin_setting
    from vpn_bot.utils import LanguageManager
    
    custom_text = await get_admin_setting('download_apps_text', LanguageManager.get('tutorial.apps_text'))
    
    keyboard = [
        [InlineKeyboardButton(LanguageManager.get('tutorial.btn_ovpn_android'), url='https://play.google.com/store/apps/details?id=net.openvpn.openvpn')],
        [InlineKeyboardButton(LanguageManager.get('tutorial.btn_ovpn_ios'), url='https://apps.apple.com/app/openvpn-connect/id590379981')],
        [InlineKeyboardButton(LanguageManager.get('tutorial.btn_ovpn_windows'), url='https://openvpn.net/community-downloads/')],
        [InlineKeyboardButton(LanguageManager.get('tutorial.btn_back_tut'), callback_data='tutorials')]
    ]
    
    await query.edit_message_text(custom_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def _rollback_ovpn_prep(user_db_id: int, sub_id: int, refund_amount: float, unit: str):
    """Refund wallet and remove staged subscription after MikroTik failure."""
    from vpn_bot.models import Transaction

    async with AsyncSessionLocal() as session:
        user = await session.get(User, user_db_id)
        sub = await session.get(Subscription, sub_id)
        if user and refund_amount > 0:
            user.wallet_balance += refund_amount
            session.add(
                Transaction(
                    user_id=user_db_id,
                    amount=refund_amount,
                    type="refund",
                    description=LanguageManager.get("buy.mt_error"),
                    currency_unit=unit,
                )
            )
        if sub:
            await session.delete(sub)
        await session.commit()


async def checkout_subscription(user_id: int, profile_id: int, progress_callback=None, coupon_id: int | None = None):
    """
    Executes a purchase transaction for a user and profile.
    Checks balance, creates Mikrotik user, deducts balance, and saves subscription.
    Returns: (success, subscription, error_message)
    """
    from vpn_bot.admin_sales_service import assert_new_purchase_capacity

    ok_cap, cap_msg = await assert_new_purchase_capacity("ovpn")
    if not ok_cap:
        return False, None, cap_msg

    prep = None
    async with AsyncSessionLocal() as session:
        # 1. Fetch Plan & User
        p_res = await session.execute(select(Profile).where(Profile.id == profile_id))
        profile = p_res.scalars().first()
        
        u_res = await session.execute(
            select(User).where(User.telegram_id == user_id).with_for_update()
        )
        user = u_res.scalars().first()
        
        if not profile or not user:
            return False, None, LanguageManager.get('buy.error_load')

        if user.is_banned:
            return False, None, LanguageManager.get('user.account_banned')

        terms_ok, terms_err = await assert_terms_accepted_for_checkout(user)
        if not terms_ok:
            return False, None, terms_err

        if not profile.is_active:
             return False, None, LanguageManager.get('renew.plan_gone')

        # 2. Check Balance & Transactional Deduct
        try:
            base, disc, p_price, unit, code_id = await _resolve_checkout_pricing(
                session, user, profile, is_wg=False, context="purchase_ovpn", coupon_id=coupon_id
            )
        except ValueError as exc:
            key = str(exc.args[0]) if exc.args else "coupon.invalid"
            return False, None, LanguageManager.get(key)
        # Allow 100 unit mismatch for better UX in manual payments
        safety_margin = 100
        if user.wallet_balance + safety_margin < p_price:
             return False, None, LanguageManager.get('renew.insufficient', plan=profile.name, price=p_price, balance=user.wallet_balance, needed=p_price-user.wallet_balance)

        if user.wallet_balance < p_price and p_price > 0:
            return False, None, LanguageManager.get('renew.insufficient', plan=profile.name, price=p_price, balance=user.wallet_balance, needed=p_price - user.wallet_balance)

        # Deduct logic - manual to keep session active
        from vpn_bot.models import Transaction
        
        deduct_amount = p_price
        if deduct_amount > 0:
            new_balance = user.wallet_balance - deduct_amount
            await session.execute(
                update(User).where(User.id == user.id).values(wallet_balance=new_balance)
            )
            user.wallet_balance = new_balance
        
        desc = LanguageManager.get('desc.purchase', name=profile.name)
        if code_id and disc > 0:
            dc = await session.get(DiscountCode, code_id)
            code_label = dc.code if dc else str(code_id)
            desc = LanguageManager.get('desc.purchase_discounted', name=profile.name, code=code_label)
        
        txn = Transaction(
            user_id=user.id,
            amount=-deduct_amount if deduct_amount > 0 else 0,
            type='purchase',
            description=desc,
            currency_unit=unit,
            discount_code_id=code_id,
            original_amount=base if code_id else None,
            discount_amount=disc if code_id else None,
        )
        session.add(txn)
        await session.flush()

        if code_id:
            from vpn_bot.discount_service import redeem_coupon_atomic
            await redeem_coupon_atomic(
                session,
                code_id=code_id,
                user_id=user.id,
                context="purchase_ovpn",
                original_amount=base,
                final_amount=p_price,
                discount_amount=disc,
                currency=unit,
                transaction_id=txn.id,
            )

        # 3. Fetch Server Details
        server = await session.get(Server, profile.server_id)
        if not server:
            await session.rollback()
            return False, None, LanguageManager.get('buy.server_error')

        # 4. Create Subscription Record (Stage 1: Get ID for unique username)
        from vpn_bot.utils import utc_now
        expiry = utc_now() + timedelta(days=profile.validity_days)
        sub = Subscription(
            user_id=user.id,
            profile_id=profile.id,
            server_id=profile.server_id,
            mikrotik_username="TEMP", # Placeholder
            mikrotik_password="TEMP",
            expiry_date=expiry,
            status='active',
            total_limit_bytes=float(profile.data_limit_gb) * 1024**3 if profile.data_limit_gb else 0,
            used_bytes=0
        )
        session.add(sub)
        await session.flush()
        
        # Now we have sub.id, generate unique username
        from vpn_bot.utils import to_base36
        mt_username = f"u{to_base36(sub.id)}"
        mt_password = ''.join(random.choices(string.ascii_letters + string.digits, k=8))
        
        sub.mikrotik_username = mt_username
        sub.mikrotik_password = mt_password

        prep = {
            "sub_id": sub.id,
            "user_db_id": user.id,
            "server_id": server.id,
            "mt_username": mt_username,
            "mt_password": mt_password,
            "profile_name": profile.name,
            "refund_amount": deduct_amount,
            "currency_unit": unit,
        }
        await session.commit()

    # 5. Create User in MikroTik (DB connection released)
    server = None
    async with AsyncSessionLocal() as session:
        server = await session.get(Server, prep["server_id"])
    if not server:
        await _rollback_ovpn_prep(
            prep["user_db_id"], prep["sub_id"], prep["refund_amount"], prep["currency_unit"]
        )
        return False, None, LanguageManager.get("buy.server_error")

    try:
        mgr = get_mikrotik_manager(server)
    except ValueError as cred_err:
        if "ENCRYPTION_KEY" in str(cred_err) or "decrypt" in str(cred_err).lower():
            logging.error(f"MikroTik credentials unavailable for server {server.name}: {cred_err}")
        await _rollback_ovpn_prep(
            prep["user_db_id"], prep["sub_id"], prep["refund_amount"], prep["currency_unit"]
        )
        return False, None, LanguageManager.get("buy.server_error")

    from vpn_bot.utils import run_mikrotik

    mt_success = False
    max_retries = 3
    for attempt in range(1, max_retries + 1):
        try:
            if attempt > 1 and progress_callback:
                await progress_callback(attempt)
            mt_success = await run_mikrotik(
                mgr.create_user,
                prep["mt_username"],
                prep["mt_password"],
                prep["profile_name"],
            )
            if mt_success:
                break
            logging.warning(
                f"MikroTik creation attempt {attempt} returned False for {prep['mt_username']}"
            )
        except Exception as mt_err:
            logging.error(f"MikroTik creation attempt {attempt} failed: {mt_err}")
        if attempt < max_retries:
            await asyncio.sleep(2)

    if not mt_success:
        await _rollback_ovpn_prep(
            prep["user_db_id"], prep["sub_id"], prep["refund_amount"], prep["currency_unit"]
        )
        return False, None, LanguageManager.get("buy.mt_error")

    async with AsyncSessionLocal() as session:
        sub = await session.get(Subscription, prep["sub_id"])
        if not sub:
            return False, None, LanguageManager.get("buy.error_load")
        await session.refresh(sub)
        return True, sub, None

async def checkout_wg_subscription(user_id: int, profile_id: int, context=None):
    """
    Executes a purchase transaction for a user and WG profile.
    Returns: (success, subscription, error_message)
    """
    # Reuse finalize_wg_purchase logic but adapted for this return format
    success = await finalize_wg_purchase(user_id, profile_id, context, is_tg_id=True)
    if success:
        # We need to return the sub, but finalize_wg_purchase doesn't return it easily.
        # Actually, let's just use finalize_wg_purchase as the main entry point.
        return True, "WG_DELIVERED", None
    else:
        return False, None, "Checkout failed."

async def finalize_wg_purchase(user_id: int, profile_id: int, context, is_tg_id: bool = True, progress_callback=None, coupon_id: int | None = None) -> bool:
    """Core logic to deduct money and create WG account. Now in user_features.py for shared use."""
    from vpn_bot.admin_sales_service import assert_new_purchase_capacity
    from vpn_bot.coupon_flow import coupon_id_from_context

    if coupon_id is None and context and hasattr(context, "user_data"):
        coupon_id = coupon_id_from_context(context)

    ok_cap, cap_msg = await assert_new_purchase_capacity("wg")
    if not ok_cap:
        async with AsyncSessionLocal() as session:
            if is_tg_id:
                u_res = await session.execute(select(User).where(User.telegram_id == user_id))
            else:
                u_res = await session.execute(select(User).where(User.id == user_id))
            user = u_res.scalars().first()
        if user and context and hasattr(context, "bot"):
            from vpn_bot.utils import ensure_telegram_text

            text = ensure_telegram_text(
                cap_msg,
                fallback_key="admin.sales.capacity_full_default",
            )
            try:
                await context.bot.send_message(user.telegram_id, text, parse_mode="Markdown")
            except Exception:
                pass
        logger.warning("WG Purchase blocked: sales capacity full")
        return False

    async with AsyncSessionLocal() as session:
        from vpn_bot.models import WireGuardProfile, WireGuardSubscription, WireGuardInterface, Server, Transaction
        from datetime import datetime, timedelta
        
        p_res = await session.execute(select(WireGuardProfile).where(WireGuardProfile.id == profile_id))
        profile = p_res.scalars().first()
        
        if is_tg_id:
            u_res = await session.execute(
                select(User).where(User.telegram_id == user_id).with_for_update()
            )
        else:
            u_res = await session.execute(
                select(User).where(User.id == user_id).with_for_update()
            )
        user = u_res.scalars().first()
        
        if not profile or not user: 
            logger.error(f"WG Purchase failed: Profile {profile_id} or User {user_id} not found.")
            return False

        if user.is_banned:
            logger.warning(f"WG Purchase blocked: user {user_id} is banned")
            return False

        terms_ok, terms_err = await assert_terms_accepted_for_checkout(user)
        if not terms_ok:
            if context and hasattr(context, "bot"):
                try:
                    await context.bot.send_message(user.telegram_id, terms_err, parse_mode="Markdown")
                except Exception:
                    pass
            logger.warning("WG Purchase blocked: terms not accepted")
            return False

        # 1. Deduct balance (with 100-unit mismatch safety margin)
        try:
            base, disc, p_price, unit, code_id = await _resolve_checkout_pricing(
                session, user, profile, is_wg=True, context="purchase_wg", coupon_id=coupon_id
            )
        except ValueError as exc:
            logger.warning("WG Purchase blocked: invalid coupon %s", exc)
            return False
        # Allow 100 unit mismatch (e.g. 48,900 instead of 49,000)
        safety_margin = 100 
        if user.wallet_balance + safety_margin < p_price:
            logger.warning(f"Insufficient balance for WG: {user.wallet_balance} (needed {p_price})")
            return False

        if user.wallet_balance < p_price and p_price > 0:
            logger.warning(f"Insufficient balance for WG: {user.wallet_balance} (needed {p_price})")
            return False
            
        desc = LanguageManager.get('wg.purchase_desc', name=profile.name)
        if code_id and disc > 0:
            dc = await session.get(DiscountCode, code_id)
            code_label = dc.code if dc else str(code_id)
            desc = LanguageManager.get('desc.purchase_discounted', name=profile.name, code=code_label)
        deduct_amount = p_price
        txn = await WalletManager._deduct_logic(
            session,
            user.id,
            deduct_amount,
            desc,
            discount_code_id=code_id,
            original_amount=base if code_id else None,
            discount_amount=disc if code_id else None,
        )
        if not txn:
            return False

        if code_id:
            from vpn_bot.discount_service import redeem_coupon_atomic
            await redeem_coupon_atomic(
                session,
                code_id=code_id,
                user_id=user.id,
                context="purchase_wg",
                original_amount=base,
                final_amount=p_price,
                discount_amount=disc,
                currency=unit,
                transaction_id=txn.id,
            )
            
        # 2. Select / Create Interface
        server_id = profile.server_id
        server = await session.get(Server, server_id) if server_id else None
        if not server:
            s_res = await session.execute(select(Server).where(Server.is_active == True))
            server = s_res.scalars().first()
            if not server: return False
            server_id = server.id

        # Find best interface (row lock + live active count prevents over-capacity)
        from vpn_bot.admin_wg_service import pick_wg_interface_for_purchase

        interface = await pick_wg_interface_for_purchase(session, server_id)
        
        mgr = get_mikrotik_manager(server)
        
        if not interface:
            # Creation logic with FULL INHERITANCE from existing interfaces
            wg_params = await asyncio.to_thread(mgr.find_available_wg_interface_params)
            if not wg_params: return False
            
            keys = await asyncio.to_thread(mgr.create_wg_interface, wg_params['name'], wg_params['listen_port'], wg_params['address'])
            if not keys: return False
            
            interface = WireGuardInterface(
                server_id=server_id,
                name=wg_params['name'],
                public_key=keys['public_key'],
                private_key=keys.get('private_key', 'managed-by-router'),
                listen_port=keys.get('listen_port', wg_params['listen_port']),
                address=wg_params['address'],
                is_active=True
            )
            
            # Inherit settings from first active interface on same server
            parent_res = await session.execute(
                select(WireGuardInterface).where(
                    WireGuardInterface.server_id == server_id,
                    WireGuardInterface.is_active == True
                ).order_by(WireGuardInterface.id.asc()).limit(1)
            )
            parent = parent_res.scalars().first()
            if parent:
                interface.dns = parent.dns
                interface.mtu = parent.mtu
                interface.keepalive = parent.keepalive
                interface.max_users = parent.max_users
                interface.upstream_interface = parent.upstream_interface
                interface.routing_mark = parent.routing_mark
                interface.nat_routing_mark = parent.nat_routing_mark
                interface.nat_dst_address = parent.nat_dst_address
                interface.nat_dst_address_list = parent.nat_dst_address_list
                interface.gateway = parent.gateway
                interface.route_table = parent.route_table
                interface.route_dst_address = parent.route_dst_address
                interface.route_distance = parent.route_distance
                interface.endpoint_host = parent.endpoint_host
            
            session.add(interface)
            await session.flush()
            
            from vpn_bot.admin_wg_service import _WG_MT_APPLY_TIMEOUT, _WG_MT_APPLY_RETRIES
            from vpn_bot.mt_session import run_mikrotik_for_server

            await run_mikrotik_for_server(
                server,
                mgr.sync_wg_interface_automation,
                name=interface.name,
                address=interface.address,
                upstream=interface.upstream_interface,
                routing_mark=interface.routing_mark,
                nat_routing_mark=interface.nat_routing_mark,
                nat_dst=interface.nat_dst_address or "127.0.0.1",
                nat_dst_list=interface.nat_dst_address_list,
                nat_dst_negate=bool(getattr(interface, "nat_dst_negate", True)),
                gateway=interface.gateway,
                route_table=interface.route_table,
                route_dst=interface.route_dst_address or "0.0.0.0/0",
                route_distance=interface.route_distance if interface.route_distance is not None else 1,
                listen_port=interface.listen_port,
                timeout=_WG_MT_APPLY_TIMEOUT,
                retries=_WG_MT_APPLY_RETRIES,
            )

        # 3. Create Peer keys
        priv_key, pub_key = generate_wg_keys()
        
        # 4. Assign IP to user (avoid reuse while expired peers remain on router)
        from vpn_bot.admin_wg_service import next_wg_client_ip

        user_ip = await next_wg_client_ip(session, interface)
        
        # 5. Create Subscription Record (Stage 1: Get ID)
        from vpn_bot.utils import utc_now as _utc_now
        expiry = _utc_now() + timedelta(days=profile.duration_days)
        sub = WireGuardSubscription(
            user_id=user.id,
            interface_id=interface.id,
            profile_id=profile.id,
            unique_identifier="pending", # Placeholder
            peer_public_key=pub_key,
            peer_private_key=priv_key,
            assigned_ip=user_ip,
            comment_text="pending",
            expiry_date=expiry,
            bytes_remaining=(profile.volume_gb or 0) * 1024**3,
            status="pending"
        )
        session.add(sub)
        await session.flush() # Get auto-increment ID

        # 6. Generate Short Unique ID
        # Approved strategy: WG- + Base36 of ID
        unique_id = f"WG-{to_base36(sub.id)}"
        comment = f"{unique_id} | {user.username or user.telegram_id} | {expiry.strftime('%Y-%m-%d')}"
        
        # 7. Add peer to MikroTik with Retries
        mt_success = False
        max_retries = 3
        for attempt in range(1, max_retries + 1):
            try:
                if attempt > 1 and progress_callback:
                    await progress_callback(attempt)
                    
                from vpn_bot.utils import run_mikrotik

                mt_success = await run_mikrotik(
                    mgr.add_wg_peer, interface.name, pub_key, f"{user_ip}/32", comment
                )
                if mt_success:
                    break
            except Exception as mt_err:
                logging.error(f"WireGuard peer creation attempt {attempt} failed: {mt_err}")
                
            if attempt < max_retries:
                await asyncio.sleep(2)

        if not mt_success:
            await session.rollback()
            return False
            
        # 8. Update Subscription with real unique_id
        sub.unique_identifier = unique_id
        sub.comment_text = comment
        sub.status = "active"
        
        # 8.1 Simple Queue
        if profile.rate_limit:
            from vpn_bot.utils import run_mikrotik

            await run_mikrotik(mgr.add_wg_queue, unique_id, f"{user_ip}/32", profile.rate_limit)
            
        from vpn_bot.admin_wg_service import sync_wg_interface_current_users

        await sync_wg_interface_current_users(session, interface.id)
        await session.commit()
        
        # Proactive: check if we should pre-create the next interface
        try:
            from vpn_bot.admin_wg_service import check_and_preemptively_create_interfaces
            asyncio.create_task(check_and_preemptively_create_interfaces())
        except Exception as preempt_err:
            logger.debug(f"Proactive WG interface check skipped: {preempt_err}")
        
        if context:
            from vpn_bot.wg_delivery import deliver_wg_config
            from vpn_bot.utils import ensure_telegram_text

            sub.interface = interface
            sub.profile = profile
            delivered = await deliver_wg_config(
                context.bot,
                user.telegram_id,
                sub,
                server,
                volume_gb=profile.volume_gb,
                send_purchase_success=True,
                show_main_menu=True,
            )
            if not delivered:
                try:
                    fail_text = ensure_telegram_text(
                        LanguageManager.get("buy.delivery_fail"),
                        fallback_key="buy.delivery_fail",
                    )
                    await context.bot.send_message(
                        user.telegram_id,
                        fail_text,
                        parse_mode="Markdown",
                    )
                except Exception as send_err:
                    logger.error("WG delivery_fail notify error: %s", send_err)

        return True

async def send_ovpn_file(bot, chat_id, subscription: Subscription, ovpn: OvpnConfig):
    username = subscription.mikrotik_username
    password = subscription.mikrotik_password
    
    from io import BytesIO
    f_data = ovpn.config_content.encode('utf-8')
    doc = BytesIO(f_data)
    doc.name = ovpn.filename
    
    label = ovpn.display_name or LanguageManager.get('config.ovpn_config_default')
    caption = LanguageManager.get('config.ovpn_caption',
        label=label,
        username=username,
        password=password
    )
    
    await bot.send_document(
        chat_id=chat_id,
        document=doc,
        caption=caption,
        parse_mode='Markdown'
    )

async def send_connection_info(bot, chat_id, subscription: Subscription, server: Server):
    username = subscription.mikrotik_username
    password = subscription.mikrotik_password
    
    conn_info_all = await get_admin_setting('connection_info', {})
    if not isinstance(conn_info_all, dict):
        conn_info_all = {}
        
    server_id_str = str(server.id) if server else "0"
    s_info = conn_info_all.get(server_id_str, {
        'l2tp': {'ip': server.host if server else LanguageManager.get('common.unknown'), 'secret': '123456', 'version': 'l2tp_v2'},
        'sstp': {'ip': server.host if server else LanguageManager.get('common.unknown'), 'port': '443'}
    })
    
    l2tp_host = s_info['l2tp'].get('ip') or (server.host if server else LanguageManager.get('common.unknown'))
    l2tp_port = s_info['l2tp'].get('port', '1701')
    l2tp_secret = s_info['l2tp'].get('secret', '123456')
    
    sstp_host = s_info['sstp'].get('ip') or (server.host if server else LanguageManager.get('common.unknown'))
    sstp_port = s_info['sstp'].get('port', '443')
    
    l2tp_version = s_info['l2tp'].get('version', 'l2tp_v2')
    l2tp_title = LanguageManager.get(f'config.{l2tp_version}_title')
    l2tp_port_line = LanguageManager.get('config.l2tp_port_line', port=l2tp_port) if l2tp_version == 'l2tp_v3' else ""
    
    conn_info = LanguageManager.get('config.conn_info',
        l2tp_title=l2tp_title,
        l2tp_host=l2tp_host,
        l2tp_port_line=l2tp_port_line,
        l2tp_secret=l2tp_secret,
        sstp_host=sstp_host,
        sstp_port=sstp_port,
        username=username,
        password=password
    )
    await bot.send_message(chat_id, conn_info, parse_mode='Markdown')

async def send_config_files(bot, chat_id, subscription: Subscription, server: Server, session):
    """Old helper, now sends all available configs + info."""
    # 1. Connection Info
    await send_connection_info(bot, chat_id, subscription, server)
    
    # 2. All OVPNs
    res_conf = await session.execute(select(OvpnConfig).where(OvpnConfig.server_id == server.id))
    configs = res_conf.scalars().all()
    
    if configs:
        for ovpn in configs:
            await send_ovpn_file(bot, chat_id, subscription, ovpn)
    else:
        await bot.send_message(chat_id, LanguageManager.get('config.no_ovpn'))
