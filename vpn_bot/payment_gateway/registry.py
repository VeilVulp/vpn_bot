"""Resolve active payment gateway from environment."""

from __future__ import annotations

import os

from vpn_bot.payment_gateway.base import PaymentGateway
from vpn_bot.payment_gateway.manual import ManualReceiptGateway
from vpn_bot.payment_gateway.zarinpal import ZarinPalGateway


def get_payment_gateway() -> PaymentGateway:
    """Return configured gateway; defaults to manual receipt + wallet flow."""
    provider = os.getenv("PAYMENT_GATEWAY", "manual").strip().lower()
    if provider == "zarinpal":
        gw = ZarinPalGateway()
        if gw.is_configured():
            return gw
    return ManualReceiptGateway()
