"""Purchase flow phase enums and guard helpers (foundation for state-machine refactor)."""

from __future__ import annotations

from enum import Enum


class PurchasePhase(str, Enum):
    IDLE = "idle"
    TERMS = "terms"
    COUPON = "coupon"
    PAY = "pay"
    PROVISION = "provision"
    DELIVER = "deliver"


class ReceiptPhase(str, Enum):
    PENDING = "pending"
    APPROVED = "approved"
    FULFILLED = "fulfilled"


def can_enter_pay(phase: PurchasePhase, *, terms_accepted: bool, banned: bool) -> bool:
    if banned:
        return False
    if phase not in (PurchasePhase.COUPON, PurchasePhase.PAY):
        return False
    return terms_accepted


def can_confirm_pay(phase: PurchasePhase, *, banned: bool, balance_ok: bool) -> bool:
    return phase == PurchasePhase.PAY and not banned and balance_ok
