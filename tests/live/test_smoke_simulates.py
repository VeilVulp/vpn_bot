"""
Pytest wrappers for legacy simulate_* scripts (service-layer smoke).

Replaces ad-hoc simulate_user_flow / simulate_admin_flow execution.
"""

import pytest

from vpn_bot.admin_server_service import get_all_servers
from vpn_bot.admin_ticket_service import get_open_ticket_count
from vpn_bot.admin_user_service import get_user_by_tg_id
from vpn_bot.admin_wg_service import get_all_wg_profiles

pytestmark = pytest.mark.live_mt

TEST_TG = 123456789


@pytest.mark.asyncio
async def test_simulate_user_lookup():
    user = await get_user_by_tg_id(TEST_TG)
    assert user is None or user.telegram_id == TEST_TG


@pytest.mark.asyncio
async def test_simulate_admin_servers_list():
    servers = await get_all_servers()
    assert isinstance(servers, list)


@pytest.mark.asyncio
async def test_simulate_wg_profiles_list():
    profiles = await get_all_wg_profiles()
    assert isinstance(profiles, list)


@pytest.mark.asyncio
async def test_simulate_ticket_count(intg_user):
    count = await get_open_ticket_count(intg_user.id)
    assert count >= 0
