"""Telegram wallet + receipt helpers for live WG E2E tests."""

from __future__ import annotations

from sqlalchemy import select

from tests.live.helpers.wg_workflow_live import fetch_latest_wg_sub, load_sub
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import PaymentReceipt, Transaction, User, WireGuardSubscription
from vpn_bot.settings_utils import get_admin_setting, set_admin_setting

E2E_RECEIPT_PREFIX = "intg_e2e_wg_stress_"
E2E_PHONE = "+989121112233"


async def prepare_broke_user(user: User) -> User:
    """Zero wallet and set phone so registration gate does not block flows."""
    async with AsyncSessionLocal() as session:
        row = await session.get(User, user.id)
        row.wallet_balance = 0.0
        row.phone_number = E2E_PHONE
        await session.commit()
        await session.refresh(row)
        return row


async def ensure_e2e_payment_settings(plan_price: float) -> None:
    """Ensure payment cards and wallet presets support the test plan price."""
    price_int = int(plan_price)
    cards = await get_admin_setting("payment_cards", [])
    if not cards:
        await set_admin_setting(
            "payment_cards",
            [
                {
                    "number": "6037-9912-3456-7890",
                    "holder": "INTG Test",
                    "bank": "Test Bank",
                }
            ],
        )

    presets = await get_admin_setting("wallet_presets", [5, 10, 20])
    if price_int not in [int(p) for p in presets]:
        merged = sorted({int(p) for p in presets} | {price_int})
        await set_admin_setting("wallet_presets", merged)


async def fetch_pending_receipt(
    user_id: int,
    *,
    plan_id: int | None = None,
    reference_prefix: str = E2E_RECEIPT_PREFIX,
) -> PaymentReceipt | None:
    async with AsyncSessionLocal() as session:
        q = (
            select(PaymentReceipt)
            .where(
                PaymentReceipt.user_id == user_id,
                PaymentReceipt.status == "pending",
            )
            .order_by(PaymentReceipt.id.desc())
        )
        if plan_id is not None:
            q = q.where(PaymentReceipt.plan_id == plan_id)
        if reference_prefix:
            q = q.where(PaymentReceipt.receipt_file_id.like(f"{reference_prefix}%"))
        res = await session.execute(q)
        return res.scalars().first()


async def submit_text_receipt(user_driver, reference: str) -> None:
    """Send text receipt reference and confirm."""
    await user_driver.send_text(reference)
    assert not user_driver.errors, user_driver.errors
    await user_driver.tap("receipt_yes")
    assert not user_driver.errors, user_driver.errors


async def approve_receipt_via_admin(admin_driver, receipt_id: int) -> None:
    await admin_driver.tap(f"receipt_approve_{receipt_id}")
    assert not admin_driver.errors, admin_driver.errors


def _fast_pay_callback(callbacks: list[str]) -> str | None:
    for cb in callbacks:
        if cb.startswith("fast_pay_wg_"):
            return cb
    return None


async def reset_telegram_session(user_driver) -> None:
    """Leave wallet/buy ConversationHandler state before standalone callbacks."""
    user_driver.clear_processing_lock()
    await user_driver.send_command("start")
    for _ in range(3):
        callbacks = user_driver.callbacks_on_screen()
        if "main_menu" in callbacks:
            await user_driver.tap("main_menu")
            assert not user_driver.errors, user_driver.errors
            break
        await user_driver.send_command("start")
    assert not user_driver.errors, user_driver.errors


def _last_bot_text(user_driver) -> str:
    for rec in reversed(user_driver.bot.calls):
        text = rec.kwargs.get("text")
        if text:
            return str(text)
    return ""


async def run_wallet_wg_purchase_e2e(
    user_driver,
    admin_driver,
    user: User,
    profile,
) -> WireGuardSubscription:
    """
    Broke user → buy_wg → fast_pay → text receipt → admin approve → active sub.
    """
    await prepare_broke_user(user)
    await ensure_e2e_payment_settings(profile.price_toman)

    await user_driver.send_command("start")
    await user_driver.tap("buy_wg")
    assert not user_driver.errors, user_driver.errors

    await user_driver.tap(f"buy_wg_plan_{profile.id}")
    assert not user_driver.errors, user_driver.errors

    fast_pay = _fast_pay_callback(user_driver.callbacks_on_screen())
    assert fast_pay is not None, user_driver.callbacks_on_screen()
    await user_driver.tap(fast_pay)
    assert not user_driver.errors, user_driver.errors

    ref = f"{E2E_RECEIPT_PREFIX}purchase_{profile.id}"
    await submit_text_receipt(user_driver, ref)

    receipt = await fetch_pending_receipt(user.id, plan_id=profile.id, reference_prefix=ref)
    assert receipt is not None
    assert receipt.is_wireguard is True

    await approve_receipt_via_admin(admin_driver, receipt.id)

    sub = await fetch_latest_wg_sub(user.id, profile.id)
    assert sub is not None
    assert sub.status == "active"

    await reset_telegram_session(user_driver)
    return sub


async def run_wallet_wg_renewal_e2e(
    user_driver,
    admin_driver,
    user: User,
    sub: WireGuardSubscription,
    price: float,
) -> WireGuardSubscription:
    """Top up via wallet receipt then renew_wg_confirm from Telegram."""
    refreshed = await load_sub(sub.id)
    assert refreshed is not None
    sub = refreshed

    await prepare_broke_user(user)
    await ensure_e2e_payment_settings(price)
    await reset_telegram_session(user_driver)

    user_driver.clear_processing_lock()
    await user_driver.tap(f"renew_wg_{sub.id}")
    assert not user_driver.errors, user_driver.errors

    if f"renew_wg_confirm_{sub.id}" not in user_driver.callbacks_on_screen():
        callbacks = user_driver.callbacks_on_screen()
        assert "wallet_menu" in callbacks, (
            f"callbacks={callbacks} last_text={_last_bot_text(user_driver)!r}"
        )
        await user_driver.tap("wallet_menu")
        price_int = int(price)
        topup_cb = f"topup_{price_int}"
        if topup_cb not in user_driver.callbacks_on_screen():
            topup_cb = next(
                (c for c in user_driver.callbacks_on_screen() if c.startswith("topup_")),
                None,
            )
        assert topup_cb is not None, user_driver.callbacks_on_screen()
        await user_driver.tap(topup_cb)
        assert not user_driver.errors, user_driver.errors

        ref = f"{E2E_RECEIPT_PREFIX}renew_{sub.id}"
        await submit_text_receipt(user_driver, ref)

        receipt = await fetch_pending_receipt(user.id, plan_id=None, reference_prefix=ref)
        assert receipt is not None
        await approve_receipt_via_admin(admin_driver, receipt.id)

        await reset_telegram_session(user_driver)
        await user_driver.tap(f"renew_wg_{sub.id}")
        assert not user_driver.errors, user_driver.errors

    assert f"renew_wg_confirm_{sub.id}" in user_driver.callbacks_on_screen(), (
        f"callbacks={user_driver.callbacks_on_screen()} text={_last_bot_text(user_driver)!r}"
    )
    await user_driver.tap(f"renew_wg_confirm_{sub.id}")
    assert not user_driver.errors, user_driver.errors

    refreshed = await fetch_latest_wg_sub(user.id, sub.profile_id)
    assert refreshed is not None
    assert refreshed.status == "active"
    return refreshed


async def assert_wallet_has_tx(user_id: int, *, min_tx_count: int = 1) -> None:
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(Transaction)
            .where(Transaction.user_id == user_id)
            .order_by(Transaction.id.desc())
        )
        rows = list(res.scalars().all())
        assert len(rows) >= min_tx_count, f"expected >= {min_tx_count} transactions"
