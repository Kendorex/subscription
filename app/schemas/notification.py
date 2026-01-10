from __future__ import annotations
from datetime import datetime

from uuid import UUID
from .common import APIModel
from models.notification import NotificationType, NotificationChannel, NotificationStatus

class NotificationOut(APIModel):
    id: UUID
    user_id: UUID
    type: NotificationType
    channel: NotificationChannel
    status: NotificationStatus
    payload: str | None
    scheduled_at: datetime | None
    sent_at: datetime | None
