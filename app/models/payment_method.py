from datetime import datetime
import uuid

from sqlalchemy import (
    Column,
    String,
    Boolean,
    DateTime,
    ForeignKey,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship

from db import Base


class PaymentMethod(Base):
    __tablename__ = "payment_methods"

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

    provider = Column(
        String(50),
        nullable=False,
        default="fake",
        index=True,
    )

    token_ref = Column(
        String(255),
        nullable=False,
        unique=True,
        index=True,
    )

    is_default = Column(
        Boolean,
        nullable=False,
        default=True,
    )

    created_at = Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow,
    )

#relationships

    user = relationship(
        "User",
        back_populates="payment_methods",
    )

    def __repr__(self) -> str:
        return (
            f"<PaymentMethod id={self.id} user_id={self.user_id} "
            f"provider={self.provider} default={self.is_default}>"
        )
