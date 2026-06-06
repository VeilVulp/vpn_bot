"""Seed, snapshot, and mutate helpers for pg_dump/pg_restore roundtrip tests."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.models import (
    Admin,
    AdminSetting,
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
from vpn_bot.utils import utc_now

BACKUP_E2E_PREFIX = "BACKUP_E2E_"
BACKUP_JUNK_PREFIX = "BACKUP_JUNK_"
SERVER_PLAINTEXT_PASSWORD = "backup_secret_pw_123"

VOLATILE_COLUMNS = frozenset(
    {
        "created_at",
        "updated_at",
        "start_date",
        "submitted_at",
        "closed_at",
        "last_connected_at",
    }
)


@dataclass
class BackupSeedIds:
    server_id: int
    profile_id: int
    subscription_id: int
    wg_iface_id: int
    wg_profile_id: int
    wg_subscription_id: int
    user_active_id: int
    user_banned_id: int
    transaction_ids: list[int]
    receipt_pending_id: int
    receipt_approved_id: int
    receipt_notification_id: int
    ovpn_config_id: int
    ticket_id: int
    ticket_message_ids: list[int]
    admin_id: int
    admin_setting_keys: list[str]
    server_plaintext_password: str = SERVER_PLAINTEXT_PASSWORD


def _row_to_dict(row: Any, *, exclude_volatile: bool = True) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for col in row.__table__.columns:
        name = col.name
        if exclude_volatile and name in VOLATILE_COLUMNS:
            continue
        val = getattr(row, name, None)
        if hasattr(val, "isoformat"):
            val = val.isoformat()
        data[name] = val
    return data


async def cleanup_backup_e2e_rows(session: AsyncSession) -> None:
    """Remove prior BACKUP_E2E / BACKUP_JUNK rows from isolated backup DB."""
    junk_users = (
        await session.execute(
            select(User.id).where(
                User.username.like(f"{BACKUP_E2E_PREFIX}%")
                | User.username.like(f"{BACKUP_JUNK_PREFIX}%")
            )
        )
    ).scalars().all()
    junk_user_ids = list(junk_users)

    if junk_user_ids:
        receipt_ids = (
            await session.execute(
                select(PaymentReceipt.id).where(
                    PaymentReceipt.user_id.in_(junk_user_ids)
                )
            )
        ).scalars().all()
        if receipt_ids:
            await session.execute(
                delete(ReceiptNotification).where(
                    ReceiptNotification.receipt_id.in_(receipt_ids)
                )
            )
        ticket_ids = (
            await session.execute(
                select(Ticket.id).where(Ticket.user_id.in_(junk_user_ids))
            )
        ).scalars().all()
        if ticket_ids:
            await session.execute(
                delete(TicketMessage).where(TicketMessage.ticket_id.in_(ticket_ids))
            )
        await session.execute(
            delete(WireGuardSubscription).where(
                WireGuardSubscription.user_id.in_(junk_user_ids)
            )
        )
        await session.execute(
            delete(Subscription).where(Subscription.user_id.in_(junk_user_ids))
        )
        await session.execute(
            delete(Transaction).where(Transaction.user_id.in_(junk_user_ids))
        )
        await session.execute(
            delete(PaymentReceipt).where(PaymentReceipt.user_id.in_(junk_user_ids))
        )
        await session.execute(delete(Ticket).where(Ticket.user_id.in_(junk_user_ids)))
        await session.execute(delete(User).where(User.id.in_(junk_user_ids)))

    backup_servers = (
        await session.execute(
            select(Server.id).where(Server.name.like(f"{BACKUP_E2E_PREFIX}%"))
        )
    ).scalars().all()
    if backup_servers:
        await session.execute(
            delete(OvpnConfig).where(OvpnConfig.server_id.in_(backup_servers))
        )
        await session.execute(
            delete(Profile).where(Profile.server_id.in_(backup_servers))
        )
        await session.execute(
            delete(WireGuardSubscription).where(
                WireGuardSubscription.interface_id.in_(
                    select(WireGuardInterface.id).where(
                        WireGuardInterface.server_id.in_(backup_servers)
                    )
                )
            )
        )
        await session.execute(
            delete(WireGuardInterface).where(
                WireGuardInterface.server_id.in_(backup_servers)
            )
        )
        await session.execute(
            delete(WireGuardProfile).where(
                WireGuardProfile.server_id.in_(backup_servers)
            )
        )
        await session.execute(
            delete(Subscription).where(Subscription.server_id.in_(backup_servers))
        )
        await session.execute(delete(Server).where(Server.id.in_(backup_servers)))
    await session.execute(
        delete(Admin).where(Admin.username.like(f"{BACKUP_E2E_PREFIX}%"))
    )
    await session.execute(
        delete(AdminSetting).where(AdminSetting.key.like(f"{BACKUP_E2E_PREFIX}%"))
    )
    await session.commit()


async def seed_full_bot_dataset(session: AsyncSession) -> BackupSeedIds:
    """Insert representative rows across all bot tables."""
    await cleanup_backup_e2e_rows(session)

    now = utc_now()
    expiry = now + timedelta(days=30)

    server = Server(
        name=f"{BACKUP_E2E_PREFIX}server",
        host="10.99.0.1",
        port=8728,
        username="backup_bootuser",
        password=SERVER_PLAINTEXT_PASSWORD,
        is_active=True,
        location="backup-roundtrip",
    )
    session.add(server)
    await session.flush()

    ovpn_config = OvpnConfig(
        server_id=server.id,
        config_content="client\ndev tun\nremote backup.example.com",
        filename=f"{BACKUP_E2E_PREFIX}client.ovpn",
        display_name="Backup Test OVPN",
    )
    session.add(ovpn_config)
    await session.flush()

    profile = Profile(
        name=f"{BACKUP_E2E_PREFIX}ovpn_plan",
        price_usd=2.0,
        price_toman=25_000.0,
        validity_days=30,
        data_limit_gb=10,
        server_id=server.id,
        rate_limit="10M/10M",
        is_active=True,
    )
    session.add(profile)
    await session.flush()

    wg_profile = WireGuardProfile(
        name=f"{BACKUP_E2E_PREFIX}wg_plan",
        volume_gb=5,
        duration_days=30,
        price_toman=20_000,
        price_usd=1.5,
        rate_limit="8M/8M",
        is_active=True,
        server_id=server.id,
    )
    session.add(wg_profile)
    await session.flush()

    wg_iface = WireGuardInterface(
        server_id=server.id,
        name=f"{BACKUP_E2E_PREFIX}wg0",
        public_key="backup_pub_key_abc",
        private_key="backup_priv_key_xyz",
        address="10.88.1.1/24",
        listen_port=51999,
        max_users=10,
        current_users=1,
        is_active=True,
        endpoint_host="10.99.0.1",
    )
    session.add(wg_iface)
    await session.flush()

    user_active = User(
        telegram_id=750_000_000_001,
        username=f"{BACKUP_E2E_PREFIX}user_active",
        full_name="Backup Active User",
        wallet_balance=125_000.5,
        is_banned=False,
        phone_number="+989120000001",
    )
    user_banned = User(
        telegram_id=750_000_000_002,
        username=f"{BACKUP_E2E_PREFIX}user_banned",
        full_name="Backup Banned User",
        wallet_balance=3_500.0,
        is_banned=True,
    )
    session.add_all([user_active, user_banned])
    await session.flush()

    subscription = Subscription(
        user_id=user_active.id,
        server_id=server.id,
        profile_id=profile.id,
        mikrotik_username=f"{BACKUP_E2E_PREFIX}ovpn_user1",
        mikrotik_password="ovpn_pw_backup",
        status="active",
        expiry_date=expiry,
        total_limit_bytes=10 * 1024**3,
        used_bytes=512 * 1024**2,
    )
    session.add(subscription)
    await session.flush()

    wg_sub = WireGuardSubscription(
        user_id=user_active.id,
        interface_id=wg_iface.id,
        profile_id=wg_profile.id,
        unique_identifier=f"{BACKUP_E2E_PREFIX}WG-1",
        peer_public_key="wg_pub_backup_key",
        peer_private_key="wg_priv_backup_key",
        assigned_ip="10.88.1.2",
        comment_text=f"{BACKUP_E2E_PREFIX}wg comment",
        bytes_remaining=5 * 1024**3,
        status="active",
        expiry_date=expiry,
        total_bytes_rx=1000,
        total_bytes_tx=2000,
    )
    session.add(wg_sub)
    await session.flush()

    tx_deposit = Transaction(
        user_id=user_active.id,
        amount=50_000.0,
        type="deposit",
        description=f"{BACKUP_E2E_PREFIX}deposit",
        currency_unit="TOMAN",
    )
    tx_purchase = Transaction(
        user_id=user_active.id,
        amount=-25_000.0,
        type="purchase",
        description=f"{BACKUP_E2E_PREFIX}purchase",
        currency_unit="TOMAN",
    )
    tx_adjust = Transaction(
        user_id=user_banned.id,
        amount=500.0,
        type="manual_adjustment",
        description=f"{BACKUP_E2E_PREFIX}adjust",
        currency_unit="TOMAN",
    )
    session.add_all([tx_deposit, tx_purchase, tx_adjust])
    await session.flush()

    receipt_pending = PaymentReceipt(
        user_id=user_active.id,
        amount=10_000.0,
        receipt_file_id=f"{BACKUP_E2E_PREFIX}receipt_pending",
        status="pending",
        plan_id=profile.id,
        is_wireguard=False,
        currency_unit="TOMAN",
    )
    receipt_approved = PaymentReceipt(
        user_id=user_banned.id,
        amount=15_000.0,
        receipt_file_id=f"{BACKUP_E2E_PREFIX}receipt_approved",
        status="approved",
        plan_id=wg_profile.id,
        is_wireguard=True,
        currency_unit="TOMAN",
    )
    session.add_all([receipt_pending, receipt_approved])
    await session.flush()

    receipt_notif = ReceiptNotification(
        receipt_id=receipt_pending.id,
        admin_id=750_000_000_099,
        message_id=42_001,
    )
    session.add(receipt_notif)
    await session.flush()

    setting_interval = AdminSetting(
        key=f"{BACKUP_E2E_PREFIX}backup_interval_hours",
        value="12h",
    )
    setting_json = AdminSetting(
        key=f"{BACKUP_E2E_PREFIX}sample_json",
        value_json={"cards": ["6037-0000"], "presets": [10000, 25000]},
    )
    session.add_all([setting_interval, setting_json])
    await session.flush()

    ticket = Ticket(
        user_id=user_active.id,
        subject=f"{BACKUP_E2E_PREFIX}support issue",
        status="open",
        priority="high",
    )
    session.add(ticket)
    await session.flush()

    msg_user = TicketMessage(
        ticket_id=ticket.id,
        sender_type="user",
        sender_id=user_active.telegram_id,
        message=f"{BACKUP_E2E_PREFIX}user message",
    )
    msg_admin = TicketMessage(
        ticket_id=ticket.id,
        sender_type="admin",
        sender_id=750_000_000_099,
        message=f"{BACKUP_E2E_PREFIX}admin reply",
    )
    session.add_all([msg_user, msg_admin])
    await session.flush()

    admin = Admin(
        telegram_id=750_000_000_099,
        username=f"{BACKUP_E2E_PREFIX}admin",
        is_super=False,
        permissions_json=json.dumps(["backup", "users"]),
    )
    session.add(admin)
    await session.commit()

    return BackupSeedIds(
        server_id=server.id,
        profile_id=profile.id,
        subscription_id=subscription.id,
        wg_iface_id=wg_iface.id,
        wg_profile_id=wg_profile.id,
        wg_subscription_id=wg_sub.id,
        user_active_id=user_active.id,
        user_banned_id=user_banned.id,
        transaction_ids=[tx_deposit.id, tx_purchase.id, tx_adjust.id],
        receipt_pending_id=receipt_pending.id,
        receipt_approved_id=receipt_approved.id,
        receipt_notification_id=receipt_notif.id,
        ovpn_config_id=ovpn_config.id,
        ticket_id=ticket.id,
        ticket_message_ids=[msg_user.id, msg_admin.id],
        admin_id=admin.id,
        admin_setting_keys=[
            setting_interval.key,
            setting_json.key,
        ],
    )


async def capture_backup_snapshot(ids: BackupSeedIds) -> dict[str, Any]:
    """Capture normalized rows for all seeded entities."""
    async with AsyncSessionLocal() as session:
        snap: dict[str, Any] = {}

        server = await session.get(Server, ids.server_id)
        snap["server"] = _row_to_dict(server) if server else None
        if server:
            snap["server_password_plaintext"] = server.password

        for key, model, pk in [
            ("profile", Profile, ids.profile_id),
            ("subscription", Subscription, ids.subscription_id),
            ("wg_iface", WireGuardInterface, ids.wg_iface_id),
            ("wg_profile", WireGuardProfile, ids.wg_profile_id),
            ("wg_subscription", WireGuardSubscription, ids.wg_subscription_id),
            ("user_active", User, ids.user_active_id),
            ("user_banned", User, ids.user_banned_id),
            ("ovpn_config", OvpnConfig, ids.ovpn_config_id),
            ("ticket", Ticket, ids.ticket_id),
            ("admin", Admin, ids.admin_id),
        ]:
            row = await session.get(model, pk)
            snap[key] = _row_to_dict(row) if row else None

        snap["transactions"] = []
        for tid in ids.transaction_ids:
            row = await session.get(Transaction, tid)
            if row:
                snap["transactions"].append(_row_to_dict(row))
        snap["transactions"].sort(key=lambda r: r.get("type", ""))

        snap["receipts"] = []
        for rid in (ids.receipt_pending_id, ids.receipt_approved_id):
            row = await session.get(PaymentReceipt, rid)
            if row:
                snap["receipts"].append(_row_to_dict(row))
        snap["receipts"].sort(key=lambda r: r.get("status", ""))

        notif = await session.get(ReceiptNotification, ids.receipt_notification_id)
        snap["receipt_notification"] = _row_to_dict(notif) if notif else None

        snap["ticket_messages"] = []
        for mid in ids.ticket_message_ids:
            row = await session.get(TicketMessage, mid)
            if row:
                snap["ticket_messages"].append(_row_to_dict(row))
        snap["ticket_messages"].sort(key=lambda r: r.get("sender_type", ""))

        snap["admin_settings"] = []
        for skey in ids.admin_setting_keys:
            row = await session.get(AdminSetting, skey)
            if row:
                snap["admin_settings"].append(_row_to_dict(row))
        snap["admin_settings"].sort(key=lambda r: r.get("key", ""))

        return snap


async def mutate_database_after_backup(ids: BackupSeedIds) -> None:
    """Corrupt DB after backup to prove restore replaces state."""
    async with AsyncSessionLocal() as session:
        await session.execute(
            delete(Transaction).where(Transaction.user_id == ids.user_banned_id)
        )
        await session.execute(
            delete(PaymentReceipt).where(PaymentReceipt.id == ids.receipt_approved_id)
        )
        banned = await session.get(User, ids.user_banned_id)
        if banned:
            await session.delete(banned)

        active = await session.get(User, ids.user_active_id)
        if active:
            active.wallet_balance = 0.0

        junk = User(
            telegram_id=750_000_000_999,
            username=f"{BACKUP_JUNK_PREFIX}orphan",
            wallet_balance=1.0,
        )
        session.add(junk)
        await session.commit()


async def junk_user_exists() -> bool:
    async with AsyncSessionLocal() as session:
        res = await session.execute(
            select(User.id).where(User.username.like(f"{BACKUP_JUNK_PREFIX}%"))
        )
        return res.scalars().first() is not None


def assert_snapshots_equal(before: dict[str, Any], after: dict[str, Any]) -> None:
    """Raise AssertionError with table-level diff on mismatch."""
    mismatches: list[str] = []

    def _compare(label: str, a: Any, b: Any) -> None:
        if a != b:
            mismatches.append(f"{label}:\n  before={a!r}\n  after={b!r}")

    for key in sorted(set(before) | set(after)):
        if key == "server_password_plaintext":
            _compare(key, before.get(key), after.get(key))
            continue
        bv = before.get(key)
        av = after.get(key)
        if isinstance(bv, list) and isinstance(av, list):
            if bv != av:
                mismatches.append(f"{key}: list mismatch ({len(bv)} vs {len(av)})")
        else:
            _compare(key, bv, av)

    if mismatches:
        raise AssertionError("Snapshot mismatch after restore:\n" + "\n".join(mismatches))
