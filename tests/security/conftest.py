"""Fixtures for white-box security tests."""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import OvpnConfig, Subscription, User, WireGuardSubscription

pytest_plugins = ["tests.conftest_db"]

pytestmark = [pytest.mark.security, pytest.mark.db]


@pytest.fixture
async def victim_user(db_user_factory):
    return await db_user_factory(balance=500_000.0, banned=False)


@pytest.fixture
async def attacker_user(db_user_factory):
    return await db_user_factory(balance=500_000.0, banned=False)


@pytest.fixture
async def banned_user(db_user_factory):
    return await db_user_factory(balance=500_000.0, banned=True)


@pytest.fixture
async def unregistered_user(db_user_factory):
    user = await db_user_factory(balance=0.0, banned=False)
    async with AsyncSessionLocal() as session:
        row = await session.get(User, user.id)
        row.phone_number = None
        await session.commit()
        await session.refresh(row)
        return row


@pytest.fixture
async def victim_ovpn_sub(victim_user, mock_server, db_ovpn_profile):
    suffix = uuid.uuid4().hex[:8]
    expiry = datetime.now(timezone.utc) + timedelta(days=30)
    async with AsyncSessionLocal() as session:
        sub = Subscription(
            user_id=victim_user.id,
            server_id=mock_server.id,
            profile_id=db_ovpn_profile.id,
            mikrotik_username=f"sec_ovpn_{suffix}",
            mikrotik_password=f"SecretPass_{suffix}",
            status="active",
            expiry_date=expiry,
            total_limit_bytes=1024**3,
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        yield sub


@pytest.fixture
async def victim_ovpn_config(mock_server, victim_ovpn_sub):
    async with AsyncSessionLocal() as session:
        cfg = OvpnConfig(
            server_id=mock_server.id,
            filename=f"sec_{uuid.uuid4().hex[:6]}.ovpn",
            config_content="client\ndev tun\n",
            display_name="SecTest OVPN",
        )
        session.add(cfg)
        await session.commit()
        await session.refresh(cfg)
        yield cfg


@pytest.fixture
async def victim_wg_sub(victim_user, db_wg_interface, db_wg_profile):
    suffix = uuid.uuid4().hex[:8]
    expiry = datetime.now(timezone.utc) + timedelta(days=30)
    async with AsyncSessionLocal() as session:
        sub = WireGuardSubscription(
            user_id=victim_user.id,
            interface_id=db_wg_interface.id,
            profile_id=db_wg_profile.id,
            unique_identifier=f"SEC-WG-{suffix}",
            peer_public_key="a" * 43 + "=",
            peer_private_key="b" * 43 + "=",
            assigned_ip="10.88.0.42",
            status="active",
            expiry_date=expiry,
            bytes_remaining=1024**3,
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        yield sub


@pytest.fixture
async def victim_wg_sub_expired(victim_user, db_wg_interface, db_wg_profile):
    suffix = uuid.uuid4().hex[:8]
    expiry = datetime.now(timezone.utc) - timedelta(days=1)
    async with AsyncSessionLocal() as session:
        sub = WireGuardSubscription(
            user_id=victim_user.id,
            interface_id=db_wg_interface.id,
            profile_id=db_wg_profile.id,
            unique_identifier=f"SEC-EXP-{suffix}",
            peer_public_key="c" * 43 + "=",
            peer_private_key="d" * 43 + "=",
            assigned_ip="10.88.0.99",
            status="expired",
            expiry_date=expiry,
            bytes_remaining=0,
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        yield sub


@pytest.fixture
async def attacker_wg_sub(attacker_user, db_wg_interface, db_wg_profile):
    suffix = uuid.uuid4().hex[:8]
    expiry = datetime.now(timezone.utc) + timedelta(days=30)
    async with AsyncSessionLocal() as session:
        sub = WireGuardSubscription(
            user_id=attacker_user.id,
            interface_id=db_wg_interface.id,
            profile_id=db_wg_profile.id,
            unique_identifier=f"SEC-ATK-{suffix}",
            peer_public_key="e" * 43 + "=",
            peer_private_key="f" * 43 + "=",
            assigned_ip="10.88.0.50",
            status="active",
            expiry_date=expiry,
            bytes_remaining=1024**3,
        )
        session.add(sub)
        await session.commit()
        await session.refresh(sub)
        yield sub
