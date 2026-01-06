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

from app.db import Base


class RefundStatus(str, Enum):
    REQUESTED = "requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class Refund(Base):
    __tablename__ = "refunds"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    # Возврат относится к транзакции списания/операции
    transaction_id = Column(
        UUID(as_uuid=True),
        ForeignKey("transactions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True, 
        index=True,
    )

    amount_cents = Column(
        Integer,
        nullable=False,
    )

    status = Column(
        SqlEnum(RefundStatus, name="refund_status"),
        nullable=False,
        default=RefundStatus.REQUESTED,
        index=True,
    )

    reason = Column(
        String(255),
        nullable=True,
    )

    provider_refund_id = Column(
        String(128),
        nullable=True,
        index=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

    processed_at = Column(
        DateTime(timezone=True),
        nullable=True,
    )

#relationships

    transaction = relationship(
        "Transaction",
        back_populates="refund",
    )

#helpers

    def mark_succeeded(self, provider_refund_id: str | None = None) -> None:
        self.status = RefundStatus.SUCCEEDED
        self.processed_at = datetime.utcnow()
        if provider_refund_id:
            self.provider_refund_id = provider_refund_id

    def mark_failed(self, provider_refund_id: str | None = None) -> None:
        self.status = RefundStatus.FAILED
        self.processed_at = datetime.utcnow()
        if provider_refund_id:
            self.provider_refund_id = provider_refund_id

    def __repr__(self) -> str:
        return (
            f"<Refund id={self.id} tx_id={self.transaction_id} "
            f"amount_cents={self.amount_cents} status={self.status}>"
        )
