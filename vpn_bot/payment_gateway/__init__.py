"""Payment gateway package."""

from vpn_bot.payment_gateway.base import PaymentGateway, PaymentResult
from vpn_bot.payment_gateway.manual import ManualReceiptGateway
from vpn_bot.payment_gateway.registry import get_payment_gateway
from vpn_bot.payment_gateway.zarinpal import ZarinPalGateway

__all__ = [
    "PaymentGateway",
    "PaymentResult",
    "ManualReceiptGateway",
    "ZarinPalGateway",
    "get_payment_gateway",
]
