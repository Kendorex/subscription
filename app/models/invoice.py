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
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from db import Base


class InvoiceStatus(str, Enum):
    PENDING = "pending"
    PAID = "paid"
    FAILED = "failed"
    VOID = "void"  #подписка отменена, и инвойс больше не нужен


class Invoice(Base):
    __tablename__ = "invoices"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    subscription_id = Column(
        UUID(as_uuid=True),
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
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

    status = Column(
        SqlEnum(InvoiceStatus, name="invoice_status"),
        nullable=False,
        default=InvoiceStatus.PENDING,
        index=True,
    )

    # Для ретраев
    attempt_no = Column(
        Integer,
        nullable=False,
        default=1,
    )

    # Когда инвойс должен быть оплачен (обычно = конец периода)
    due_at = Column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    paid_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Идемпотентность: защита от повторной обработки
    idempotency_key = Column(
        String(128),
        nullable=False,
        unique=True,
        index=True,
    )

# relationships
    subscription = relationship(
        "Subscription",
        back_populates="invoices",
    )

    transactions = relationship(
        "Transaction",
        back_populates="invoice",
    )

#helpers

    def mark_paid(self, when: datetime | None = None) -> None:
        self.status = InvoiceStatus.PAID
        self.paid_at = when or datetime.utcnow()

    def mark_failed(self) -> None:
        self.status = InvoiceStatus.FAILED

    def __repr__(self) -> str:
        return (
            f"<Invoice id={self.id} sub_id={self.subscription_id} "
            f"amount_cents={self.amount_cents} status={self.status} due_at={self.due_at}>"
        )
