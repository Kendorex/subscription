from datetime import datetime
from enum import Enum
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from db import Base


class NotificationType(str, Enum):
    PAYMENT_OK = "payment_ok"
    PAYMENT_FAIL = "payment_fail"

    SUBSCRIPTION_CREATED = "subscription_created"
    SUBSCRIPTION_RENEWED = "subscription_renewed"
    SUBSCRIPTION_CANCELED = "subscription_canceled"

    TRIAL_ENDING = "trial_ending"
    PAST_DUE = "past_due"


class NotificationChannel(str, Enum):
    IN_APP = "in_app"
    EMAIL = "email"


class NotificationStatus(str, Enum):
    PENDING = "pending"
    SENT = "sent"
    FAILED = "failed"


class Notification(Base):
    __tablename__ = "notifications"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    user_id = Column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    type = Column(
        SqlEnum(NotificationType, name="notification_type"),
        nullable=False,
        index=True,
    )

    channel = Column(
        SqlEnum(NotificationChannel, name="notification_channel"),
        nullable=False,
        default=NotificationChannel.IN_APP,
        index=True,
    )

    status = Column(
        SqlEnum(NotificationStatus, name="notification_status"),
        nullable=False,
        default=NotificationStatus.PENDING,
        index=True,
    )

    payload = Column(
        Text,
        nullable=True,
    )

    scheduled_at = Column(
        DateTime(timezone=True),
        nullable=True,
        index=True,
    )

    sent_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    user = relationship(
        "User",
        back_populates="notifications",
    )

    def mark_sent(self, when: datetime | None = None) -> None:
        self.status = NotificationStatus.SENT
        self.sent_at = when or datetime.utcnow()

    def mark_failed(self) -> None:
        self.status = NotificationStatus.FAILED

    def __repr__(self) -> str:
        return (
            f"<Notification id={self.id} user_id={self.user_id} "
            f"type={self.type} status={self.status} channel={self.channel}>"
        )
