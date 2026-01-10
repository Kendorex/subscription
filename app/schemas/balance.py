from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field

class BalanceOut(BaseModel):
    balance_cents: int = Field(ge=0)
    currency: str = "RUB"

class BalanceTopUpIn(BaseModel):
    amount_cents: int = Field(..., gt=0)
    idempotency_key: str = Field(..., min_length=3, max_length=128)

class BalanceEntryOut(BaseModel):
    id: UUID
    amount_cents: int
    currency: str
    entry_type: str
    created_at: datetime
    idempotency_key: str
    related_invoice_id: UUID | None = None
    related_subscription_id: UUID | None = None
