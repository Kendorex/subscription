from datetime import datetime
from enum import Enum
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from db import Base

class SubscriptionStatus(str, Enum):
    TRIAL = "trial"
    ACTIVE = "active"
    PAST_DUE = "past_due"
    CANCELED = "canceled"
    EXPIRED = "expired"

class Subscription(Base):
    __tablename__="subscriptions"

    id=Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    user_id = Column (
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True
    )

    plan_id= Column(
        UUID(as_uuid=True),
        ForeignKey("plans.id", ondelete="RESTRICT"),
        nullable=False,
        index=True
    )

    status=Column(
        SqlEnum(SubscriptionStatus, name="subscription_status"),
        nullable=False,
        default=SubscriptionStatus.TRIAL,
        index=True,
    )

    started_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    current_period_start = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    current_period_end = Column(
        DateTime(timezone=True),
        nullable=False,
    )

    # Если выставлено — подписка будет отменена в указанное время (обычно конец периода)
    cancel_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Факт отмены (когда пользователь/админ отменил подписку)
    canceled_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

#relationships

    user = relationship(
        "User",
        back_populates="subscriptions",
    )

    plan = relationship(
        "Plan",
        back_populates="subscriptions",
    )

    invoices = relationship(
        "Invoice",
        back_populates="subscription",
        cascade="all, delete-orphan",
    )

#helpers

    def is_active(self) -> bool:
        return self.status in (SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE)
#Нужно ли списывать: период закончился и подписка не отменена
    def is_due(self, now: datetime) -> bool:
        if not self.is_active():
            return False
        if self.cancel_at and now >= self.cancel_at:
            return False
        return now >= self.current_period_end
    
#Отметить отмену
    def mark_canceled(self, when: datetime | None = None) -> None:
        when = when or datetime.utcnow()
        self.canceled_at = when
        if not self.cancel_at:
            self.cancel_at = self.current_period_end

    def __repr__(self) -> str:
        return (
            f"<Subscription id={self.id} user_id={self.user_id} plan_id={self.plan_id} "
            f"status={self.status} period_end={self.current_period_end}>"
        )