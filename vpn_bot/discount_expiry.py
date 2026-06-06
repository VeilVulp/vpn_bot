"""Discount code expiry duration validation and computation."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Literal

ExpiryUnit = Literal["minute", "hour", "day", "week", "month", "year"]

EXPIRY_UNIT_LIMITS: dict[str, tuple[int, int]] = {
    "minute": (1, 60),
    "hour": (1, 24),
    "day": (1, 30),
    "week": (1, 4),
    "month": (1, 12),
    "year": (1, 5),
}

EXPIRY_UNITS: tuple[str, ...] = tuple(EXPIRY_UNIT_LIMITS.keys())


def validate_expiry_amount(unit: str, amount: int) -> bool:
    if unit not in EXPIRY_UNIT_LIMITS:
        return False
    lo, hi = EXPIRY_UNIT_LIMITS[unit]
    return lo <= amount <= hi


def compute_valid_until(
    unit: str,
    amount: int,
    *,
    now: datetime | None = None,
) -> datetime:
    if not validate_expiry_amount(unit, amount):
        raise ValueError("invalid_expiry_amount")
    base = now or datetime.now(timezone.utc)
    if base.tzinfo is None:
        base = base.replace(tzinfo=timezone.utc)
    if unit == "minute":
        delta = timedelta(minutes=amount)
    elif unit == "hour":
        delta = timedelta(hours=amount)
    elif unit == "day":
        delta = timedelta(days=amount)
    elif unit == "week":
        delta = timedelta(weeks=amount)
    elif unit == "month":
        delta = timedelta(days=amount * 30)
    elif unit == "year":
        delta = timedelta(days=amount * 365)
    else:
        raise ValueError("invalid_expiry_unit")
    return base + delta
