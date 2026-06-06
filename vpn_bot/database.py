import os

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from vpn_bot.config import config

_pool_size = int(os.getenv("DB_POOL_SIZE", "10"))
_max_overflow = int(os.getenv("DB_MAX_OVERFLOW", "20"))

# Create async engine with PostgreSQL connection pool settings
engine = create_async_engine(
    config.DATABASE_URL,
    echo=config.DEBUG,
    future=True,
    pool_pre_ping=True,
    pool_size=_pool_size,
    max_overflow=_max_overflow,
    pool_timeout=int(os.getenv("DB_POOL_TIMEOUT", "30")),
)

# Async session factory
AsyncSessionLocal = sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False
)

Base = declarative_base()

async def get_db():
    """Dependency for getting async database sessions."""
    async with AsyncSessionLocal() as session:
        yield session

async def _migrate_receipt_notifications_bigint(conn):
    """Upgrade receipt_notifications.admin_id/message_id to BIGINT (Telegram ids > int32)."""
    import logging
    from sqlalchemy import text

    log = logging.getLogger("vpn_bot")
    for col in ("admin_id", "message_id"):
        try:
            await conn.execute(
                text(
                    f"ALTER TABLE receipt_notifications "
                    f"ALTER COLUMN {col} TYPE BIGINT USING {col}::bigint"
                )
            )
            log.info("Migrated receipt_notifications.%s to BIGINT", col)
        except Exception as exc:
            msg = str(exc).lower()
            if "does not exist" in msg or "already" in msg:
                log.debug("receipt_notifications.%s migration skipped: %s", col, exc)
            else:
                log.warning("receipt_notifications.%s migration: %s", col, exc)


async def _migrate_wg_nat_routing_mark(conn):
    """Add wireguard_interfaces.nat_routing_mark for NAT General tab match field."""
    import logging
    from sqlalchemy import text

    log = logging.getLogger("vpn_bot")
    try:
        await conn.execute(
            text(
                "ALTER TABLE wireguard_interfaces "
                "ADD COLUMN IF NOT EXISTS nat_routing_mark VARCHAR(50)"
            )
        )
        log.info("Ensured wireguard_interfaces.nat_routing_mark column exists")
    except Exception as exc:
        log.warning("wireguard_interfaces.nat_routing_mark migration: %s", exc)


async def _migrate_wg_nat_dst_negate(conn):
    import logging
    from sqlalchemy import text

    log = logging.getLogger("vpn_bot")
    try:
        await conn.execute(
            text(
                "ALTER TABLE wireguard_interfaces "
                "ADD COLUMN IF NOT EXISTS nat_dst_negate BOOLEAN DEFAULT TRUE"
            )
        )
        log.info("Ensured wireguard_interfaces.nat_dst_negate column exists")
    except Exception as exc:
        log.warning("wireguard_interfaces.nat_dst_negate migration: %s", exc)


async def _migrate_wg_route_list_fields(conn):
    """Add wireguard_interfaces route list fields (table, dst, distance)."""
    import logging
    from sqlalchemy import text

    log = logging.getLogger("vpn_bot")
    for stmt in (
        "ALTER TABLE wireguard_interfaces "
        "ADD COLUMN IF NOT EXISTS route_table VARCHAR(50)",
        "ALTER TABLE wireguard_interfaces "
        "ADD COLUMN IF NOT EXISTS route_dst_address VARCHAR(50) DEFAULT '0.0.0.0/0'",
        "ALTER TABLE wireguard_interfaces "
        "ADD COLUMN IF NOT EXISTS route_distance INTEGER DEFAULT 1",
    ):
        try:
            await conn.execute(text(stmt))
        except Exception as exc:
            log.warning("wireguard_interfaces route list migration: %s", exc)
    try:
        await conn.execute(
            text(
                "UPDATE wireguard_interfaces SET route_table = routing_mark "
                "WHERE route_table IS NULL AND routing_mark IS NOT NULL AND gateway IS NOT NULL"
            )
        )
        log.info("Ensured wireguard_interfaces route list columns exist")
    except Exception as exc:
        log.warning("wireguard_interfaces route_table backfill: %s", exc)


async def _migrate_wg_nat_dst_address_list(conn):
    import logging
    from sqlalchemy import text

    log = logging.getLogger("vpn_bot")
    try:
        await conn.execute(
            text(
                "ALTER TABLE wireguard_interfaces "
                "ADD COLUMN IF NOT EXISTS nat_dst_address_list VARCHAR(80)"
            )
        )
        log.info("Ensured wireguard_interfaces.nat_dst_address_list column exists")
    except Exception as exc:
        log.warning("wireguard_interfaces.nat_dst_address_list migration: %s", exc)


async def _migrate_user_purchase_terms_fields(conn):
    """Add users.purchase_terms_* columns for terms acceptance tracking."""
    import logging
    from sqlalchemy import text

    log = logging.getLogger("vpn_bot")
    for stmt in (
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS purchase_terms_accepted_at TIMESTAMPTZ",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS purchase_terms_version VARCHAR(20)",
    ):
        try:
            await conn.execute(text(stmt))
        except Exception as exc:
            log.warning("users purchase_terms migration: %s", exc)
    log.info("Ensured users purchase_terms columns exist")


async def _migrate_admins_permissions_json(conn):
    """Add admins.permissions_json if missing (RBAC)."""
    import logging
    from sqlalchemy import text

    log = logging.getLogger("vpn_bot")
    try:
        await conn.execute(
            text("ALTER TABLE admins ADD COLUMN IF NOT EXISTS permissions_json VARCHAR")
        )
        log.info("Ensured admins.permissions_json column exists")
    except Exception as exc:
        msg = str(exc).lower()
        if "does not exist" in msg or "already" in msg or "duplicate" in msg:
            log.debug("admins.permissions_json migration skipped: %s", exc)
        else:
            log.warning("admins.permissions_json migration: %s", exc)


async def _migrate_transactions_receipt_id(conn):
    """Add transactions.receipt_id for linking wallet credits to payment receipts."""
    import logging
    from sqlalchemy import text

    log = logging.getLogger("vpn_bot")
    try:
        await conn.execute(
            text(
                "ALTER TABLE transactions "
                "ADD COLUMN IF NOT EXISTS receipt_id INTEGER "
                "REFERENCES payment_receipts(id)"
            )
        )
        log.info("Ensured transactions.receipt_id column exists")
    except Exception as exc:
        log.warning("transactions.receipt_id migration: %s", exc)


async def _migrate_discount_code_fields(conn):
    """Add discount code tables/columns for coupon system."""
    import logging
    from sqlalchemy import text

    log = logging.getLogger("vpn_bot")
    for stmt in (
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS discount_code_id INTEGER",
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS original_amount DOUBLE PRECISION",
        "ALTER TABLE transactions ADD COLUMN IF NOT EXISTS discount_amount DOUBLE PRECISION",
        "ALTER TABLE payment_receipts ADD COLUMN IF NOT EXISTS discount_code_id INTEGER",
        "ALTER TABLE payment_receipts ADD COLUMN IF NOT EXISTS credit_amount DOUBLE PRECISION",
        "ALTER TABLE payment_receipts ADD COLUMN IF NOT EXISTS payable_amount DOUBLE PRECISION",
    ):
        try:
            await conn.execute(text(stmt))
        except Exception as exc:
            log.warning("discount migration: %s", exc)
    log.info("Ensured discount code columns exist")


async def _migrate_reporting_indexes(conn):
    """Composite indexes for user history and subscription lookups."""
    import logging
    from sqlalchemy import text

    log = logging.getLogger("vpn_bot")
    for stmt in (
        "CREATE INDEX IF NOT EXISTS ix_transactions_user_created ON transactions (user_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS ix_subscriptions_user_status ON subscriptions (user_id, status)",
    ):
        try:
            await conn.execute(text(stmt))
        except Exception as exc:
            log.warning("reporting index migration: %s", exc)
    log.info("Ensured reporting composite indexes exist")


async def init_db():
    """Initialize database tables."""

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _migrate_receipt_notifications_bigint(conn)
        await _migrate_wg_nat_routing_mark(conn)
        await _migrate_wg_nat_dst_address_list(conn)
        await _migrate_wg_nat_dst_negate(conn)
        await _migrate_wg_route_list_fields(conn)
        await _migrate_admins_permissions_json(conn)
        await _migrate_user_purchase_terms_fields(conn)
        await _migrate_transactions_receipt_id(conn)
        await _migrate_discount_code_fields(conn)
        await _migrate_reporting_indexes(conn)
