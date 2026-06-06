"""End-to-end discount / coupon system tests (user flow, admin CRUD, security, regression)."""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from contextlib import contextmanager

import pytest

pytest_plugins = ["tests.db.discount_conftest", "tests.conftest_db"]

from vpn_bot.admin_discount import (
    DISCOUNT_LIST_PAGE_SIZE,
    DISCOUNT_NAME,
    _admin_discount_main_menu_dispatch,
    discount_codes_menu,
    discount_page_next,
    discount_page_prev,
    discount_type_selected,
    receive_discount_edit_value,
    receive_discount_expiry_value,
)
from vpn_bot.admin_discount_service import (
    create_discount_code,
    delete_discount_code,
    get_discount_code,
    list_discount_codes,
    toggle_discount_code,
    update_discount_code,
)
from vpn_bot.admin_permissions import PERM_RECEIPTS, has_perm_in_set, permission_for_callback
from vpn_bot.coupon_flow import (
    COUPON_ENTRY,
    COUPON_PROMPT,
    clear_coupon_flow,
    coupon_enter_start,
    coupon_id_from_context,
    coupon_skip,
    global_receive_coupon_code,
    receive_coupon_code,
)
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.discount_expiry import EXPIRY_UNIT_LIMITS, compute_valid_until, validate_expiry_amount
from vpn_bot.discount_service import (
    COUPON_SESSION_TTL_SECONDS,
    PREVIEW_FAIL_LIMIT,
    PREVIEW_FAIL_WINDOW_SECONDS,
    active_coupon_from_session,
    apply_coupon_to_amount,
    check_preview_rate_limit,
    compute_discounted_amount,
    normalize_code,
    redeem_coupon_atomic,
    store_active_coupon,
    validate_coupon_eligibility,
    validate_coupon_for_use,
)
from vpn_bot.models import DiscountCode
from vpn_bot.user_features import checkout_subscription, finalize_wg_purchase
from vpn_bot.utils import LanguageManager

pytestmark = [pytest.mark.db]


@pytest.fixture(autouse=True)
def _fa_locale():
    LanguageManager.load_locales()
    LanguageManager._current_lang = "fa"
    yield
    LanguageManager._current_lang = "en"


def _text_update(user_id: int, text: str, *, message_id: int = 100):
    update = MagicMock()
    user = MagicMock()
    user.id = user_id
    update.effective_user = user
    update.message = AsyncMock()
    update.message.reply_text = AsyncMock()
    update.message.text = text
    update.message.message_id = message_id
    update.callback_query = None
    return update


def _callback_update(user_id: int, data: str):
    update = MagicMock()
    user = MagicMock()
    user.id = user_id
    update.effective_user = user
    update.callback_query = AsyncMock()
    update.callback_query.data = data
    update.callback_query.answer = AsyncMock()
    update.callback_query.edit_message_text = AsyncMock()
    update.message = None
    return update


def _admin_patches():
    @contextmanager
    def _ctx():
        with (
            patch("vpn_bot.admin_management.is_user_admin", AsyncMock(return_value=True)),
            patch("vpn_bot.admin_permissions.has_admin_perm", AsyncMock(return_value=True)),
        ):
            yield

    return _ctx()


# ---------------------------------------------------------------------------
# Section 1 — expiry helpers
# ---------------------------------------------------------------------------


class TestDiscountExpiryHelpers:
    @pytest.mark.parametrize(
        "unit,amount,expected",
        [
            ("minute", 1, True),
            ("minute", 60, True),
            ("minute", 0, False),
            ("minute", 61, False),
            ("hour", 24, True),
            ("hour", 25, False),
            ("day", 30, True),
            ("day", 31, False),
            ("week", 4, True),
            ("week", 5, False),
            ("month", 12, True),
            ("month", 13, False),
            ("year", 5, True),
            ("year", 6, False),
            ("invalid", 1, False),
        ],
    )
    def test_validate_expiry_amount(self, unit, amount, expected):
        assert validate_expiry_amount(unit, amount) is expected

    def test_compute_valid_until_minute(self):
        base = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
        result = compute_valid_until("minute", 30, now=base)
        assert result == base + timedelta(minutes=30)

    def test_compute_valid_until_week(self):
        base = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
        result = compute_valid_until("week", 2, now=base)
        assert result == base + timedelta(weeks=2)

    def test_compute_valid_until_month(self):
        base = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
        result = compute_valid_until("month", 1, now=base)
        assert result == base + timedelta(days=30)

    def test_compute_valid_until_rejects_invalid(self):
        with pytest.raises(ValueError, match="invalid_expiry_amount"):
            compute_valid_until("minute", 0)

    def test_all_units_have_limits(self):
        for unit in ("minute", "hour", "day", "week", "month", "year"):
            lo, hi = EXPIRY_UNIT_LIMITS[unit]
            assert lo >= 1
            assert hi >= lo


# ---------------------------------------------------------------------------
# Section 2 — coupon session helpers
# ---------------------------------------------------------------------------


class TestCouponSessionHelpers:
    def test_clear_coupon_flow_removes_all_keys(self):
        user_data = {
            "_coupon_scope": "buy_wg",
            "_coupon_resume": "buy_wg_plans",
            "_coupon_waiting_code": True,
            "_coupon_fail": {"count": 2, "window_start": time.time()},
            "active_coupon": {
                "code_id": 1,
                "code": "X",
                "scope": "buy_wg",
                "validated_at": time.time(),
            },
            "_coupon_handled_msg_id": 99,
            "_coupon_last_next_state": 3,
        }
        clear_coupon_flow(user_data)
        assert user_data == {}

    def test_coupon_id_from_context_rejects_scope_mismatch(self):
        context = MagicMock()
        context.user_data = {
            "_coupon_scope": "buy_wg",
            "active_coupon": {
                "code_id": 1,
                "code": "X",
                "scope": "buy_ovpn",
                "validated_at": time.time(),
            },
        }
        assert coupon_id_from_context(context) is None

    def test_coupon_id_from_context_accepts_matching_scope(self):
        context = MagicMock()
        context.user_data = {
            "_coupon_scope": "buy_wg",
            "active_coupon": {
                "code_id": 7,
                "code": "WG10",
                "scope": "buy_wg",
                "validated_at": time.time(),
            },
        }
        assert coupon_id_from_context(context) == 7


# ---------------------------------------------------------------------------
# Section 3 — user coupon prompt flow
# ---------------------------------------------------------------------------


class TestUserCouponPromptFlow:
    @pytest.mark.asyncio
    async def test_coupon_skip_resumes_wg_plans(self, db_user):
        update = MagicMock()
        update.callback_query = AsyncMock()
        update.callback_query.answer = AsyncMock()
        context = MagicMock()
        context.user_data = {
            "_coupon_scope": "buy_wg",
            "_coupon_resume": "buy_wg_plans",
            "active_coupon": {
                "code_id": 1,
                "code": "X",
                "scope": "buy_wg",
                "validated_at": time.time(),
            },
        }

        with patch(
            "vpn_bot.bot_handler.buy_wg_service_show_plans",
            AsyncMock(return_value=0),
        ) as resume:
            state = await coupon_skip(update, context)

        resume.assert_awaited_once()
        assert active_coupon_from_session(context.user_data) is None
        assert state == 0

    @pytest.mark.asyncio
    async def test_coupon_enter_moves_to_entry_state(self):
        update = MagicMock()
        update.callback_query = AsyncMock()
        update.callback_query.answer = AsyncMock()
        update.callback_query.edit_message_text = AsyncMock()
        context = MagicMock()
        context.user_data = {"_coupon_scope": "buy_wg", "_coupon_resume": "buy_wg_plans"}

        state = await coupon_enter_start(update, context)

        assert state == COUPON_ENTRY
        assert context.user_data["_coupon_waiting_code"] is True
        update.callback_query.edit_message_text.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_coupon_enter_valid_code_stores_and_resumes(self, db_user, unique_code):
        row = await create_discount_code(
            name="UserFlow",
            code=unique_code,
            discount_type="percent",
            value=15,
            currency_unit=None,
            max_uses_per_user=3,
        )
        update = _text_update(db_user.telegram_id, unique_code)
        context = MagicMock()
        context.user_data = {
            "_coupon_scope": "buy_wg",
            "_coupon_resume": "buy_wg_plans",
            "_coupon_waiting_code": True,
        }

        with patch("vpn_bot.bot_handler.buy_wg_service_show_plans", AsyncMock(return_value=0)):
            state = await receive_coupon_code(update, context)

        coupon = active_coupon_from_session(context.user_data)
        assert coupon is not None
        assert coupon["code_id"] == row.id
        assert coupon["code"] == unique_code
        assert state == 0

    @pytest.mark.asyncio
    async def test_coupon_enter_expired_code_stays_in_entry(self, db_user, unique_code):
        past = datetime.now(timezone.utc) - timedelta(minutes=30)
        await create_discount_code(
            name="ExpiredUser",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
            valid_until=past,
        )
        update = _text_update(db_user.telegram_id, unique_code)
        context = MagicMock()
        context.user_data = {
            "_coupon_scope": "buy_wg",
            "_coupon_waiting_code": True,
        }

        state = await receive_coupon_code(update, context)

        assert state == COUPON_ENTRY
        assert active_coupon_from_session(context.user_data) is None
        reply = update.message.reply_text.await_args
        body = (reply.kwargs.get("text") if reply.kwargs else "") or (reply.args[0] if reply.args else "")
        assert "منقضی" in body

    @pytest.mark.asyncio
    async def test_coupon_preview_rate_limit(self, db_user):
        update = _text_update(db_user.telegram_id, "BADCODE1")
        context = MagicMock()
        context.user_data = {
            "_coupon_scope": "buy_wg",
            "_coupon_waiting_code": True,
            "_coupon_fail": {"count": PREVIEW_FAIL_LIMIT, "window_start": time.time()},
        }

        state = await receive_coupon_code(update, context)

        assert state == COUPON_ENTRY
        reply = update.message.reply_text.await_args
        body = (reply.kwargs.get("text") if reply.kwargs else "") or (reply.args[0] if reply.args else "")
        assert "تلاش" in body or "rate" in body.lower()

    @pytest.mark.asyncio
    async def test_coupon_enter_dedupes_duplicate_message(self, db_user, unique_code):
        await create_discount_code(
            name="Dedup",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )
        update = _text_update(db_user.telegram_id, unique_code, message_id=4242)
        context = MagicMock()
        context.user_data = {
            "_coupon_scope": "buy_wg",
            "_coupon_resume": "buy_wg_plans",
            "_coupon_waiting_code": True,
        }

        with patch("vpn_bot.bot_handler.buy_wg_service_show_plans", AsyncMock(return_value=0)) as resume:
            first = await receive_coupon_code(update, context)
            second = await receive_coupon_code(update, context)

        assert first == 0
        assert second == 0
        resume.assert_awaited_once()
        assert update.message.reply_text.await_count == 1


# ---------------------------------------------------------------------------
# Section 4 — DB integration: checkout workflow
# ---------------------------------------------------------------------------


class TestDiscountCheckoutWorkflow:
    @pytest.mark.asyncio
    async def test_wg_percent_purchase_applies_discount(self, db_user, db_wg_profile, mock_mikrotik, unique_code):
        row = await create_discount_code(
            name="TenOff",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=2,
        )
        async with AsyncSessionLocal() as session:
            pricing = await apply_coupon_to_amount(
                session,
                base_amount=float(db_wg_profile.price_toman),
                currency="TOMAN",
                coupon_id=row.id,
                user_id=db_user.id,
                context="purchase_wg",
            )
        assert pricing is not None
        assert pricing.discount_amount > 0
        assert pricing.final_amount < pricing.base_amount

        ok = await finalize_wg_purchase(
            db_user.telegram_id, db_wg_profile.id, context=None, coupon_id=row.id
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_wg_fixed_toman_purchase(self, db_user, db_wg_profile, mock_mikrotik, unique_code):
        row = await create_discount_code(
            name="Fixed",
            code=unique_code,
            discount_type="fixed",
            value=5000,
            currency_unit="TOMAN",
            max_uses_per_user=1,
        )
        ok = await finalize_wg_purchase(
            db_user.telegram_id, db_wg_profile.id, context=None, coupon_id=row.id
        )
        assert ok is True

    @pytest.mark.asyncio
    async def test_skip_coupon_full_price_ovpn(self, db_user, db_ovpn_profile, mock_mikrotik):
        ok, sub, _ = await checkout_subscription(
            db_user.telegram_id, db_ovpn_profile.id, coupon_id=None
        )
        assert ok is True
        assert sub is not None

    @pytest.mark.asyncio
    async def test_inactive_coupon_rejected_at_checkout(self, db_user, db_ovpn_profile, mock_mikrotik, unique_code):
        row = await create_discount_code(
            name="Inactive",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )
        await toggle_discount_code(row.id)
        ok, _, _ = await checkout_subscription(
            db_user.telegram_id, db_ovpn_profile.id, coupon_id=row.id
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_concurrent_checkout_single_redemption(
        self, db_user, db_ovpn_profile, mock_mikrotik, unique_code
    ):
        row = await create_discount_code(
            name="Race",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )

        async def attempt():
            return await checkout_subscription(
                db_user.telegram_id, db_ovpn_profile.id, coupon_id=row.id
            )

        results = await asyncio.gather(attempt(), attempt(), return_exceptions=True)
        successes = sum(1 for r in results if isinstance(r, tuple) and r[0] is True)
        assert successes == 1

    def test_compute_percent_100_zeroes_price(self):
        code = DiscountCode(discount_type="percent", value=100, currency_unit=None)
        final, disc = compute_discounted_amount(50_000, code, "TOMAN")
        assert final == 0
        assert disc == 50_000


# ---------------------------------------------------------------------------
# Section 5 — admin E2E: discount CRUD
# ---------------------------------------------------------------------------


class TestAdminDiscountCrud:
    @pytest.mark.asyncio
    async def test_admin_discount_wizard_create_e2e(self, unique_code):
        from tests.helpers.admin_e2e_harness import AdminE2EDriver, build_admin_application

        app, bot, _ = await build_admin_application(include_user_handlers=False)
        driver = AdminE2EDriver(
            app,
            bot,
            admin_user_id=800003,
            permissions={"sales", "sales.discounts"},
        )
        await driver.open_admin_menu()
        driver.clear_processing_lock()
        await driver.tap("sales_mgmt_menu")
        driver.clear_processing_lock()
        await driver.tap("discount_codes_menu")
        driver.clear_processing_lock()
        await driver.tap("discount_add")
        driver.clear_processing_lock()
        await driver.tap("discount_type_percent")
        driver.clear_processing_lock()
        await driver.send_text("E2E Discount")
        driver.clear_processing_lock()
        await driver.send_text(unique_code)
        driver.clear_processing_lock()
        await driver.send_text("15")
        driver.clear_processing_lock()
        await driver.send_text("3")
        driver.clear_processing_lock()
        await driver.tap("discount_expiry_none")

        assert not driver.errors
        texts = " ".join(str(c.kwargs.get("text") or "") for c in driver.bot.calls)
        assert unique_code in texts
        rows = await list_discount_codes()
        assert any(r.code == unique_code for r in rows)

    @pytest.mark.asyncio
    async def test_discount_type_selected_prompts_name(self):
        update = _callback_update(800001, "discount_type_percent")
        context = MagicMock()
        context.user_data = {}

        with _admin_patches():
            state = await discount_type_selected(update, context)

        assert state == DISCOUNT_NAME
        assert context.user_data["discount_wizard"]["discount_type"] == "percent"

    @pytest.mark.asyncio
    async def test_list_pagination_nav(self, unique_code):
        base = unique_code
        for i in range(DISCOUNT_LIST_PAGE_SIZE + 1):
            await create_discount_code(
                name=f"Page{i}",
                code=f"{base}{i:02d}",
                discount_type="percent",
                value=5,
                currency_unit=None,
                max_uses_per_user=1,
            )

        update = _callback_update(800001, "discount_codes_menu")
        context = MagicMock()
        context.user_data = {"discount_list_mode": "active", "discount_list_page": 0}

        with _admin_patches():
            await discount_codes_menu(update, context)
            call = update.callback_query.edit_message_text.await_args
            page0 = (call.kwargs.get("text") if call.kwargs else "") or (call.args[0] if call.args else "")
            assert f"1–{DISCOUNT_LIST_PAGE_SIZE}" in page0 or "1–10" in page0

            update.callback_query.data = "discount_page_next"
            await discount_page_next(update, context)
            call = update.callback_query.edit_message_text.await_args
            page1 = (call.kwargs.get("text") if call.kwargs else "") or (call.args[0] if call.args else "")
            assert f"Page{DISCOUNT_LIST_PAGE_SIZE}" in page1 or f"{DISCOUNT_LIST_PAGE_SIZE + 1}." in page1

            update.callback_query.data = "discount_page_prev"
            await discount_page_prev(update, context)
            call = update.callback_query.edit_message_text.await_args
            page_back = (call.kwargs.get("text") if call.kwargs else "") or (call.args[0] if call.args else "")
            assert "1." in page_back

    @pytest.mark.asyncio
    async def test_toggle_discount_code(self, unique_code):
        row = await create_discount_code(
            name="ToggleMe",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )
        assert row.is_active is True
        toggled = await toggle_discount_code(row.id)
        assert toggled.is_active is False
        toggled_back = await toggle_discount_code(row.id)
        assert toggled_back.is_active is True

    @pytest.mark.asyncio
    async def test_edit_discount_name_and_max(self, unique_code):
        row = await create_discount_code(
            name="Before",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )
        await update_discount_code(row.id, name="After Edit")
        await update_discount_code(row.id, max_uses_per_user=5)
        updated = await get_discount_code(row.id)
        assert updated.name == "After Edit"
        assert updated.max_uses_per_user == 5

    @pytest.mark.asyncio
    async def test_receive_discount_edit_value_updates_name(self, unique_code):
        row = await create_discount_code(
            name="OldName",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )
        update = _text_update(800001, "NewName")
        context = MagicMock()
        context.user_data = {
            "discount_edit_id": row.id,
            "discount_edit_field": "name",
        }

        with _admin_patches():
            with patch(
                "vpn_bot.admin_discount._render_discount_codes_menu",
                AsyncMock(return_value=500),
            ):
                state = await receive_discount_edit_value(update, context)

        assert state == 500
        updated = await get_discount_code(row.id)
        assert updated.name == "NewName"

    @pytest.mark.asyncio
    async def test_delete_unused_code_hard_deletes(self, unique_code):
        row = await create_discount_code(
            name="Unused",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )
        result = await delete_discount_code(row.id)
        assert result == "hard"
        assert await get_discount_code(row.id) is None


# ---------------------------------------------------------------------------
# Section 6 — regression: admin discount fallback must not hijack buy buttons
# ---------------------------------------------------------------------------


class TestAdminDiscountFallbackRegression:
    @pytest.mark.asyncio
    async def test_main_menu_dispatch_clears_wizard_state(self):
        context = MagicMock()
        context.user_data = {
            "discount_wizard": {"name": "x"},
            "discount_edit_id": 1,
            "discount_edit_field": "max",
        }
        update = MagicMock()
        update.message = MagicMock()
        update.message.text = LanguageManager.get("menu.buy_wg")

        with patch("vpn_bot.bot_handler.main_menu_text_dispatch", AsyncMock(return_value=0)) as dispatch:
            result = await _admin_discount_main_menu_dispatch(update, context)

        assert "discount_wizard" not in context.user_data
        assert "discount_edit_id" not in context.user_data
        assert "discount_edit_field" not in context.user_data
        dispatch.assert_awaited_once_with(update, context)
        assert result == 0

    @pytest.mark.asyncio
    async def test_buy_wg_from_discount_menu_routes_to_user_flow_not_admin_list(self, db_user):
        from tests.helpers.admin_e2e_harness import AdminE2EDriver, build_admin_application

        wg_label = LanguageManager.get_all_translations_raw("menu.buy_wg")[0]
        app, bot, _ = await build_admin_application(include_user_handlers=True)
        driver = AdminE2EDriver(
            app,
            bot,
            admin_user_id=db_user.telegram_id,
            permissions={"sales", "sales.discounts"},
        )

        with patch("vpn_bot.bot_handler.buy_wg_service", AsyncMock(return_value=0)) as buy_wg:
            with patch(
                "vpn_bot.admin_discount._render_discount_codes_menu",
                AsyncMock(return_value=500),
            ) as render_list:
                await driver.open_admin_menu()
                driver.clear_processing_lock()
                await driver.tap("sales_mgmt_menu")
                driver.clear_processing_lock()
                await driver.tap("discount_codes_menu")
                driver.clear_processing_lock()
                render_list.reset_mock()
                await driver.send_text(wg_label)

        buy_wg.assert_awaited()
        render_list.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_buy_service_from_discount_menu_routes_to_user_flow(self, db_user):
        from tests.helpers.admin_e2e_harness import AdminE2EDriver, build_admin_application

        ovpn_label = LanguageManager.get_all_translations_raw("menu.buy_service")[0]
        app, bot, _ = await build_admin_application(include_user_handlers=True)
        driver = AdminE2EDriver(
            app,
            bot,
            admin_user_id=db_user.telegram_id,
            permissions={"sales", "sales.discounts"},
        )

        with patch("vpn_bot.bot_handler.buy_service", AsyncMock(return_value=0)) as buy_service:
            with patch(
                "vpn_bot.admin_discount._render_discount_codes_menu",
                AsyncMock(return_value=500),
            ) as render_list:
                await driver.open_admin_menu()
                driver.clear_processing_lock()
                await driver.tap("sales_mgmt_menu")
                driver.clear_processing_lock()
                await driver.tap("discount_codes_menu")
                driver.clear_processing_lock()
                render_list.reset_mock()
                await driver.send_text(ovpn_label)

        buy_service.assert_awaited()
        render_list.assert_not_awaited()


# ---------------------------------------------------------------------------
# Section 6c — admin + coupon workflow isolation
# ---------------------------------------------------------------------------


class TestAdminCouponWorkflowIsolation:
    @pytest.mark.asyncio
    async def test_route_admin_clears_coupon_waiting_state(self, db_user):
        from vpn_bot.admin_conversation import route_admin_command

        context = MagicMock()
        context.user_data = {
            "_coupon_waiting_code": True,
            "_coupon_scope": "buy_wg",
            "_coupon_resume": "buy_wg_plans",
        }
        update = MagicMock()
        update.effective_chat = MagicMock()
        update.effective_chat.id = db_user.telegram_id
        update.effective_chat.type = "private"
        update.message = MagicMock()

        with (
            _admin_patches(),
            patch(
                "vpn_bot.admin_permissions.resolve_group_admin_scope",
                AsyncMock(return_value="private"),
            ),
            patch(
                "vpn_bot.admin_permissions.require_admin_message",
                AsyncMock(return_value=True),
            ),
            patch("vpn_bot.admin_panel.admin_start", AsyncMock(return_value=0)) as admin_start,
        ):
            await route_admin_command(update, context)

        admin_start.assert_awaited_once()
        assert "_coupon_waiting_code" not in context.user_data
        assert "_coupon_scope" not in context.user_data

    @pytest.mark.asyncio
    async def test_admin_start_clears_coupon_waiting_state(self, db_user):
        from vpn_bot.admin_panel import admin_start

        context = MagicMock()
        context.user_data = {
            "_coupon_waiting_code": True,
            "_coupon_scope": "buy_ovpn",
            "_coupon_resume": "buy_plans",
        }
        update = MagicMock()
        update.effective_user = MagicMock()
        update.effective_user.id = db_user.telegram_id

        with (
            patch("vpn_bot.admin_panel.is_user_admin", AsyncMock(return_value=True)),
            patch("vpn_bot.admin_menu.build_admin_main_keyboard", AsyncMock(return_value=None)),
            patch("vpn_bot.admin_panel.universal_reply", AsyncMock()),
        ):
            await admin_start(update, context)

        assert "_coupon_waiting_code" not in context.user_data
        assert "_coupon_scope" not in context.user_data

    @pytest.mark.asyncio
    async def test_global_coupon_aborts_on_slash_command(self):
        context = MagicMock()
        context.user_data = {
            "_coupon_waiting_code": True,
            "_coupon_scope": "buy_wg",
            "_coupon_resume": "buy_wg_plans",
        }
        update = _text_update(1, "/admin")

        with patch("vpn_bot.coupon_flow.receive_coupon_code", AsyncMock()) as receive:
            await global_receive_coupon_code(update, context)

        receive.assert_not_awaited()
        assert "_coupon_waiting_code" not in context.user_data
        assert "_coupon_scope" not in context.user_data

    @pytest.mark.asyncio
    async def test_global_coupon_clears_orphan_waiting_without_scope(self):
        context = MagicMock()
        context.user_data = {"_coupon_waiting_code": True}
        update = _text_update(1, "SAVE10")

        with patch("vpn_bot.coupon_flow.receive_coupon_code", AsyncMock()) as receive:
            await global_receive_coupon_code(update, context)

        receive.assert_not_awaited()
        assert "_coupon_waiting_code" not in context.user_data


# ---------------------------------------------------------------------------
# Section 6b — discount expiry (comprehensive)
# ---------------------------------------------------------------------------


class TestDiscountExpiry:
    def test_plan_discounted_uses_html_strikethrough_on_full_price(self):
        from vpn_bot.coupon_flow import format_discounted_price_display

        rendered = format_discounted_price_display("10,000 تومان", "8,000 تومان")
        assert rendered == "<s>10,000 تومان</s> | 8,000 <b>تومان</b>"

    def test_plan_discounted_usd_strikethrough_on_full_price(self):
        from vpn_bot.coupon_flow import format_discounted_price_display

        rendered = format_discounted_price_display("$10.00", "$8.00")
        assert rendered == "<s>$10.00</s> | <b>$</b>8.00"

    @pytest.mark.asyncio
    async def test_valid_from_in_future_rejected(self, db_user, unique_code):
        future = datetime.now(timezone.utc) + timedelta(hours=2)
        async with AsyncSessionLocal() as session:
            code = DiscountCode(
                name="Future",
                code=unique_code,
                discount_type="percent",
                value=10,
                currency_unit=None,
                max_uses_per_user=1,
                valid_from=future,
                is_active=True,
            )
            session.add(code)
            await session.commit()
            await session.refresh(code)

            result = await validate_coupon_eligibility(
                session, code_str=unique_code, user_id=db_user.id
            )
        assert result.ok is False
        assert result.error_key == "coupon.expired"

    @pytest.mark.asyncio
    async def test_valid_until_past_rejected_in_eligibility(self, db_user, unique_code):
        past = datetime.now(timezone.utc) - timedelta(minutes=5)
        await create_discount_code(
            name="Past",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
            valid_until=past,
        )
        async with AsyncSessionLocal() as session:
            result = await validate_coupon_eligibility(
                session, code_str=unique_code, user_id=db_user.id
            )
        assert result.ok is False
        assert result.error_key == "coupon.expired"

    def test_session_ttl_expires_stored_coupon(self):
        user_data = {}
        store_active_coupon(
            user_data,
            code_id=1,
            code="SAVE10",
            scope="buy_wg",
        )
        user_data["active_coupon"]["validated_at"] = (
            time.time() - COUPON_SESSION_TTL_SECONDS - 1
        )
        assert active_coupon_from_session(user_data) is None
        assert "active_coupon" not in user_data

    @pytest.mark.asyncio
    async def test_disabled_between_preview_and_checkout(
        self, db_user, db_ovpn_profile, mock_mikrotik, unique_code
    ):
        row = await create_discount_code(
            name="MidDisable",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )
        async with AsyncSessionLocal() as session:
            preview = await validate_coupon_for_use(
                session,
                code_id=row.id,
                user_id=db_user.id,
                context="purchase_ovpn",
                base_amount=100_000,
                currency="TOMAN",
            )
        assert preview.ok is True

        await toggle_discount_code(row.id)
        ok, _, _ = await checkout_subscription(
            db_user.telegram_id, db_ovpn_profile.id, coupon_id=row.id
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_admin_expiry_wizard_computes_valid_until(self, unique_code):
        update = _text_update(800001, "7")
        context = MagicMock()
        context.user_data = {
            "discount_wizard": {
                "name": "Timed",
                "code": unique_code,
                "discount_type": "percent",
                "value": 10,
                "max_uses_per_user": 1,
                "expiry_unit": "day",
            }
        }
        captured: dict = {}

        async def _capture_finalize(update, ctx, *, valid_until=None):
            captured["valid_until"] = valid_until
            return 500

        with _admin_patches():
            with patch(
                "vpn_bot.admin_discount._finalize_discount_create",
                side_effect=_capture_finalize,
            ):
                state = await receive_discount_expiry_value(update, context)

        assert state == 500
        assert captured["valid_until"] is not None
        expected = compute_valid_until("day", 7)
        assert abs(
            (captured["valid_until"] - expected).total_seconds()
        ) < 2


# ---------------------------------------------------------------------------
# Section 7 — security
# ---------------------------------------------------------------------------


class TestDiscountSecurity:
    def test_rbac_discount_menu_permission_mapping(self):
        assert permission_for_callback("discount_codes_menu") == "sales.discounts"
        assert permission_for_callback("discount_add") == "sales.discounts"
        assert not has_perm_in_set({PERM_RECEIPTS}, "sales.discounts")
        assert has_perm_in_set({"sales"}, "sales.discounts")

    @pytest.mark.asyncio
    async def test_non_admin_cannot_open_discount_menu(self, db_user):
        from tests.security.helpers.security_harness import UserSecurityDriver, build_user_application

        app, bot = await build_user_application()
        driver = UserSecurityDriver(app, bot, db_user.telegram_id)

        with patch("vpn_bot.admin_management.is_user_admin", AsyncMock(return_value=False)):
            await driver.tap("discount_codes_menu")

        alert_calls = driver.calls_of("answer_callback_query")
        assert alert_calls, "Expected access-denied callback answer for non-admin"
        last = alert_calls[-1]
        assert last.get("show_alert") is True or last.get("text")

    @pytest.mark.asyncio
    async def test_inactive_code_rejected_in_eligibility(self, db_user, unique_code):
        row = await create_discount_code(
            name="Inactive",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )
        await toggle_discount_code(row.id)

        async with AsyncSessionLocal() as session:
            result = await validate_coupon_eligibility(
                session, code_str=unique_code, user_id=db_user.id
            )
        assert result.ok is False

    @pytest.mark.asyncio
    async def test_per_user_limit_enforced(self, db_user, db_ovpn_profile, mock_mikrotik, unique_code):
        row = await create_discount_code(
            name="Once",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )
        ok1, _, _ = await checkout_subscription(
            db_user.telegram_id, db_ovpn_profile.id, coupon_id=row.id
        )
        assert ok1 is True
        ok2, _, _ = await checkout_subscription(
            db_user.telegram_id, db_ovpn_profile.id, coupon_id=row.id
        )
        assert ok2 is False

    @pytest.mark.asyncio
    async def test_expired_coupon_rejected_in_validation(self, db_user, unique_code):
        past = datetime.now(timezone.utc) - timedelta(hours=1)
        row = await create_discount_code(
            name="Expired",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
            valid_until=past,
        )
        async with AsyncSessionLocal() as session:
            result = await validate_coupon_for_use(
                session,
                code_id=row.id,
                user_id=db_user.id,
                context="purchase_ovpn",
                base_amount=100_000,
                currency="TOMAN",
            )
        assert result.ok is False
        assert result.error_key == "coupon.expired"

    @pytest.mark.asyncio
    async def test_coupon_prompt_is_user_facing_not_admin_list(self, db_user, db_wg_profile, mock_mikrotik):
        from vpn_bot.coupon_flow import prompt_coupon

        update = MagicMock()
        update.callback_query = AsyncMock()
        update.callback_query.answer = AsyncMock()
        update.effective_user = MagicMock()
        update.effective_user.id = db_user.telegram_id
        context = MagicMock()
        context.user_data = {}

        with patch("vpn_bot.coupon_flow.send_localized_text", AsyncMock()) as send_text:
            state = await prompt_coupon(
                update,
                context,
                scope="buy_wg",
                resume_callback="buy_wg_plans",
            )

        assert state == COUPON_PROMPT
        assert context.user_data["_coupon_scope"] == "buy_wg"
        assert context.user_data["_coupon_resume"] == "buy_wg_plans"
        send_text.assert_awaited_once()
        markup = send_text.await_args.kwargs.get("reply_markup")
        callbacks = [
            btn.callback_data
            for row in markup.inline_keyboard
            for btn in row
        ]
        assert "coupon_enter" in callbacks
        assert "coupon_skip" in callbacks
        assert not any(cb.startswith("discount_") for cb in callbacks)

    @pytest.mark.parametrize(
        "discount_type,value,expected_error",
        [
            ("percent", 0, "invalid_percent"),
            ("percent", -5, "invalid_percent"),
            ("percent", 101, "invalid_percent"),
            ("fixed", 0, "invalid_fixed"),
            ("fixed", -100, "invalid_fixed"),
        ],
    )
    @pytest.mark.asyncio
    async def test_admin_create_rejects_invalid_values(
        self, unique_code, discount_type, value, expected_error
    ):
        with pytest.raises(ValueError, match=expected_error):
            await create_discount_code(
                name="Bad",
                code=unique_code,
                discount_type=discount_type,
                value=value,
                currency_unit="TOMAN" if discount_type == "fixed" else None,
                max_uses_per_user=1,
            )

    @pytest.mark.asyncio
    async def test_admin_create_rejects_invalid_code_format(self, unique_code):
        with pytest.raises(ValueError, match="invalid_code_format"):
            await create_discount_code(
                name="Short",
                code="AB",
                discount_type="percent",
                value=10,
                currency_unit=None,
                max_uses_per_user=1,
            )

    @pytest.mark.asyncio
    async def test_admin_create_rejects_duplicate_code(self, unique_code):
        await create_discount_code(
            name="First",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )
        with pytest.raises(ValueError, match="code_duplicate"):
            await create_discount_code(
                name="Second",
                code=unique_code.lower(),
                discount_type="percent",
                value=5,
                currency_unit=None,
                max_uses_per_user=1,
            )

    def test_rate_limit_blocks_inside_window(self):
        user_data = {
            "_coupon_fail": {
                "count": PREVIEW_FAIL_LIMIT,
                "window_start": time.time(),
            }
        }
        assert check_preview_rate_limit(user_data) is True

    def test_rate_limit_resets_after_window(self):
        user_data = {
            "_coupon_fail": {
                "count": PREVIEW_FAIL_LIMIT,
                "window_start": time.time() - PREVIEW_FAIL_WINDOW_SECONDS - 1,
            }
        }
        assert check_preview_rate_limit(user_data) is False
        assert user_data["_coupon_fail"]["count"] == 0

    @pytest.mark.asyncio
    async def test_tampered_coupon_scope_rejected(self):
        context = MagicMock()
        context.user_data = {
            "_coupon_scope": "buy_wg",
            "active_coupon": {
                "code_id": 99,
                "code": "SAVE10",
                "scope": "buy_ovpn",
                "validated_at": time.time(),
            },
        }
        assert coupon_id_from_context(context) is None

    @pytest.mark.asyncio
    async def test_fake_coupon_id_checkout_rejected(
        self, db_user, db_ovpn_profile, mock_mikrotik
    ):
        ok, _, _ = await checkout_subscription(
            db_user.telegram_id, db_ovpn_profile.id, coupon_id=999_999
        )
        assert ok is False

    @pytest.mark.asyncio
    async def test_amount_mismatch_in_redeem_raises(
        self, db_user, unique_code
    ):
        row = await create_discount_code(
            name="Redeem",
            code=unique_code,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )
        async with AsyncSessionLocal() as session:
            with pytest.raises(ValueError, match="amount_mismatch"):
                await redeem_coupon_atomic(
                    session,
                    code_id=row.id,
                    user_id=db_user.id,
                    context="purchase_ovpn",
                    original_amount=100_000,
                    final_amount=1,
                    discount_amount=99_999,
                    currency="TOMAN",
                )
            await session.rollback()

    @pytest.mark.asyncio
    async def test_lowercase_user_input_matches_stored_code(self, db_user, unique_code):
        stored = unique_code.upper()
        await create_discount_code(
            name="Case",
            code=stored,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )
        async with AsyncSessionLocal() as session:
            result = await validate_coupon_eligibility(
                session, code_str=stored.lower(), user_id=db_user.id
            )
        assert result.ok is True
        assert result.code == stored

    @pytest.mark.asyncio
    async def test_whitespace_padded_input_matches_stored_code(self, db_user, unique_code):
        stored = unique_code.upper()
        await create_discount_code(
            name="Trim",
            code=stored,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )
        async with AsyncSessionLocal() as session:
            result = await validate_coupon_eligibility(
                session, code_str=f"  {stored.lower()}  ", user_id=db_user.id
            )
        assert result.ok is True
        assert normalize_code(f"  {stored.lower()}  ") == stored

    @pytest.mark.asyncio
    async def test_admin_create_normalizes_code_to_uppercase(self, unique_code):
        mixed = f"ab{unique_code[-4:].lower()}"
        row = await create_discount_code(
            name="Upper",
            code=mixed,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )
        assert row.code == normalize_code(mixed)

    @pytest.mark.asyncio
    async def test_admin_list_shows_uppercase_code(self, unique_code):
        mixed = f"xy{unique_code[-4:].lower()}"
        stored = normalize_code(mixed)
        await create_discount_code(
            name="ListCase",
            code=mixed,
            discount_type="percent",
            value=10,
            currency_unit=None,
            max_uses_per_user=1,
        )
        update = _callback_update(800001, "discount_codes_menu")
        context = MagicMock()
        context.user_data = {}

        with _admin_patches():
            await discount_codes_menu(update, context)

        call = update.callback_query.edit_message_text.await_args
        body = (call.kwargs.get("text") if call.kwargs else "") or (call.args[0] if call.args else "")
        assert stored in body
        assert mixed.lower() not in body or mixed.lower() == stored.lower()

    @pytest.mark.asyncio
    async def test_global_limit_second_user_rejected(
        self, db_user, db_user_factory, db_ovpn_profile, mock_mikrotik, unique_code
    ):
        row = await create_discount_code(
            name="GlobalOne",
            code=unique_code,
            discount_type="percent",
            value=5,
            currency_unit=None,
            max_uses_per_user=5,
            max_total_uses=1,
        )
        u2 = await db_user_factory(balance=1_000_000.0)

        ok1, _, _ = await checkout_subscription(
            db_user.telegram_id, db_ovpn_profile.id, coupon_id=row.id
        )
        assert ok1 is True
        ok2, _, _ = await checkout_subscription(
            u2.telegram_id, db_ovpn_profile.id, coupon_id=row.id
        )
        assert ok2 is False
