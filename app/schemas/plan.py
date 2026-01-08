# app/schemas/plan.py
from __future__ import annotations

from uuid import UUID
from pydantic import Field

from .common import APIModel
from models.plan import BillingPeriod


class PlanOut(APIModel):
    id: UUID
    name: str
    price_cents: int
    period: BillingPeriod
    trial_days: int
    is_active: bool


class PlanCreate(APIModel):
    name: str = Field(min_length=2, max_length=100)
    price_cents: int = Field(ge=0)
    period: BillingPeriod
    trial_days: int = Field(ge=0, le=365)


class PlanUpdate(APIModel):
    name: str | None = Field(default=None, min_length=2, max_length=100)
    price_cents: int | None = Field(default=None, ge=0)
    trial_days: int | None = Field(default=None, ge=0, le=365)
    is_active: bool | None = None
