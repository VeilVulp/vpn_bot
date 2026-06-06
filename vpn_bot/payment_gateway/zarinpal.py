"""ZarinPal payment gateway stub — wire when business enables online payments."""

from __future__ import annotations

import os
from typing import Any

from vpn_bot.payment_gateway.base import PaymentGateway, PaymentResult


class ZarinPalGateway(PaymentGateway):
    """Create/verify payments via ZarinPal REST API (not active until configured)."""

    def __init__(self, merchant_id: str | None = None, *, sandbox: bool = False):
        self.merchant_id = (merchant_id or os.getenv("ZARINPAL_MERCHANT_ID", "")).strip()
        self.sandbox = sandbox or os.getenv("ZARINPAL_SANDBOX", "false").lower() == "true"
        self._base = (
            "https://sandbox.zarinpal.com/pg/v4/payment"
            if self.sandbox
            else "https://payment.zarinpal.com/pg/v4/payment"
        )

    def is_configured(self) -> bool:
        return bool(self.merchant_id)

    async def create_payment(self, *, user_id: int, amount: float, context: str) -> PaymentResult:
        if not self.is_configured():
            return PaymentResult(
                success=False,
                detail={"reason": "zarinpal_not_configured", "user_id": user_id, "context": context},
            )
        # TODO: POST /request.json with amount (Rial), callback_url, description
        return PaymentResult(
            success=False,
            detail={"reason": "not_implemented", "amount": amount, "endpoint": self._base},
        )

    async def verify_webhook(self, payload: dict[str, Any]) -> PaymentResult:
        if not self.is_configured():
            return PaymentResult(success=False, detail={"reason": "zarinpal_not_configured"})
        authority = payload.get("Authority") or payload.get("authority")
        status = payload.get("Status") or payload.get("status")
        if status != "OK" or not authority:
            return PaymentResult(success=False, detail={"reason": "payment_cancelled", "payload": payload})
        # TODO: POST /verify.json with authority + amount
        return PaymentResult(success=False, reference=str(authority), detail={"reason": "verify_not_implemented"})
