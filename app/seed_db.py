from __future__ import annotations

import os
from datetime import datetime, timedelta
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from db import SessionLocal, init_db
from models.user import User, UserRole
from models.plan import Plan, BillingPeriod
from models.payment_method import PaymentMethod
from models.subscription import Subscription, SubscriptionStatus
from models.invoice import Invoice, InvoiceStatus
from models.transaction import Transaction, TransactionType, TransactionStatus
from models.refund import Refund, RefundStatus
from models.notification import (
    Notification,
    NotificationType,
    NotificationChannel,
    NotificationStatus,
)
from models.balance_entry import BalanceEntry, BalanceEntryType

def now_utc() -> datetime:
    return datetime.utcnow()

def get_or_create_user(db: Session, email: str, role: UserRole) -> User:
    u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if u:
        return u
    u = User(
        email=email,
        password_hash="demo_hash",
        role=role,
        balance_cents=100000,
    )
    db.add(u)
    db.flush()
    return u

def seed():
    init_db()

    db = SessionLocal()
    try:
        admin = get_or_create_user(db, "admin@example.com", UserRole.ADMIN)
        alice = get_or_create_user(db, "alice@example.com", UserRole.USER)
        bob = get_or_create_user(db, "bob@example.com", UserRole.USER)

        def get_or_create_plan(name: str, price_cents: int, period: BillingPeriod, trial_days: int):
            p = db.execute(select(Plan).where(Plan.name == name)).scalar_one_or_none()
            if p:
                return p
            p = Plan(
                name=name,
                price_cents=price_cents,
                period=period,
                trial_days=trial_days,
                is_active=True,
            )
            db.add(p)
            db.flush()
            return p

        basic = get_or_create_plan("Basic", 29900, BillingPeriod.MONTH, trial_days=7)
        pro = get_or_create_plan("Pro", 79900, BillingPeriod.MONTH, trial_days=14)
        yearly = get_or_create_plan("Yearly", 499900, BillingPeriod.YEAR, trial_days=0)

        db.commit()
        print("✅ Seed completed successfully!")

    except Exception as e:
        db.rollback()
        print(f"❌ Seed failed: {e}")
        import traceback
        traceback.print_exc()
        raise
    finally:
        db.close()

if __name__ == "__main__":
    seed()