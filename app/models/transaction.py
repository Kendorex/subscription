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


class TransactionType(str, Enum):
    CHARGE = "charge"
    REFUND = "refund"


class TransactionStatus(str, Enum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Transaction(Base):
    __tablename__ = "transactions"

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

    invoice_id = Column(
        UUID(as_uuid=True),
        ForeignKey("invoices.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    type = Column(
        SqlEnum(TransactionType, name="transaction_type"),
        nullable=False,
        index=True,
    )

    status = Column(
        SqlEnum(TransactionStatus, name="transaction_status"),
        nullable=False,
        default=TransactionStatus.PENDING,
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

    provider = Column(
        String(50),
        nullable=False,
        default="fake",  # например: fake / yoomoney
        index=True,
    )

    provider_payment_id = Column(
        String(128),
        nullable=True,
        index=True,
    )

    # Ключ идемпотентности на уровне транзакции
    idempotency_key = Column(
        String(128),
        nullable=True,
        index=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

#relationships

    user = relationship(
        "User",
        back_populates="transactions",
    )

    invoice = relationship(
        "Invoice",
        back_populates="transactions",
    )

    refund = relationship(
        "Refund",
        back_populates="transaction",
        uselist=False,
        cascade="all, delete-orphan",
    )

#helpers

    def mark_succeeded(self, provider_payment_id: str | None = None) -> None:
        self.status = TransactionStatus.SUCCEEDED
        if provider_payment_id:
            self.provider_payment_id = provider_payment_id

    def mark_failed(self, provider_payment_id: str | None = None) -> None:
        self.status = TransactionStatus.FAILED
        if provider_payment_id:
            self.provider_payment_id = provider_payment_id

    def __repr__(self) -> str:
        return (
            f"<Transaction id={self.id} user_id={self.user_id} type={self.type} "
            f"status={self.status} amount_cents={self.amount_cents}>"
        )
