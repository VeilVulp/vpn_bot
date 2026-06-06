"""Admin CRUD for discount codes."""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from vpn_bot.database import AsyncSessionLocal
from vpn_bot.discount_service import is_valid_code_format, normalize_code
from vpn_bot.models import DiscountCode, DiscountRedemption, PaymentReceipt, Transaction


def _discount_list_query(*, active_only: bool | None, order: Literal["asc", "desc"]):
    q = select(DiscountCode)
    if active_only is True:
        q = q.where(DiscountCode.is_active.is_(True))
    elif active_only is False:
        q = q.where(DiscountCode.is_active.is_(False))
    if order == "asc":
        q = q.order_by(DiscountCode.id.asc())
    else:
        q = q.order_by(DiscountCode.id.desc())
    return q


async def list_discount_codes(
    limit: int = 20,
    offset: int = 0,
    *,
    active_only: bool | None = None,
    order: Literal["asc", "desc"] = "asc",
) -> list[DiscountCode]:
    async with AsyncSessionLocal() as session:
        q = _discount_list_query(active_only=active_only, order=order)
        result = await session.execute(q.limit(limit).offset(offset))
        return list(result.scalars().all())


async def count_discount_codes(*, active_only: bool | None = None) -> int:
    async with AsyncSessionLocal() as session:
        q = select(func.count(DiscountCode.id))
        if active_only is True:
            q = q.where(DiscountCode.is_active.is_(True))
        elif active_only is False:
            q = q.where(DiscountCode.is_active.is_(False))
        result = await session.execute(q)
        return int(result.scalar() or 0)


async def get_discount_code(code_id: int) -> Optional[DiscountCode]:
    async with AsyncSessionLocal() as session:
        return await session.get(DiscountCode, code_id)


async def code_exists(code_str: str, exclude_id: int | None = None) -> bool:
    normalized = normalize_code(code_str)
    async with AsyncSessionLocal() as session:
        q = select(DiscountCode.id).where(DiscountCode.code == normalized)
        if exclude_id:
            q = q.where(DiscountCode.id != exclude_id)
        result = await session.execute(q)
        return result.scalars().first() is not None


async def has_redemptions(code_id: int) -> bool:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(func.count(DiscountRedemption.id)).where(
                DiscountRedemption.discount_code_id == code_id
            )
        )
        return int(result.scalar() or 0) > 0


async def create_discount_code(
    *,
    name: str,
    code: str,
    discount_type: str,
    value: float,
    currency_unit: str | None,
    max_uses_per_user: int,
    max_total_uses: int | None = None,
    valid_until: datetime | None = None,
) -> DiscountCode:
    normalized = normalize_code(code)
    if not is_valid_code_format(normalized):
        raise ValueError("invalid_code_format")
    if discount_type == "percent" and not (0 < value <= 100):
        raise ValueError("invalid_percent")
    if discount_type == "fixed" and value <= 0:
        raise ValueError("invalid_fixed")
    if max_uses_per_user < 1:
        raise ValueError("invalid_per_user_limit")

    async with AsyncSessionLocal() as session:
        if await _code_exists_session(session, normalized):
            raise ValueError("code_duplicate")
        row = DiscountCode(
            name=name.strip(),
            code=normalized,
            discount_type=discount_type,
            value=float(value),
            currency_unit=currency_unit if discount_type == "fixed" else None,
            max_uses_per_user=max_uses_per_user,
            max_total_uses=max_total_uses,
            valid_until=valid_until,
            is_active=True,
        )
        session.add(row)
        await session.commit()
        await session.refresh(row)
        return row


async def _code_exists_session(session: AsyncSession, normalized: str) -> bool:
    result = await session.execute(
        select(DiscountCode.id).where(DiscountCode.code == normalized)
    )
    return result.scalars().first() is not None


async def toggle_discount_code(code_id: int) -> Optional[DiscountCode]:
    async with AsyncSessionLocal() as session:
        row = await session.get(DiscountCode, code_id)
        if not row:
            return None
        row.is_active = not row.is_active
        await session.commit()
        await session.refresh(row)
        return row


async def update_discount_code(
    code_id: int,
    *,
    name: str | None = None,
    code: str | None = None,
    discount_type: str | None = None,
    value: float | None = None,
    currency_unit: str | None = None,
    max_uses_per_user: int | None = None,
    max_total_uses: int | None = None,
    is_active: bool | None = None,
    valid_until: datetime | None = None,
) -> Optional[DiscountCode]:
    async with AsyncSessionLocal() as session:
        row = await session.get(DiscountCode, code_id)
        if not row:
            return None
        used = await _redemption_count_session(session, code_id)

        if used:
            if name is not None:
                row.name = name.strip()
            if max_uses_per_user is not None and max_uses_per_user >= row.max_uses_per_user:
                row.max_uses_per_user = max_uses_per_user
            if max_total_uses is not None:
                if row.max_total_uses is None or max_total_uses >= row.max_total_uses:
                    row.max_total_uses = max_total_uses
            if is_active is not None:
                row.is_active = is_active
            if valid_until is not None:
                row.valid_until = valid_until
        else:
            if name is not None:
                row.name = name.strip()
            if code is not None:
                normalized = normalize_code(code)
                if not is_valid_code_format(normalized):
                    raise ValueError("invalid_code_format")
                if normalized != row.code and await _code_exists_session(session, normalized):
                    raise ValueError("code_duplicate")
                row.code = normalized
            if discount_type is not None:
                row.discount_type = discount_type
            if value is not None:
                row.value = float(value)
            if currency_unit is not None:
                row.currency_unit = currency_unit
            if max_uses_per_user is not None:
                row.max_uses_per_user = max_uses_per_user
            if max_total_uses is not None:
                row.max_total_uses = max_total_uses
            if is_active is not None:
                row.is_active = is_active
            if valid_until is not None:
                row.valid_until = valid_until

        await session.commit()
        await session.refresh(row)
        return row


async def _redemption_count_session(session: AsyncSession, code_id: int) -> int:
    result = await session.execute(
        select(func.count(DiscountRedemption.id)).where(
            DiscountRedemption.discount_code_id == code_id
        )
    )
    return int(result.scalar() or 0)


async def delete_discount_code(code_id: int) -> str:
    """
    Delete or deactivate a discount code.

    Returns:
        ``hard`` — row removed from DB (never used).
        ``soft`` — deactivated because redemptions exist.
        ``already_inactive`` — already inactive with usage history.
        ``missing`` — id not found.
    """
    async with AsyncSessionLocal() as session:
        row = await session.get(DiscountCode, code_id)
        if not row:
            return "missing"
        used = await _redemption_count_session(session, code_id)
        if used:
            if not row.is_active:
                return "already_inactive"
            row.is_active = False
            await session.commit()
            return "soft"
        await session.execute(delete(DiscountCode).where(DiscountCode.id == code_id))
        await session.commit()
        return "hard"


async def purge_all_discount_codes(*, dry_run: bool = False) -> dict[str, int]:
    """
    Remove all discount codes and related redemption rows.
    Clears discount_code_id on transactions and payment receipts first.
    """
    stats = {
        "redemptions_deleted": 0,
        "transactions_cleared": 0,
        "receipts_cleared": 0,
        "codes_deleted": 0,
    }
    async with AsyncSessionLocal() as session:
        redemptions = int(
            (await session.execute(select(func.count(DiscountRedemption.id)))).scalar() or 0
        )
        tx_refs = int(
            (
                await session.execute(
                    select(func.count(Transaction.id)).where(
                        Transaction.discount_code_id.isnot(None)
                    )
                )
            ).scalar()
            or 0
        )
        receipt_refs = int(
            (
                await session.execute(
                    select(func.count(PaymentReceipt.id)).where(
                        PaymentReceipt.discount_code_id.isnot(None)
                    )
                )
            ).scalar()
            or 0
        )
        codes = int((await session.execute(select(func.count(DiscountCode.id)))).scalar() or 0)

        stats["redemptions_deleted"] = redemptions
        stats["transactions_cleared"] = tx_refs
        stats["receipts_cleared"] = receipt_refs
        stats["codes_deleted"] = codes

        if dry_run:
            return stats

        if redemptions:
            await session.execute(delete(DiscountRedemption))
        if tx_refs:
            await session.execute(
                update(Transaction).where(Transaction.discount_code_id.isnot(None)).values(
                    discount_code_id=None
                )
            )
        if receipt_refs:
            await session.execute(
                update(PaymentReceipt)
                .where(PaymentReceipt.discount_code_id.isnot(None))
                .values(discount_code_id=None)
            )
        if codes:
            await session.execute(delete(DiscountCode))
        await session.commit()
        return stats
