"""
Telegram E2E: WireGuard renewal via user callback handlers on real MikroTik.
"""

from __future__ import annotations

import pytest

from tests.helpers.admin_e2e_harness import AdminE2EDriver
from tests.live.helpers.wg_workflow_live import (
    assert_peer_disabled,
    assert_peer_enabled,
    fetch_latest_wg_sub,
    load_sub,
    run_wg_reconcile,
    seed_wg_usage_at_quota,
    teardown_wg_sub,
)
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.user_features import finalize_wg_purchase

pytest_plugins = ["tests.live.admin_e2e_conftest"]

pytestmark = [pytest.mark.live_mt, pytest.mark.live_admin_e2e]


@pytest.fixture
async def user_driver(admin_app, e2e_test_user):
    app, bot = admin_app
    driver = AdminE2EDriver(app, bot, e2e_test_user.telegram_id)
    yield driver


@pytest.mark.asyncio
async def test_wg_renewal_e2e_after_quota_exhaustion(
    user_driver,
    e2e_test_user,
    intg_wg_profile_long_days,
    intg_wg_interface,
    live_server,
):
    ok = await finalize_wg_purchase(
        e2e_test_user.telegram_id,
        intg_wg_profile_long_days.id,
        context=None,
        is_tg_id=True,
    )
    assert ok is True

    sub = await fetch_latest_wg_sub(e2e_test_user.id, intg_wg_profile_long_days.id)
    assert sub is not None
    mgr = get_mikrotik_manager(live_server)

    await seed_wg_usage_at_quota(sub.id, at_cap=True, server=live_server)
    await run_wg_reconcile(live_server)
    assert (await load_sub(sub.id)).status == "disabled"
    await assert_peer_disabled(mgr, sub.interface.name, sub.peer_public_key)

    await user_driver.send_command("start")
    await user_driver.tap(f"renew_wg_{sub.id}")
    assert not user_driver.errors

    callbacks = user_driver.callbacks_on_screen()
    assert any(c == f"renew_wg_confirm_{sub.id}" for c in callbacks)

    await user_driver.tap(f"renew_wg_confirm_{sub.id}")
    assert not user_driver.errors

    refreshed = await load_sub(sub.id)
    assert refreshed.status == "active"
    assert refreshed.total_bytes_rx == 0
    assert refreshed.total_bytes_tx == 0
    await assert_peer_enabled(mgr, sub.interface.name, sub.peer_public_key)

    await teardown_wg_sub(sub, live_server)
