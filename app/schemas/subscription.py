from __future__ import annotations
from datetime import datetime
from pydantic import Field
from uuid import UUID
from .common import APIModel
from models.subscription import SubscriptionStatus

class SubscriptionOut(APIModel):
    id: UUID
    user_id: UUID
    plan_id: UUID
    status: SubscriptionStatus
    started_at: datetime | None
    current_period_start: datetime | None
    current_period_end: datetime | None

class SubscriptionCreate(APIModel):
    plan_id: UUID
