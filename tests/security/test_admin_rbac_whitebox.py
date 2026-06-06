"""A1–A8: Admin RBAC white-box security tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from telegram.ext import ApplicationHandlerStop, ConversationHandler

from tests.helpers.admin_e2e_harness import AdminE2EDriver, build_admin_application
from tests.security.helpers.security_harness import UserSecurityDriver, _callback_update, _text_update, build_user_application
from vpn_bot.admin_permissions import PERM_RECEIPTS, admin_callback_access_gate, has_perm_in_set
from vpn_bot.admin_management import secret_keyword_listener
from vpn_bot.utils import LanguageManager

pytestmark = [pytest.mark.security, pytest.mark.db]

REGULAR_USER_ID = 910_001
LIMITED_ADMIN_ID = 910_002


@pytest.mark.asyncio
async def test_a1_non_admin_admin_start_denied():
    app, bot, _ = await build_admin_application(include_user_handlers=False)
    update = _callback_update(REGULAR_USER_ID, REGULAR_USER_ID, "admin_start")
    update.set_bot(bot)
    update.callback_query.set_bot(bot)
    update.callback_query.message.set_bot(bot)

    with patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=False):
        with pytest.raises(ApplicationHandlerStop):
            await admin_callback_access_gate(update, MagicMock())


@pytest.mark.asyncio
async def test_a2_limited_admin_list_servers_denied():
    app, bot, _ = await build_admin_application(include_user_handlers=False)
    limited = {PERM_RECEIPTS}

    async def _has(_uid, perm):
        return has_perm_in_set(limited, perm)

    update = _callback_update(LIMITED_ADMIN_ID, LIMITED_ADMIN_ID, "list_servers")
    update.set_bot(bot)
    update.callback_query.set_bot(bot)
    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
    ):
        with pytest.raises(ApplicationHandlerStop):
            await admin_callback_access_gate(update, MagicMock())


@pytest.mark.asyncio
async def test_a3_limited_admin_view_receipt_allowed(db_user, mock_mikrotik):
    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.models import PaymentReceipt

    async with AsyncSessionLocal() as session:
        receipt = PaymentReceipt(
            user_id=db_user.id,
            amount=10_000.0,
            receipt_file_id="rbac_test",
            status="pending",
        )
        session.add(receipt)
        await session.commit()
        await session.refresh(receipt)
        rid = receipt.id

    app, bot, _ = await build_admin_application(include_user_handlers=False)
    limited = {PERM_RECEIPTS}

    async def _has(_uid, perm):
        return has_perm_in_set(limited, perm)

    driver = AdminE2EDriver(app, bot, LIMITED_ADMIN_ID, permissions=limited, super_admin=False)
    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_permissions.has_admin_perm", new_callable=AsyncMock, side_effect=_has),
    ):
        await driver.tap(f"view_receipt_{rid}")

    assert any(c.method == "send_photo" or c.method == "edit_message_text" or c.method == "send_document" for c in bot.calls) or any(
        c.method == "answer_callback_query" for c in bot.calls
    )


@pytest.mark.asyncio
async def test_a4_non_super_backup_import_denied():
    app, bot, _ = await build_admin_application(include_user_handlers=False)
    update = _callback_update(LIMITED_ADMIN_ID, LIMITED_ADMIN_ID, "backup_import")
    update.set_bot(bot)
    update.callback_query.set_bot(bot)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_management.is_super_admin", new_callable=AsyncMock, return_value=False),
    ):
        with pytest.raises(ApplicationHandlerStop):
            await admin_callback_access_gate(update, MagicMock())


@pytest.mark.asyncio
async def test_a5_non_super_admin_mgmt_denied():
    app, bot, _ = await build_admin_application(include_user_handlers=False)
    update = _callback_update(LIMITED_ADMIN_ID, LIMITED_ADMIN_ID, "admin_mgmt_menu")
    update.set_bot(bot)
    update.callback_query.set_bot(bot)

    with (
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=True),
        patch("vpn_bot.admin_management.is_super_admin", new_callable=AsyncMock, return_value=False),
    ):
        with pytest.raises(ApplicationHandlerStop):
            await admin_callback_access_gate(update, MagicMock())


@pytest.mark.asyncio
async def test_a6_non_admin_receipt_approve_denied():
    update = _callback_update(REGULAR_USER_ID, REGULAR_USER_ID, "receipt_approve_1")
    with patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=False):
        with pytest.raises(ApplicationHandlerStop):
            await admin_callback_access_gate(update, MagicMock())


@pytest.mark.asyncio
async def test_a8_secret_keyword_non_admin_no_panel():
    app, bot = await build_user_application()
    update = _text_update(REGULAR_USER_ID, REGULAR_USER_ID, "AdminPanel")
    context = MagicMock()

    with (
        patch("vpn_bot.admin_settings.get_admin_setting", new_callable=AsyncMock, return_value="AdminPanel"),
        patch("vpn_bot.admin_management.is_user_admin", new_callable=AsyncMock, return_value=False),
    ):
        result = await secret_keyword_listener(update, context)

    assert result is None
    assert not any(c.method == "send_message" and "admin" in str(c.kwargs.get("text", "")).lower() for c in bot.calls)
