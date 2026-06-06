"""
Discount / coupon code validation and atomic redemption.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal, Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from vpn_bot.models import DiscountCode, DiscountRedemption, User

DiscountContext = Literal[
    "purchase_ovpn",
    "purchase_wg",
    "wallet_topup",
    "renew_ovpn",
    "renew_wg",
]

CODE_PATTERN = re.compile(r"^[A-Z0-9_-]{4,32}$")
COUPON_SESSION_TTL_SECONDS = 30 * 60
PREVIEW_FAIL_LIMIT = 10
PREVIEW_FAIL_WINDOW_SECONDS = 5 * 60

PURCHASE_CONTEXTS = frozenset({"purchase_ovpn", "purchase_wg", "renew_ovpn", "renew_wg"})
WALLET_CONTEXTS = frozenset({"wallet_topup"})


@dataclass(frozen=True)
class CouponValidationResult:
    ok: bool
    error_key: str = "coupon.invalid"
    code_id: Optional[int] = None
    code: Optional[str] = None
    original_amount: float = 0.0
    discount_amount: float = 0.0
    final_amount: float = 0.0


@dataclass(frozen=True)
class PricingResult:
    base_amount: float
    discount_amount: float
    final_amount: float
    currency_unit: str
    discount_code_id: Optional[int] = None


def normalize_code(raw: str) -> str:
    return (raw or "").strip().upper()


def is_valid_code_format(code: str) -> bool:
    return bool(CODE_PATTERN.match(code))


def convert_fixed_discount(value: float, from_unit: str, to_unit: str) -> Optional[float]:
    """Convert fixed discount between currency units. Returns None if incompatible."""
    from_u = (from_unit or "USD").upper()
    to_u = (to_unit or "USD").upper()
    if from_u == to_u:
        return float(value)
    if from_u == "TOMAN" and to_u == "RIAL":
        return float(value) * 10
    if from_u == "RIAL" and to_u == "TOMAN":
        return float(value) / 10
    return None


def compute_discounted_amount(
    base: float,
    code: DiscountCode,
    currency: str,
) -> tuple[float, float]:
    """Return (final_amount, discount_amount)."""
    base = max(0.0, float(base))
    if code.discount_type == "percent":
        pct = min(100.0, max(0.0, float(code.value)))
        discount = round(base * pct / 100.0, 2)
    else:
        converted = convert_fixed_discount(code.value, code.currency_unit or "USD", currency)
        if converted is None:
            raise ValueError("currency_mismatch")
        discount = min(base, float(converted))
    final = max(0.0, round(base - discount, 2))
    return final, discount


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def is_code_temporally_valid(code: DiscountCode, now: Optional[datetime] = None) -> bool:
    now = now or _utc_now()
    vf = _aware(code.valid_from)
    vu = _aware(code.valid_until)
    if vf and now < vf:
        return False
    if vu and now > vu:
        return False
    return True


async def get_user_redemption_count(
    session: AsyncSession,
    code_id: int,
    user_id: int,
) -> int:
    result = await session.execute(
        select(func.count(DiscountRedemption.id)).where(
            DiscountRedemption.discount_code_id == code_id,
            DiscountRedemption.user_id == user_id,
        )
    )
    return int(result.scalar() or 0)


async def fetch_discount_code_by_str(
    session: AsyncSession,
    code_str: str,
) -> Optional[DiscountCode]:
    normalized = normalize_code(code_str)
    if not normalized:
        return None
    result = await session.execute(
        select(DiscountCode).where(DiscountCode.code == normalized)
    )
    return result.scalars().first()


async def fetch_discount_code_locked(
    session: AsyncSession,
    code_id: int,
) -> Optional[DiscountCode]:
    result = await session.execute(
        select(DiscountCode).where(DiscountCode.id == code_id).with_for_update()
    )
    return result.scalars().first()


def check_preview_rate_limit(user_data: dict) -> bool:
    """Return True if rate limit exceeded."""
    now = _utc_now().timestamp()
    bucket = user_data.setdefault("_coupon_fail", {"count": 0, "window_start": now})
    if now - bucket["window_start"] > PREVIEW_FAIL_WINDOW_SECONDS:
        bucket["count"] = 0
        bucket["window_start"] = now
    return bucket["count"] >= PREVIEW_FAIL_LIMIT


def record_preview_failure(user_data: dict) -> None:
    now = _utc_now().timestamp()
    bucket = user_data.setdefault("_coupon_fail", {"count": 0, "window_start": now})
    if now - bucket["window_start"] > PREVIEW_FAIL_WINDOW_SECONDS:
        bucket["count"] = 0
        bucket["window_start"] = now
    bucket["count"] += 1


def active_coupon_from_session(user_data: dict) -> Optional[dict]:
    coupon = user_data.get("active_coupon")
    if not coupon or not isinstance(coupon, dict):
        return None
    validated_at = coupon.get("validated_at")
    if validated_at is None:
        return None
    if _utc_now().timestamp() - float(validated_at) > COUPON_SESSION_TTL_SECONDS:
        user_data.pop("active_coupon", None)
        return None
    return coupon


def store_active_coupon(
    user_data: dict,
    *,
    code_id: int,
    code: str,
    scope: str,
) -> None:
    user_data["active_coupon"] = {
        "code_id": code_id,
        "code": code,
        "scope": scope,
        "validated_at": _utc_now().timestamp(),
    }


def clear_active_coupon(user_data: dict) -> None:
    user_data.pop("active_coupon", None)
    user_data.pop("pending_coupon_id", None)


async def validate_coupon_eligibility(
    session: AsyncSession,
    *,
    code_str: Optional[str] = None,
    code_id: Optional[int] = None,
    user_id: int,
) -> CouponValidationResult:
    """Check code exists, active, within limits — no amount/currency check."""
    if code_str is not None:
        normalized = normalize_code(code_str)
        if not is_valid_code_format(normalized):
            return CouponValidationResult(ok=False, error_key="coupon.invalid")
        code = await fetch_discount_code_by_str(session, normalized)
    elif code_id is not None:
        result = await session.execute(select(DiscountCode).where(DiscountCode.id == code_id))
        code = result.scalars().first()
    else:
        return CouponValidationResult(ok=False, error_key="coupon.invalid")

    if not code:
        return CouponValidationResult(ok=False, error_key="coupon.invalid")
    if not code.is_active:
        return CouponValidationResult(ok=False, error_key="coupon.expired")
    if not is_code_temporally_valid(code):
        return CouponValidationResult(ok=False, error_key="coupon.expired")

    user = await session.get(User, user_id)
    if not user or user.is_banned:
        return CouponValidationResult(ok=False, error_key="coupon.invalid")

    user_uses = await get_user_redemption_count(session, code.id, user_id)
    if user_uses >= code.max_uses_per_user:
        return CouponValidationResult(ok=False, error_key="coupon.limit_reached")

    if code.max_total_uses is not None and code.total_uses >= code.max_total_uses:
        return CouponValidationResult(ok=False, error_key="coupon.limit_reached")

    return CouponValidationResult(ok=True, code_id=code.id, code=code.code)


async def validate_coupon_for_use(
    session: AsyncSession,
    *,
    code_str: Optional[str] = None,
    code_id: Optional[int] = None,
    user_id: int,
    context: DiscountContext,
    base_amount: float,
    currency: str,
    lock_for_redeem: bool = False,
) -> CouponValidationResult:
    """Preview or pre-checkout validation (with optional row lock for redeem)."""
    if code_id is not None and lock_for_redeem:
        code = await fetch_discount_code_locked(session, code_id)
    elif code_str is not None:
        normalized = normalize_code(code_str)
        if not is_valid_code_format(normalized):
            return CouponValidationResult(ok=False, error_key="coupon.invalid")
        if lock_for_redeem:
            result = await session.execute(
                select(DiscountCode).where(DiscountCode.code == normalized).with_for_update()
            )
            code = result.scalars().first()
        else:
            code = await fetch_discount_code_by_str(session, normalized)
    elif code_id is not None:
        result = await session.execute(select(DiscountCode).where(DiscountCode.id == code_id))
        code = result.scalars().first()
    else:
        return CouponValidationResult(ok=False, error_key="coupon.invalid")

    if not code:
        return CouponValidationResult(ok=False, error_key="coupon.invalid")
    if not code.is_active:
        return CouponValidationResult(ok=False, error_key="coupon.expired")
    if not is_code_temporally_valid(code):
        return CouponValidationResult(ok=False, error_key="coupon.expired")

    user = await session.get(User, user_id)
    if not user or user.is_banned:
        return CouponValidationResult(ok=False, error_key="coupon.invalid")

    user_uses = await get_user_redemption_count(session, code.id, user_id)
    if user_uses >= code.max_uses_per_user:
        return CouponValidationResult(ok=False, error_key="coupon.limit_reached")

    if code.max_total_uses is not None and code.total_uses >= code.max_total_uses:
        return CouponValidationResult(ok=False, error_key="coupon.limit_reached")

    try:
        final, discount = compute_discounted_amount(base_amount, code, currency)
    except ValueError:
        return CouponValidationResult(ok=False, error_key="coupon.currency_mismatch")

    if final <= 0 and code.discount_type != "percent":
        return CouponValidationResult(ok=False, error_key="coupon.invalid")
    if code.discount_type == "percent" and float(code.value) >= 100:
        final = 0.0
        discount = float(base_amount)

    return CouponValidationResult(
        ok=True,
        code_id=code.id,
        code=code.code,
        original_amount=float(base_amount),
        discount_amount=discount,
        final_amount=final,
    )


async def redeem_coupon_atomic(
    session: AsyncSession,
    *,
    code_id: int,
    user_id: int,
    context: DiscountContext,
    original_amount: float,
    final_amount: float,
    discount_amount: float,
    currency: str,
    transaction_id: Optional[int] = None,
    receipt_id: Optional[int] = None,
) -> DiscountRedemption:
    """Must be called inside an open transaction after validation with lock."""
    code = await fetch_discount_code_locked(session, code_id)
    if not code:
        raise ValueError("coupon_not_found")

    revalidate = await validate_coupon_for_use(
        session,
        code_id=code_id,
        user_id=user_id,
        context=context,
        base_amount=original_amount,
        currency=currency,
        lock_for_redeem=True,
    )
    if not revalidate.ok:
        raise ValueError(revalidate.error_key)

    expected_final, expected_discount = compute_discounted_amount(original_amount, code, currency)
    if abs(expected_final - final_amount) > 0.01 or abs(expected_discount - discount_amount) > 0.01:
        raise ValueError("amount_mismatch")

    redemption = DiscountRedemption(
        discount_code_id=code.id,
        user_id=user_id,
        context=context,
        original_amount=original_amount,
        discount_amount=discount_amount,
        final_amount=final_amount,
        currency_unit=currency,
        transaction_id=transaction_id,
        receipt_id=receipt_id,
    )
    session.add(redemption)
    code.total_uses = (code.total_uses or 0) + 1
    await session.flush()
    return redemption


async def apply_coupon_to_amount(
    session: AsyncSession,
    *,
    user_id: int,
    base_amount: float,
    currency: str,
    context: DiscountContext,
    coupon_id: Optional[int] = None,
) -> PricingResult:
    if coupon_id is None:
        return PricingResult(
            base_amount=base_amount,
            discount_amount=0.0,
            final_amount=base_amount,
            currency_unit=currency,
        )
    result = await validate_coupon_for_use(
        session,
        code_id=coupon_id,
        user_id=user_id,
        context=context,
        base_amount=base_amount,
        currency=currency,
    )
    if not result.ok:
        raise ValueError(result.error_key)
    return PricingResult(
        base_amount=base_amount,
        discount_amount=result.discount_amount,
        final_amount=result.final_amount,
        currency_unit=currency,
        discount_code_id=result.code_id,
    )
