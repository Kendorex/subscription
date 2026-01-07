from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Column,
    String,
    DateTime,
    Enum as SqlEnum,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
import uuid

from db import Base

class UserRole(str, Enum):
    USER="user"
    ADMIN="admin"

class User(Base):
    __tablename__ = "users"

    id=Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4
    )

    email = Column(
        String(255),
        unique=True,
        nullable=False,
        index=True
    )

    password_hash= Column(
        String(255),
        nullable=False,
    )

    role = Column(
        SqlEnum(UserRole, name="user_role"),
        nullable=False,
        default=UserRole.USER,
)

    created_at=Column(
        DateTime(timezone=True),
        nullable=False,
        default=datetime.utcnow
    )

#Relationships
    subscriptions = relationship(
        "Subscription",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    payment_methods = relationship(
        "PaymentMethod",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    transactions = relationship(
        "Transaction",
        back_populates="user"
    )

    notifications = relationship(
        "Notification",
        back_populates="user",
        cascade="all, delete-orphan"
    )

#helpers
    def is_admin(self) -> bool:
        return self.role == UserRole.ADMIN
    
    def __repr__(self) -> str:
        return f"<User id={self.id} email={self.email} role={self.role}>"