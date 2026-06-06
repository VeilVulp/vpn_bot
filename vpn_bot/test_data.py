"""
Detect and purge pytest / integration test rows from the production database.
"""

from __future__ import annotations

import re
from typing import Any

from vpn_bot.admin_server_service import is_mock_or_test_server
from vpn_bot.models import Server

TEST_NAME_RE = re.compile(
    r"^(INTG_|DBTEST_|INACTIVE_PROF|CHAOS_|Admin-Test|Test )",
    re.I,
)

# Pytest DB user telegram_id range (see tests/conftest_db._next_tg_id)
TEST_TG_ID_MIN = 700_000_000_000
TEST_TG_ID_MAX = 799_999_999_999

# WG interface names created only in tests (production uses bot_wgN, etc.)
TEST_WG_INTERFACE_PREFIXES = (
    "wg_test_",
    "wg_db_",
    "wg_fw_",
    "wg_auto_",
    "wg_parent_",
    "wg_old_",
    "wg_newer_",
    "wg_low_",
    "wg_high_",
    "wg_full_",
)


def is_test_profile_name(name: str | None) -> bool:
    if not name:
        return False
    if name.lower() == "default":
        return False
    if bool(TEST_NAME_RE.match(name)):
        return True
    return "DBTEST" in name.upper()


def is_test_wg_interface_name(name: str | None) -> bool:
    if not name:
        return False
    lower = name.lower()
    return any(lower.startswith(p) for p in TEST_WG_INTERFACE_PREFIXES)


def is_test_username(username: str | None) -> bool:
    if not username:
        return False
    if username.lower().startswith("dbtest_"):
        return True
    if bool(TEST_NAME_RE.match(username)):
        return True
    if username.lower().startswith("chaos_"):
        return True
    if re.fullmatch(r"u\d+", username, re.I):
        return True
    return False


def is_test_telegram_id(telegram_id: int | None) -> bool:
    if telegram_id is None:
        return False
    return TEST_TG_ID_MIN <= int(telegram_id) <= TEST_TG_ID_MAX


def is_test_user_row(user: Any) -> bool:
    return is_test_username(getattr(user, "username", None)) or is_test_telegram_id(
        getattr(user, "telegram_id", None)
    )


def is_purchasable_plan(profile: Any, *, server: Server | None = None) -> bool:
    """True when a plan may appear in user buy menus (active production server + name)."""
    if not profile or not getattr(profile, "is_active", True):
        return False
    if is_test_profile_name(getattr(profile, "name", None)):
        return False
    server_id = getattr(profile, "server_id", None)
    if not server_id:
        return False
    if server is None:
        return True
    if not server.is_active:
        return False
    return not is_test_server_row(server)


async def _active_production_server_ids(session) -> set[int]:
    from sqlalchemy import select

    servers = (await session.execute(select(Server))).scalars().all()
    return {s.id for s in servers if s.is_active and not is_test_server_row(s)}


async def list_purchasable_ovpn_profiles():
    """Active OVPN plans on production servers only (excludes DBTEST / pytest rows)."""
    from sqlalchemy import select

    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.models import Profile

    async with AsyncSessionLocal() as session:
        prod_ids = await _active_production_server_ids(session)
        if not prod_ids:
            return []
        result = await session.execute(
            select(Profile).where(
                Profile.is_active,
                Profile.server_id.in_(prod_ids),
            )
        )
        return [p for p in result.scalars().all() if not is_test_profile_name(p.name)]


async def list_purchasable_wg_profiles():
    """Active WireGuard plans on production servers only (excludes DBTEST / pytest rows)."""
    from sqlalchemy import select

    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.models import WireGuardProfile

    async with AsyncSessionLocal() as session:
        prod_ids = await _active_production_server_ids(session)
        if not prod_ids:
            return []
        result = await session.execute(
            select(WireGuardProfile).where(
                WireGuardProfile.is_active,
                WireGuardProfile.server_id.in_(prod_ids),
            )
        )
        return [p for p in result.scalars().all() if not is_test_profile_name(p.name)]


async def assert_purchasable_plan(session, profile) -> bool:
    """Load server and verify plan is allowed for user checkout."""
    if not is_purchasable_plan(profile):
        return False
    server = await session.get(Server, profile.server_id)
    return is_purchasable_plan(profile, server=server)


def _is_documentation_or_local_host(host: str) -> bool:
    if host in (
        "127.0.0.1",
        "localhost",
        "0.0.0.0",
        "192.0.2.10",
        "198.51.100.1",
        "203.0.113.1",
    ):
        return True
    # RFC 5737 TEST-NET-3 (pytest inactive-server rows use 203.0.113.x)
    if host.startswith("203.0.113."):
        return True
    return False


def is_test_server_row(server: Server) -> bool:
    """True for pytest mocks and RFC5737 placeholders — not real router IPs."""
    name = server.name or ""
    host = (server.host or "").strip()

    if name.startswith("[DBTEST]") or "DBTEST" in name.upper():
        return True
    if (server.username or "").lower() == "mock":
        return True
    if _is_documentation_or_local_host(host):
        return True
    if "Real List" in name:
        return True
    if name.startswith("Prod Router") or name.startswith("Live Router"):
        return True
    if name.startswith("MikroTik Prod") or name.startswith("Inactive Prod"):
        return True
    if name.startswith("Router Main") or name.startswith("Inactive Router"):
        return True
    if name.startswith("DelTest") or name.startswith("DelOk"):
        return True
    if (server.location or "").lower() in ("pytest-db", "pytest", "test"):
        return True
    return False


async def purge_all_test_data(*, dry_run: bool = False) -> dict[str, int]:
    """
    Remove mock servers and associated test users, profiles, interfaces, subscriptions.
    Keeps production servers and real customer data.
    """
    from sqlalchemy import delete, select

    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.models import (
        OvpnConfig,
        PaymentReceipt,
        Profile,
        ReceiptNotification,
        Server,
        Subscription,
        Ticket,
        TicketMessage,
        Transaction,
        User,
        WireGuardInterface,
        WireGuardProfile,
        WireGuardSubscription,
    )

    stats = {
        "servers_deleted": 0,
        "servers_deactivated": 0,
        "wg_interfaces_deleted": 0,
        "wg_subs_deleted": 0,
        "ovpn_subs_deleted": 0,
        "wg_profiles_deleted": 0,
        "ovpn_profiles_deleted": 0,
        "users_deleted": 0,
        "receipts_deleted": 0,
        "transactions_deleted": 0,
        "tickets_deleted": 0,
    }

    async with AsyncSessionLocal() as session:
        servers = (await session.execute(select(Server))).scalars().all()
        test_server_ids = {s.id for s in servers if is_test_server_row(s)}

        ifaces = (await session.execute(select(WireGuardInterface))).scalars().all()
        test_iface_ids = {
            i.id
            for i in ifaces
            if i.server_id in test_server_ids or is_test_wg_interface_name(i.name)
        }

        users = (await session.execute(select(User))).scalars().all()
        test_user_ids = {u.id for u in users if is_test_user_row(u)}

        ovpn_profiles = (await session.execute(select(Profile))).scalars().all()
        test_ovpn_prof_ids = {
            p.id
            for p in ovpn_profiles
            if is_test_profile_name(p.name)
            or p.server_id in test_server_ids
        }

        wg_profiles = (await session.execute(select(WireGuardProfile))).scalars().all()
        test_wg_prof_ids = {
            p.id
            for p in wg_profiles
            if is_test_profile_name(p.name)
            or p.server_id in test_server_ids
        }

        if dry_run:
            stats["servers_deleted"] = len(test_server_ids)
            stats["wg_interfaces_deleted"] = len(test_iface_ids)
            stats["users_deleted"] = len(test_user_ids)
            stats["ovpn_profiles_deleted"] = len(test_ovpn_prof_ids)
            stats["wg_profiles_deleted"] = len(test_wg_prof_ids)
            return stats

        # Test-user financial rows (transactions before receipts — FK on transactions.receipt_id)
        if test_user_ids:
            txn_ids = (
                await session.execute(
                    select(Transaction.id).where(Transaction.user_id.in_(test_user_ids))
                )
            ).scalars().all()
            if txn_ids:
                from vpn_bot.models import DiscountRedemption

                await session.execute(
                    delete(DiscountRedemption).where(
                        DiscountRedemption.transaction_id.in_(txn_ids)
                    )
                )
                r = await session.execute(
                    delete(Transaction).where(Transaction.id.in_(txn_ids))
                )
                stats["transactions_deleted"] = r.rowcount or 0

            receipt_ids = (
                await session.execute(
                    select(PaymentReceipt.id).where(
                        PaymentReceipt.user_id.in_(test_user_ids)
                    )
                )
            ).scalars().all()
            if receipt_ids:
                await session.execute(
                    delete(DiscountRedemption).where(
                        DiscountRedemption.receipt_id.in_(receipt_ids)
                    )
                )
                await session.execute(
                    delete(ReceiptNotification).where(
                        ReceiptNotification.receipt_id.in_(receipt_ids)
                    )
                )
                r = await session.execute(
                    delete(PaymentReceipt).where(PaymentReceipt.id.in_(receipt_ids))
                )
                stats["receipts_deleted"] = r.rowcount or 0

            ticket_ids = (
                await session.execute(
                    select(Ticket.id).where(Ticket.user_id.in_(test_user_ids))
                )
            ).scalars().all()
            if ticket_ids:
                await session.execute(
                    delete(TicketMessage).where(TicketMessage.ticket_id.in_(ticket_ids))
                )
                r = await session.execute(delete(Ticket).where(Ticket.id.in_(ticket_ids)))
                stats["tickets_deleted"] = r.rowcount or 0

        # WG subscriptions (test iface / user / profile)
        wg_sub_q = select(WireGuardSubscription.id)
        wg_filters = []
        if test_iface_ids:
            wg_filters.append(WireGuardSubscription.interface_id.in_(test_iface_ids))
        if test_user_ids:
            wg_filters.append(WireGuardSubscription.user_id.in_(test_user_ids))
        if test_wg_prof_ids:
            wg_filters.append(WireGuardSubscription.profile_id.in_(test_wg_prof_ids))
        if wg_filters:
            from sqlalchemy import or_

            wg_sub_ids = (
                await session.execute(wg_sub_q.where(or_(*wg_filters)))
            ).scalars().all()
            if wg_sub_ids:
                r = await session.execute(
                    delete(WireGuardSubscription).where(
                        WireGuardSubscription.id.in_(wg_sub_ids)
                    )
                )
                stats["wg_subs_deleted"] = r.rowcount or 0

        # OVPN subscriptions
        ovpn_filters = []
        if test_server_ids:
            ovpn_filters.append(Subscription.server_id.in_(test_server_ids))
        if test_user_ids:
            ovpn_filters.append(Subscription.user_id.in_(test_user_ids))
        if test_ovpn_prof_ids:
            ovpn_filters.append(Subscription.profile_id.in_(test_ovpn_prof_ids))
        if ovpn_filters:
            from sqlalchemy import or_

            r = await session.execute(delete(Subscription).where(or_(*ovpn_filters)))
            stats["ovpn_subs_deleted"] = r.rowcount or 0

        if test_iface_ids:
            r = await session.execute(
                delete(WireGuardInterface).where(
                    WireGuardInterface.id.in_(test_iface_ids)
                )
            )
            stats["wg_interfaces_deleted"] = r.rowcount or 0

        if test_wg_prof_ids:
            await session.execute(
                delete(WireGuardProfile).where(
                    WireGuardProfile.id.in_(test_wg_prof_ids)
                )
            )
            stats["wg_profiles_deleted"] = len(test_wg_prof_ids)

        if test_ovpn_prof_ids:
            await session.execute(
                delete(Profile).where(Profile.id.in_(test_ovpn_prof_ids))
            )
            stats["ovpn_profiles_deleted"] = len(test_ovpn_prof_ids)

        if test_server_ids:
            await session.execute(
                delete(OvpnConfig).where(OvpnConfig.server_id.in_(test_server_ids))
            )
            for sid in test_server_ids:
                srv = await session.get(Server, sid)
                if srv:
                    await session.delete(srv)
            stats["servers_deleted"] = len(test_server_ids)

        # Remaining mock servers without [DBTEST] prefix but localhost
        for s in servers:
            if s.id in test_server_ids:
                continue
            if is_mock_or_test_server(s) and s.is_active:
                s.is_active = False
                stats["servers_deactivated"] += 1

        if test_user_ids:
            # Re-check: only delete users with no remaining subs
            remaining_ovpn = (
                await session.execute(
                    select(Subscription.user_id).where(
                        Subscription.user_id.in_(test_user_ids)
                    )
                )
            ).scalars().all()
            remaining_wg = (
                await session.execute(
                    select(WireGuardSubscription.user_id).where(
                        WireGuardSubscription.user_id.in_(test_user_ids)
                    )
                )
            ).scalars().all()
            still_linked = set(remaining_ovpn) | set(remaining_wg)
            safe_user_ids = [uid for uid in test_user_ids if uid not in still_linked]
            if safe_user_ids:
                r = await session.execute(
                    delete(User).where(User.id.in_(safe_user_ids))
                )
                stats["users_deleted"] = r.rowcount or 0

        await session.commit()

    return stats


async def purge_test_plans_only(*, dry_run: bool = False) -> dict[str, int]:
    """Remove pytest / DBTEST OVPN & WireGuard plans and their subscriptions."""
    from sqlalchemy import delete, select

    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.models import Profile, Subscription, WireGuardProfile, WireGuardSubscription

    stats = {"ovpn_profiles_deleted": 0, "wg_profiles_deleted": 0, "ovpn_subs_deleted": 0, "wg_subs_deleted": 0}

    async with AsyncSessionLocal() as session:
        ovpn_rows = (await session.execute(select(Profile))).scalars().all()
        wg_rows = (await session.execute(select(WireGuardProfile))).scalars().all()
        servers = (await session.execute(select(Server))).scalars().all()
        test_server_ids = {s.id for s in servers if is_test_server_row(s)}

        test_ovpn_ids = [
            p.id
            for p in ovpn_rows
            if is_test_profile_name(p.name) or p.server_id in test_server_ids
        ]
        test_wg_ids = [
            p.id
            for p in wg_rows
            if is_test_profile_name(p.name) or p.server_id in test_server_ids
        ]

        if dry_run:
            stats["ovpn_profiles_deleted"] = len(test_ovpn_ids)
            stats["wg_profiles_deleted"] = len(test_wg_ids)
            return stats

        if test_ovpn_ids:
            r = await session.execute(
                delete(Subscription).where(Subscription.profile_id.in_(test_ovpn_ids))
            )
            stats["ovpn_subs_deleted"] = r.rowcount or 0
            r = await session.execute(delete(Profile).where(Profile.id.in_(test_ovpn_ids)))
            stats["ovpn_profiles_deleted"] = r.rowcount or 0

        if test_wg_ids:
            r = await session.execute(
                delete(WireGuardSubscription).where(
                    WireGuardSubscription.profile_id.in_(test_wg_ids)
                )
            )
            stats["wg_subs_deleted"] = r.rowcount or 0
            r = await session.execute(
                delete(WireGuardProfile).where(WireGuardProfile.id.in_(test_wg_ids))
            )
            stats["wg_profiles_deleted"] = r.rowcount or 0

        await session.commit()

    return stats


async def purge_all_servers(*, dry_run: bool = False) -> dict[str, int]:
    """
    Remove every server row and server-scoped dependencies (subs, plans, WG ifaces).
    Use for DB reset before adding a single production router.
    """
    from sqlalchemy import delete, select

    from vpn_bot.database import AsyncSessionLocal
    from vpn_bot.models import (
        OvpnConfig,
        Profile,
        Server,
        Subscription,
        WireGuardInterface,
        WireGuardProfile,
        WireGuardSubscription,
    )

    stats = {
        "servers_deleted": 0,
        "wg_interfaces_deleted": 0,
        "wg_subs_deleted": 0,
        "ovpn_subs_deleted": 0,
        "wg_profiles_deleted": 0,
        "ovpn_profiles_deleted": 0,
    }

    async with AsyncSessionLocal() as session:
        servers = (await session.execute(select(Server))).scalars().all()
        server_ids = {s.id for s in servers}
        if not server_ids:
            return stats

        iface_ids = (
            await session.execute(
                select(WireGuardInterface.id).where(
                    WireGuardInterface.server_id.in_(server_ids)
                )
            )
        ).scalars().all()
        iface_id_set = set(iface_ids)

        wg_prof_ids = (
            await session.execute(
                select(WireGuardProfile.id).where(
                    WireGuardProfile.server_id.in_(server_ids)
                )
            )
        ).scalars().all()
        ovpn_prof_ids = (
            await session.execute(
                select(Profile.id).where(Profile.server_id.in_(server_ids))
            )
        ).scalars().all()

        if dry_run:
            stats["servers_deleted"] = len(server_ids)
            stats["wg_interfaces_deleted"] = len(iface_id_set)
            stats["wg_profiles_deleted"] = len(wg_prof_ids)
            stats["ovpn_profiles_deleted"] = len(ovpn_prof_ids)
            return stats

        from sqlalchemy import or_

        wg_sub_filters = []
        if iface_id_set:
            wg_sub_filters.append(
                WireGuardSubscription.interface_id.in_(iface_id_set)
            )
        if wg_prof_ids:
            wg_sub_filters.append(
                WireGuardSubscription.profile_id.in_(wg_prof_ids)
            )
        if wg_sub_filters:
            r = await session.execute(
                delete(WireGuardSubscription).where(or_(*wg_sub_filters))
            )
            stats["wg_subs_deleted"] = r.rowcount or 0

        r = await session.execute(
            delete(Subscription).where(Subscription.server_id.in_(server_ids))
        )
        stats["ovpn_subs_deleted"] = r.rowcount or 0

        if iface_id_set:
            r = await session.execute(
                delete(WireGuardInterface).where(
                    WireGuardInterface.id.in_(iface_id_set)
                )
            )
            stats["wg_interfaces_deleted"] = r.rowcount or 0

        if wg_prof_ids:
            await session.execute(
                delete(WireGuardProfile).where(WireGuardProfile.id.in_(wg_prof_ids))
            )
            stats["wg_profiles_deleted"] = len(wg_prof_ids)

        if ovpn_prof_ids:
            await session.execute(delete(Profile).where(Profile.id.in_(ovpn_prof_ids)))
            stats["ovpn_profiles_deleted"] = len(ovpn_prof_ids)

        await session.execute(
            delete(OvpnConfig).where(OvpnConfig.server_id.in_(server_ids))
        )

        for srv in servers:
            await session.delete(srv)
        stats["servers_deleted"] = len(server_ids)

        await session.commit()

    try:
        from vpn_bot.mt_cache import mt_cache

        mt_cache.clear()
    except Exception:
        pass

    return stats
