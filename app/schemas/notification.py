from __future__ import annotations
from datetime import datetime
from .common import APIModel
from models.notification import NotificationType, NotificationChannel, NotificationStatus

class NotificationOut(APIModel):
    id: str
    user_id: str
    type: NotificationType
    channel: NotificationChannel
    status: NotificationStatus
    payload: str | None
    scheduled_at: datetime | None
    sent_at: datetime | None
