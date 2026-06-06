"""F1–F6: White-box tests for previously-missing auth guards.

Covers:
  F1 — export_sales_csv blocked for non-admin
  F2 — export_sales_csv blocked for admin without PERM_REPORTS
  F3 — confirm_renewal (OVPN) blocked for attacker (IDOR)
  F4 — confirm_wg_renewal blocked for attacker (IDOR)
  F5 — ticket reply_to_ticket_start blocked for non-owner
  F6 — arbitrary topup amount rejected
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.security.helpers.security_harness import (
    UserSecurityDriver,
    _callback_update,
    bind_bot,
    build_user_application,
)
from tests.helpers.admin_e2e_harness import RecordingFakeBot
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import (
    Admin,
    Subscription,
    Ticket,
    WireGuardSubscription,
)
from vpn_bot.utils import LanguageManager

pytestmark = [pytest.mark.security, pytest.mark.db]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _cb(tg_id: int, data: str):
    return _callback_update(tg_id, tg_id, data)


# ---------------------------------------------------------------------------
# F1 / F2 — export_sales_csv auth
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_f1_non_admin_cannot_export_csv(attacker_user):
    """A regular user tapping report_export_sales must receive an access-denied response."""
    app, bot = await build_user_application()
    driver = UserSecurityDriver(app, bot, attacker_user.telegram_id)
    with patch("vpn_bot.admin_report_service.get_sales_report_data", new_callable=AsyncMock) as mock_q:
        await driver.tap("report_export_sales")
        mock_q.assert_not_awaited()

    # No CSV document should have been sent
    sent_docs = [c for c in bot.calls if c.method == "send_document"]
    assert not sent_docs, "Non-admin received CSV document"


@pytest.mark.asyncio
async def test_f2_limited_admin_without_reports_perm_cannot_export_csv(attacker_user):
    """A DB admin whose permissions_json does NOT include PERM_REPORTS is denied."""
    from vpn_bot.admin_permissions import PERM_TICKETS  # excluded from reports
    async with AsyncSessionLocal() as session:
        adm = Admin(
            telegram_id=attacker_user.telegram_id,
            permissions_json=json.dumps([PERM_TICKETS]),
        )
        session.add(adm)
        await session.commit()

    app, bot = await build_user_application()
    driver = UserSecurityDriver(app, bot, attacker_user.telegram_id)
    with patch("vpn_bot.admin_report_service.get_sales_report_data", new_callable=AsyncMock) as mock_q:
        await driver.tap("report_export_sales")
        mock_q.assert_not_awaited()

    sent_docs = [c for c in bot.calls if c.method == "send_document"]
    assert not sent_docs, "Admin without PERM_REPORTS received CSV"

    # Cleanup
    async with AsyncSessionLocal() as session:
        row = await session.get(Admin, adm.id)
        if row:
            await session.delete(row)
            await session.commit()


# ---------------------------------------------------------------------------
# F3 — OVPN renew confirm IDOR
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_f3_attacker_cannot_renew_victim_ovpn_sub(
    victim_ovpn_sub, attacker_user, mock_mikrotik
):
    """Attacker calling renew_confirm_<victim_sub_id> must be denied, wallet unchanged."""
    async with AsyncSessionLocal() as session:
        row = await session.get(Subscription, victim_ovpn_sub.id)
        original_expiry = row.expiry_date

    app, bot = await build_user_application()
    driver = UserSecurityDriver(app, bot, attacker_user.telegram_id)

    with patch("vpn_bot.wallet_manager.WalletManager._deduct_logic", new_callable=AsyncMock) as mock_deduct:
        await driver.tap(f"renew_confirm_{victim_ovpn_sub.id}")
        mock_deduct.assert_not_awaited()

    # Victim's expiry must be unchanged
    async with AsyncSessionLocal() as session:
        row = await session.get(Subscription, victim_ovpn_sub.id)
        assert row.expiry_date == original_expiry, "Victim's OVPN expiry was modified by attacker"


# ---------------------------------------------------------------------------
# F4 — WG renew confirm IDOR
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_f4_attacker_cannot_renew_victim_wg_sub(
    victim_wg_sub, attacker_user, mock_mikrotik
):
    """Attacker calling renew_wg_confirm_<victim_sub_id> must be denied."""
    async with AsyncSessionLocal() as session:
        row = await session.get(WireGuardSubscription, victim_wg_sub.id)
        original_expiry = row.expiry_date

    app, bot = await build_user_application()
    driver = UserSecurityDriver(app, bot, attacker_user.telegram_id)

    with patch("vpn_bot.wallet_manager.WalletManager._deduct_logic", new_callable=AsyncMock) as mock_deduct:
        await driver.tap(f"renew_wg_confirm_{victim_wg_sub.id}")
        mock_deduct.assert_not_awaited()

    async with AsyncSessionLocal() as session:
        row = await session.get(WireGuardSubscription, victim_wg_sub.id)
        assert row.expiry_date == original_expiry, "Victim's WG expiry was modified by attacker"


# ---------------------------------------------------------------------------
# F5 — Ticket reply IDOR
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_f5_attacker_cannot_reply_to_victims_ticket(victim_user, attacker_user):
    """Attacker calling ticket_reply_<victim_ticket_id> must be blocked."""
    # Create a ticket owned by victim
    async with AsyncSessionLocal() as session:
        ticket = Ticket(
            user_id=victim_user.id,
            subject="Victim ticket",
            status="open",
        )
        session.add(ticket)
        await session.commit()
        await session.refresh(ticket)
        ticket_id = ticket.id

    app, bot = await build_user_application()
    driver = UserSecurityDriver(app, bot, attacker_user.telegram_id)

    with patch(
        "vpn_bot.admin_ticket_service.add_ticket_message", new_callable=AsyncMock
    ) as mock_add:
        await driver.tap(f"ticket_reply_{ticket_id}")
        # If add_ticket_message was called with the attacker's data, that is a bug.
        # The new guard must prevent TICKET_REPLY state from being entered at all.
        for call in mock_add.call_args_list:
            args = call.args
            # sender_type should never be 'user' with attacker's id on victim's ticket
            if len(args) >= 3 and args[2] == "user":
                pytest.fail("add_ticket_message was called for attacker on victim's ticket")

    # Cleanup
    async with AsyncSessionLocal() as session:
        row = await session.get(Ticket, ticket_id)
        if row:
            await session.delete(row)
            await session.commit()


# ---------------------------------------------------------------------------
# F6 — Arbitrary topup amount rejected
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_f6_arbitrary_topup_amount_rejected(attacker_user):
    """A crafted topup_999999999 callback must be rejected before payment logic runs."""
    app, bot = await build_user_application()
    driver = UserSecurityDriver(app, bot, attacker_user.telegram_id)

    with patch(
        "vpn_bot.bot_handler._wallet_topup_pricing", new_callable=AsyncMock
    ) as mock_price:
        await driver.tap("topup_999999999")
        mock_price.assert_not_awaited()
