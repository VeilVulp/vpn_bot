"""WG renewal resets MikroTik peer quota via reset_wg_peer_for_renewal."""

from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.helpers.mock_mikrotik import MockMikrotikManager
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import WireGuardSubscription
from vpn_bot.user_features import confirm_wg_renewal
from vpn_bot.utils import utc_now

pytestmark = pytest.mark.db


@pytest.mark.asyncio
async def test_wg_renewal_resets_peer_quota_on_mikrotik(
    db_user, db_wg_interface, db_wg_profile, mock_server, mock_mikrotik
):
    uid = f"WG-RENEW-{uuid.uuid4().hex[:8]}"
    expiry = utc_now() + timedelta(days=5)
    async with AsyncSessionLocal() as session:
        sub = WireGuardSubscription(
            user_id=db_user.id,
            interface_id=db_wg_interface.id,
            profile_id=db_wg_profile.id,
            unique_identifier=uid,
            peer_public_key="g" * 43 + "=",
            peer_private_key="h" * 43 + "=",
            assigned_ip="10.88.0.77",
            status="active",
            expiry_date=expiry,
            bytes_remaining=100,
            total_bytes_rx=900_000_000,
            total_bytes_tx=100_000_000,
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        sub_id = sub.id
        peer_key = sub.peer_public_key

    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.wg_peers[db_wg_interface.name] = {
        peer_key: {"rx": 900_000_000, "tx": 100_000_000},
    }

    query = MagicMock()
    query.data = f"renew_wg_confirm_{sub_id}"
    query.answer = AsyncMock()
    query.edit_message_text = AsyncMock()

    update = MagicMock()
    update.callback_query = query
    update.effective_user = MagicMock()
    update.effective_user.id = db_user.telegram_id
    update.effective_chat = MagicMock()
    update.effective_chat.id = db_user.telegram_id

    context = MagicMock()
    context.user_data = {}

    with patch("vpn_bot.bot_handler.send_wg_config_again", new_callable=AsyncMock):
        result = await confirm_wg_renewal(update, context)

    assert result is not None
    assert len(mgr.reset_wg_peer_for_renewal_calls) == 1
    iface, pub, allowed, _comment = mgr.reset_wg_peer_for_renewal_calls[0]
    assert iface == db_wg_interface.name
    assert pub == peer_key
    assert allowed == "10.88.0.77/32"

    async with AsyncSessionLocal() as session:
        renewed = await session.get(WireGuardSubscription, sub_id)
        assert renewed.total_bytes_rx == 0
        assert renewed.total_bytes_tx == 0
        assert renewed.last_router_rx == 0
        assert renewed.last_router_tx == 0
