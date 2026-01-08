from __future__ import annotations
from datetime import datetime
from pydantic import Field
from .common import APIModel
from models.subscription import SubscriptionStatus

class SubscriptionOut(APIModel):
    id: str
    user_id: str
    plan_id: str
    status: SubscriptionStatus
    started_at: datetime | None
    current_period_start: datetime | None
    current_period_end: datetime | None

class SubscriptionCreate(APIModel):
    plan_id: str = Field(min_length=1)
