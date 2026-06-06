"""Shared fixtures for discount DB tests."""

from __future__ import annotations

import uuid

import pytest


@pytest.fixture(autouse=True)
async def discount_checkout_gates(_fresh_db_engine_per_test):
    from vpn_bot.admin_discount_service import purge_all_discount_codes
    from vpn_bot.admin_settings_service import set_purchase_terms_enabled
    from vpn_bot.settings_utils import set_admin_setting

    await purge_all_discount_codes(dry_run=False)
    await set_purchase_terms_enabled(False)
    await set_admin_setting("sales_ovpn_limit", 0)
    await set_admin_setting("sales_wg_limit", 0)
    await set_admin_setting("sales_global_active", True)
    await set_admin_setting("sales_ovpn_active", True)
    await set_admin_setting("sales_wg_active", True)
    yield
    await purge_all_discount_codes(dry_run=False)


@pytest.fixture
def unique_code():
    return f"T{uuid.uuid4().hex[:6].upper()}"
