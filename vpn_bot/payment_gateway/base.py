"""Payment gateway abstraction — manual receipt + wallet is the default implementation."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class PaymentResult:
    success: bool
    reference: str | None = None
    detail: dict[str, Any] | None = None


class PaymentGateway(ABC):
    """Common interface for future online gateways (ZarinPal, crypto, etc.)."""

    @abstractmethod
    async def create_payment(self, *, user_id: int, amount: float, context: str) -> PaymentResult:
        raise NotImplementedError

    @abstractmethod
    async def verify_webhook(self, payload: dict[str, Any]) -> PaymentResult:
        raise NotImplementedError
