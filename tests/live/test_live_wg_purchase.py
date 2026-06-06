"""Live WireGuard purchase via finalize_wg_purchase."""

import asyncio

import pytest
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.mikrotik_manager import get_mikrotik_manager
from vpn_bot.models import WireGuardSubscription
from vpn_bot.user_features import finalize_wg_purchase

pytestmark = pytest.mark.live_mt


@pytest.mark.asyncio
async def test_finalize_wg_purchase_creates_peer(
    intg_user, intg_wg_profile, intg_wg_interface, live_server
):
    ok = await finalize_wg_purchase(
        intg_user.telegram_id,
        intg_wg_profile.id,
        context=None,
        is_tg_id=True,
    )
    assert ok is True

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WireGuardSubscription)
            .options(joinedload(WireGuardSubscription.interface))
            .where(
                WireGuardSubscription.user_id == intg_user.id,
                WireGuardSubscription.profile_id == intg_wg_profile.id,
                WireGuardSubscription.status == "active",
            )
            .order_by(WireGuardSubscription.id.desc())
        )
        sub = res.scalars().first()

    assert sub is not None
    assert sub.unique_identifier.startswith("WG-")

    mgr = get_mikrotik_manager(live_server)
    peers = await asyncio.to_thread(mgr.get_all_wg_peers, sub.interface.name)
    peer_keys = [p.get("public-key") for p in (peers or [])]
    assert sub.peer_public_key in peer_keys

    await asyncio.to_thread(
        mgr.remove_wg_peer, sub.interface.name, sub.peer_public_key
    )
    if sub.unique_identifier:
        try:
            await asyncio.to_thread(mgr.remove_wg_queue, sub.unique_identifier)
        except Exception:
            pass

    async with AsyncSessionLocal() as session:
        row = await session.get(WireGuardSubscription, sub.id)
        if row:
            await session.delete(row)
            await session.commit()
