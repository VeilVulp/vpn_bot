"""Manual card-to-card receipt + wallet balance (current production flow)."""

from __future__ import annotations

from typing import Any

from vpn_bot.payment_gateway.base import PaymentGateway, PaymentResult


class ManualReceiptGateway(PaymentGateway):
    """Placeholder gateway — wallet deduction and receipt upload remain in bot_handler."""

    async def create_payment(self, *, user_id: int, amount: float, context: str) -> PaymentResult:
        return PaymentResult(
            success=False,
            detail={"reason": "manual_receipt_required", "user_id": user_id, "amount": amount, "context": context},
        )

    async def verify_webhook(self, payload: dict[str, Any]) -> PaymentResult:
        return PaymentResult(success=False, detail={"reason": "not_applicable", "payload_keys": list(payload.keys())})
