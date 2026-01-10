from datetime import datetime
from enum import Enum
import uuid

from sqlalchemy import (
    Column,
    DateTime,
    Enum as SqlEnum,
    ForeignKey,
    Integer,
    String,
    Index,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from db import Base


class BalanceEntryType(str, Enum):
    TOPUP = "topup"
    SUBSCRIPTION_CHARGE = "subscription_charge"
    REFUND = "refund"
    ADJUSTMENT = "adjustment"


class BalanceEntry(Base):
    __tablename__ = "balance_entries"

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

    amount_cents = Column(
        Integer,
        nullable=False,
    )

    currency = Column(
        String(3),
        nullable=False,
        default="RUB",
    )

    entry_type = Column(
        SqlEnum(BalanceEntryType, name="balance_entry_type"),
        nullable=False,
        index=True,
    )

    related_invoice_id = Column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    related_subscription_id = Column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    idempotency_key = Column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
        index=True,
    )

    user = relationship("User", back_populates="balance_entries")
    invoice = relationship("Invoice")
    subscription = relationship("Subscription")

    __table_args__ = (
        Index("ix_balance_entries_user_created", "user_id", "created_at"),
    )

    def __repr__(self) -> str:
        return (
            f"<BalanceEntry id={self.id} user_id={self.user_id} "
            f"amount_cents={self.amount_cents} type={self.entry_type}>"
        )
