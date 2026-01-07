# payments/gateway.py
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Optional


class PaymentError(Exception):
    """Базовая ошибка платежей."""


class PaymentTemporaryUnavailable(PaymentError):
    """Платёжная система недоступна (ретраить можно)."""


class PaymentInsufficientFunds(PaymentError):
    """Недостаточно средств (ретраи обычно бессмысленны до смены способа оплаты)."""


class PaymentDeclined(PaymentError):
    """Платёж отклонён по иной причине (можно ретраить ограниченно)."""


@dataclass(frozen=True)
class ChargeResult:
    provider_payment_id: str
    raw: dict


class PaymentGateway(Protocol):
    def charge(
        self,
        *,
        token_ref: str,
        amount_cents: int,
        currency: str,
        idempotency_key: str,
        description: str,
    ) -> ChargeResult:
        ...
