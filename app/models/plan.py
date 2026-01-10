from enum import Enum

from sqlalchemy import (
    Column,
    String,
    Integer,
    Boolean,
    DateTime,
    Enum as SqlEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid

from db import Base

class BillingPeriod(str, Enum):
    DAY="day"
    MONTH="month"
    YEAR="year"

class Plan(Base):
    __tablename__="plans"

    id=Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    name=Column(
        String(100),
        nullable=False,
        unique=True   
    )

    price_cents=Column(
        Integer,
        nullable=False
    )

    period=Column(
        SqlEnum(BillingPeriod, name="billing_period"),
        nullable=False
    )

    trial_days=Column(
        Integer,
        nullable=False,
        default=0
    )

    is_active= Column(
        Boolean,
        nullable=False,
        default=True
    )

    subscriptions = relationship(
        "Subscription",
        back_populates="plan"
    )

    def is_trial(self) -> bool:
        return self.trial_days > 0
    
    def __repr__(self) -> str:
        return (
            f"<Plan id={self.id} name={self.name} "
            f"price_cents={self.price_cents} period={self.period}>"
        )