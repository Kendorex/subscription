from __future__ import annotations

import random
import uuid
from dataclasses import dataclass

from .gateway import (
    PaymentGateway,
    ChargeResult,
    PaymentTemporaryUnavailable,
    PaymentInsufficientFunds,
    PaymentDeclined,
)

@dataclass
class FakeGatewayConfig:
    p_success: float = 0.80
    p_insufficient: float = 0.10
    p_unavailable: float = 0.05
    p_declined: float = 0.05

class FakePaymentGateway(PaymentGateway):
    def __init__(self, cfg: FakeGatewayConfig | None = None):
        self.cfg = cfg or FakeGatewayConfig()

    def charge(
        self,
        *,
        token_ref: str,
        amount_cents: int,
        currency: str,
        idempotency_key: str,
        description: str,
    ) -> ChargeResult:
        r = random.random()

        if r < self.cfg.p_unavailable:
            raise PaymentTemporaryUnavailable("Gateway temporarily unavailable")

        if r < self.cfg.p_unavailable + self.cfg.p_insufficient:
            raise PaymentInsufficientFunds("Insufficient funds")

        if r < self.cfg.p_unavailable + self.cfg.p_insufficient + self.cfg.p_declined:
            raise PaymentDeclined("Payment declined")

        return ChargeResult(
            provider_payment_id=f"fake_pay_{uuid.uuid4().hex[:12]}",
            raw={
                "token_ref": token_ref,
                "amount_cents": amount_cents,
                "currency": currency,
                "idempotency_key": idempotency_key,
                "description": description,
            },
        )
