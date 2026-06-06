"""WireGuard firewall automation: DB persistence, inheritance, capacity, preemptive create."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import select

from tests.helpers.mock_mikrotik import MockMikrotikManager
from vpn_bot.admin_wg_service import (
    apply_wg_automation,
    check_and_preemptively_create_interfaces,
    update_wg_interface,
)
from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import WireGuardInterface, WireGuardSubscription
from vpn_bot.user_features import finalize_wg_purchase

pytestmark = pytest.mark.db


async def _occupy_interface_slots(iface_id: int, count: int, db_user_factory) -> None:
    """Seed active WG subs so capacity gate sees a full interface."""
    from datetime import timedelta

    from vpn_bot.admin_wg_service import sync_wg_interface_current_users
    from vpn_bot.utils import utc_now

    users = [await db_user_factory(balance=1_000_000.0) for _ in range(count)]
    async with AsyncSessionLocal() as session:
        for i, user in enumerate(users):
            session.add(
                WireGuardSubscription(
                    user_id=user.id,
                    interface_id=iface_id,
                    profile_id=None,
                    unique_identifier=f"WG-seed_{uuid.uuid4().hex[:6]}",
                    peer_public_key=f"pk_{uuid.uuid4().hex}",
                    peer_private_key="priv",
                    assigned_ip=f"10.99.0.{i + 2}",
                    expiry_date=utc_now() + timedelta(days=3),
                    status="active",
                )
            )
        await sync_wg_interface_current_users(session, iface_id)
        await session.commit()


async def _clear_server_interfaces(server_id: int) -> None:
    async with AsyncSessionLocal() as session:
        iface_ids = (
            await session.execute(
                select(WireGuardInterface.id).where(
                    WireGuardInterface.server_id == server_id
                )
            )
        ).scalars().all()
        if iface_ids:
            from sqlalchemy import delete

            await session.execute(
                delete(WireGuardSubscription).where(
                    WireGuardSubscription.interface_id.in_(iface_ids)
                )
            )
            await session.execute(
                delete(WireGuardInterface).where(WireGuardInterface.id.in_(iface_ids))
            )
        await session.commit()


@pytest.mark.asyncio
async def test_update_wg_interface_persists_firewall_fields(mock_server, mock_mikrotik):
    async with AsyncSessionLocal() as session:
        iface = WireGuardInterface(
            server_id=mock_server.id,
            name=f"wg_fw_{uuid.uuid4().hex[:6]}",
            public_key="pub",
            private_key="priv",
            listen_port=51820,
            address="10.1.0.1/24",
            max_users=10,
            is_active=True,
        )
        session.add(iface)
        await session.commit()
        await session.refresh(iface)
        iface_id = iface.id

    ok, err = await update_wg_interface(
        iface_id,
        {
            "upstream_interface": "ether1",
            "routing_mark": "wg_mark_a",
            "nat_routing_mark": "wg_nat_match",
            "nat_dst_address": "192.168.1.1",
        },
    )
    assert ok is True
    assert err is None

    async with AsyncSessionLocal() as session:
        row = await session.get(WireGuardInterface, iface_id)
        assert row.upstream_interface == "ether1"
        assert row.routing_mark == "wg_mark_a"
        assert row.nat_routing_mark == "wg_nat_match"
        assert row.nat_dst_address == "192.168.1.1"


@pytest.mark.asyncio
async def test_apply_wg_automation_passes_db_to_manager(mock_server, mock_mikrotik):
    async with AsyncSessionLocal() as session:
        iface = WireGuardInterface(
            server_id=mock_server.id,
            name=f"wg_auto_{uuid.uuid4().hex[:6]}",
            public_key="pub",
            private_key="priv",
            listen_port=51821,
            address="10.2.0.1/24",
            upstream_interface="ether2",
            routing_mark="rm_test",
            nat_routing_mark="nat_rm_test",
            nat_dst_address="10.0.0.5",
            gateway="192.168.88.1",
            max_users=5,
            is_active=True,
        )
        session.add(iface)
        await session.commit()
        await session.refresh(iface)

    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.automation_calls.clear()

    ok, route_applied = await apply_wg_automation(iface.id)
    assert ok is True
    assert route_applied is True
    last = mgr.last_automation()
    assert last is not None
    assert last["name"] == iface.name
    assert last["upstream"] == "ether2"
    assert last["routing_mark"] == "rm_test"
    assert last["nat_routing_mark"] == "nat_rm_test"
    assert last["nat_dst"] == "10.0.0.5"
    assert last["gateway"] == "192.168.88.1"
    assert last["listen_port"] == 51821


@pytest.mark.asyncio
async def test_purchase_creates_interface_when_all_full(
    mock_server, mock_mikrotik, db_wg_profile, db_user_factory
):
    await _clear_server_interfaces(mock_server.id)
    user = await db_user_factory(balance=2_000_000.0)
    mgr = MockMikrotikManager.for_server(mock_server.id)

    async with AsyncSessionLocal() as session:
        parent = WireGuardInterface(
            server_id=mock_server.id,
            name=f"wg_parent_{uuid.uuid4().hex[:6]}",
            public_key="pub_p",
            private_key="priv_p",
            listen_port=51830,
            address="10.10.0.1/24",
            upstream_interface="ether_wan",
            routing_mark="parent_rm",
            nat_routing_mark="parent_nat_rm",
            nat_dst_address="203.0.113.1",
            gateway="192.168.1.1",
            max_users=2,
            current_users=2,
            is_active=True,
        )
        session.add(parent)
        await session.flush()
        parent_id = parent.id
        await session.commit()

    await _occupy_interface_slots(parent_id, 2, db_user_factory)

    mgr.automation_calls.clear()
    ok = await finalize_wg_purchase(
        user.telegram_id, db_wg_profile.id, context=None, is_tg_id=True
    )
    assert ok is True
    assert len(mgr.automation_calls) >= 1

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WireGuardInterface).where(WireGuardInterface.server_id == mock_server.id)
        )
        all_ifaces = list(res.scalars().all())
        assert len(all_ifaces) == 2
        child = next(i for i in all_ifaces if i.id != parent.id)
        assert child.upstream_interface == "ether_wan"
        assert child.routing_mark == "parent_rm"
        assert child.nat_routing_mark == "parent_nat_rm"
        assert child.nat_dst_address == "203.0.113.1"
        assert child.gateway == "192.168.1.1"


@pytest.mark.asyncio
async def test_purchase_inherits_lowest_id_parent(
    mock_server, mock_mikrotik, db_wg_profile, db_user_factory
):
    await _clear_server_interfaces(mock_server.id)
    user = await db_user_factory(balance=2_000_000.0)

    async with AsyncSessionLocal() as session:
        old = WireGuardInterface(
            server_id=mock_server.id,
            name=f"wg_old_{uuid.uuid4().hex[:6]}",
            public_key="pub1",
            private_key="priv1",
            listen_port=51840,
            address="10.20.0.1/24",
            upstream_interface="from_old_parent",
            routing_mark="old_rm",
            max_users=1,
            current_users=1,
            is_active=True,
        )
        session.add(old)
        await session.flush()

        new_full = WireGuardInterface(
            server_id=mock_server.id,
            name=f"wg_newer_{uuid.uuid4().hex[:6]}",
            public_key="pub2",
            private_key="priv2",
            listen_port=51841,
            address="10.21.0.1/24",
            upstream_interface="from_wrong_parent",
            routing_mark="wrong_rm",
            max_users=1,
            current_users=1,
            is_active=True,
        )
        session.add(new_full)
        await session.commit()
        assert old.id < new_full.id
        old_id, new_id = old.id, new_full.id

    await _occupy_interface_slots(old_id, 1, db_user_factory)
    await _occupy_interface_slots(new_id, 1, db_user_factory)

    ok = await finalize_wg_purchase(
        user.telegram_id, db_wg_profile.id, context=None, is_tg_id=True
    )
    assert ok is True

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WireGuardInterface).where(
                WireGuardInterface.server_id == mock_server.id,
                WireGuardInterface.current_users == 1,
            )
        )
        created = [
            i
            for i in res.scalars().all()
            if i.name not in (old.name, new_full.name)
        ]
        assert len(created) == 1
        assert created[0].upstream_interface == "from_old_parent"
        assert created[0].routing_mark == "old_rm"


@pytest.mark.asyncio
async def test_preemptive_create_when_below_threshold(mock_server, mock_mikrotik, db_user_factory):
    await _clear_server_interfaces(mock_server.id)
    async with AsyncSessionLocal() as session:
        iface = WireGuardInterface(
            server_id=mock_server.id,
            name=f"wg_full_{uuid.uuid4().hex[:6]}",
            public_key="pub",
            private_key="priv",
            listen_port=51850,
            address="10.30.0.1/24",
            upstream_interface="ether1",
            routing_mark="preempt_rm",
            max_users=10,
            current_users=8,
            is_active=True,
        )
        session.add(iface)
        await session.flush()
        iface_id = iface.id
        await session.commit()

    await _occupy_interface_slots(iface_id, 8, db_user_factory)

    mgr = MockMikrotikManager.for_server(mock_server.id)
    mgr.automation_calls.clear()

    with patch(
        "vpn_bot.admin_settings.get_admin_setting",
        side_effect=lambda key, default=None: 5 if key == "wg_preemptive_threshold" else default,
    ):
        created = await check_and_preemptively_create_interfaces()

    assert len(created) >= 1
    assert len(mgr.automation_calls) >= 1
    last = mgr.last_automation()
    assert last["upstream"] == "ether1"
    assert last["routing_mark"] == "preempt_rm"


@pytest.mark.asyncio
async def test_preemptive_parent_is_lowest_id(mock_server, mock_mikrotik, db_user_factory):
    await _clear_server_interfaces(mock_server.id)
    async with AsyncSessionLocal() as session:
        low = WireGuardInterface(
            server_id=mock_server.id,
            name=f"wg_low_{uuid.uuid4().hex[:6]}",
            public_key="p1",
            private_key="k1",
            listen_port=51860,
            address="10.40.0.1/24",
            upstream_interface="inherit_low",
            routing_mark="low_rm",
            max_users=10,
            current_users=9,
            is_active=True,
        )
        session.add(low)
        await session.flush()

        high = WireGuardInterface(
            server_id=mock_server.id,
            name=f"wg_high_{uuid.uuid4().hex[:6]}",
            public_key="p2",
            private_key="k2",
            listen_port=51861,
            address="10.41.0.1/24",
            upstream_interface="inherit_high",
            routing_mark="high_rm",
            max_users=10,
            current_users=9,
            is_active=True,
        )
        session.add(high)
        await session.commit()
        assert low.id < high.id
        low_id, high_id = low.id, high.id

    await _occupy_interface_slots(low_id, 9, db_user_factory)
    await _occupy_interface_slots(high_id, 9, db_user_factory)

    with patch(
        "vpn_bot.admin_settings.get_admin_setting",
        side_effect=lambda key, default=None: 5 if key == "wg_preemptive_threshold" else default,
    ):
        created = await check_and_preemptively_create_interfaces()

    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(WireGuardInterface).where(
                WireGuardInterface.server_id == mock_server.id,
                WireGuardInterface.name.in_(created),
            )
        )
        children = list(res.scalars().all())

    assert len(children) == 1
    child = children[0]
    assert child.upstream_interface == "inherit_low"
    assert child.routing_mark == "low_rm"
