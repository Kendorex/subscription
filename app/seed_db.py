# scripts/seed_db.py
from __future__ import annotations

import os
from datetime import datetime, timedelta
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

# важно: импортни модели, чтобы они зарегистрировались в metadata
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

def now_utc() -> datetime:
    # у тебя в моделях default=datetime.utcnow (naive), но колонки timezone=True.
    # Для учебного проекта ок, но лучше позже перейти на aware.
    return datetime.utcnow()


def get_or_create_user(db: Session, email: str, role: UserRole) -> User:
    u = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if u:
        return u
    u = User(
        email=email,
        password_hash="demo_hash",  # не храни так в проде :)
        role=role,
    )
    db.add(u)
    db.flush()
    return u


def seed():
    init_db()

    db = SessionLocal()
    try:
        # --- Users
        admin = get_or_create_user(db, "admin@example.com", UserRole.ADMIN)
        alice = get_or_create_user(db, "alice@example.com", UserRole.USER)
        bob = get_or_create_user(db, "bob@example.com", UserRole.USER)

        # --- Plans
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

        # --- Payment methods
        def ensure_pm(user: User, token: str, is_default: bool = True):
            pm = db.execute(
                select(PaymentMethod).where(PaymentMethod.token_ref == token)
            ).scalar_one_or_none()
            if pm:
                return pm
            pm = PaymentMethod(
                user_id=user.id,
                provider="fake",
                token_ref=token,
                is_default=is_default,
            )
            db.add(pm)
            db.flush()
            return pm

        pm_alice = ensure_pm(alice, "tok_alice_001", True)
        pm_bob = ensure_pm(bob, "tok_bob_001", True)

        # --- Subscriptions
        # Alice: trial -> active, период уже почти закончился (для демонстрации автосписания)
        start = now_utc() - timedelta(days=20)
        period_start = now_utc() - timedelta(days=5)
        period_end = now_utc() + timedelta(days=1)

        sub_alice = Subscription(
            user_id=alice.id,
            plan_id=pro.id,
            status=SubscriptionStatus.ACTIVE,
            started_at=start,
            current_period_start=period_start,
            current_period_end=period_end,
        )
        db.add(sub_alice)
        db.flush()

        # Bob: past_due (неуспешное списание)
        sub_bob = Subscription(
            user_id=bob.id,
            plan_id=basic.id,
            status=SubscriptionStatus.PAST_DUE,
            started_at=now_utc() - timedelta(days=40),
            current_period_start=now_utc() - timedelta(days=30),
            current_period_end=now_utc() - timedelta(days=1),
        )
        db.add(sub_bob)
        db.flush()

        # --- Invoices
        inv_alice = Invoice(
            subscription_id=sub_alice.id,
            amount_cents=pro.price_cents,
            currency="RUB",
            status=InvoiceStatus.PAID,
            attempt_no=1,
            due_at=sub_alice.current_period_end,
            paid_at=now_utc() - timedelta(hours=1),
            idempotency_key="inv_alice_001",
        )
        db.add(inv_alice)
        db.flush()

        inv_bob = Invoice(
            subscription_id=sub_bob.id,
            amount_cents=basic.price_cents,
            currency="RUB",
            status=InvoiceStatus.FAILED,
            attempt_no=2,
            due_at=sub_bob.current_period_end,
            idempotency_key="inv_bob_001",
        )
        db.add(inv_bob)
        db.flush()

        # --- Transactions
        tx_alice = Transaction(
            user_id=alice.id,
            invoice_id=inv_alice.id,
            type=TransactionType.CHARGE,
            status=TransactionStatus.SUCCEEDED,
            amount_cents=inv_alice.amount_cents,
            currency="RUB",
            provider="fake",
            provider_payment_id="pay_fake_alice_001",
            idempotency_key="tx_alice_001",
        )
        db.add(tx_alice)
        db.flush()

        tx_bob = Transaction(
            user_id=bob.id,
            invoice_id=inv_bob.id,
            type=TransactionType.CHARGE,
            status=TransactionStatus.FAILED,
            amount_cents=inv_bob.amount_cents,
            currency="RUB",
            provider="fake",
            provider_payment_id="pay_fake_bob_001",
            idempotency_key="tx_bob_001",
        )
        db.add(tx_bob)
        db.flush()

        # --- Refund (пример возврата Alice)
        rf = Refund(
            transaction_id=tx_alice.id,
            amount_cents=5000,
            status=RefundStatus.SUCCEEDED,
            reason="Goodwill refund",
            provider_refund_id="refund_fake_001",
            processed_at=now_utc(),
        )
        db.add(rf)
        db.flush()

        # --- Notifications
        n1 = Notification(
            user_id=alice.id,
            type=NotificationType.PAYMENT_OK,
            channel=NotificationChannel.IN_APP,
            status=NotificationStatus.SENT,
            payload='{"invoice":"inv_alice_001","message":"Payment succeeded"}',
            scheduled_at=None,
            sent_at=now_utc(),
        )
        n2 = Notification(
            user_id=bob.id,
            type=NotificationType.PAYMENT_FAIL,
            channel=NotificationChannel.EMAIL,
            status=NotificationStatus.PENDING,
            payload='{"invoice":"inv_bob_001","message":"Payment failed: insufficient funds"}',
            scheduled_at=now_utc() + timedelta(minutes=5),
        )
        db.add_all([n1, n2])

        db.commit()
        print("✅ Seed completed.")
        print(f"Users: admin={admin.email}, alice={alice.email}, bob={bob.email}")
        print(f"Plans: {basic.name}, {pro.name}, {yearly.name}")
        print(f"Subscriptions: alice={sub_alice.status}, bob={sub_bob.status}")

    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    # Удобно: можно задать DATABASE_URL прямо перед запуском
    # set DATABASE_URL=... (Windows) / export DATABASE_URL=... (Linux/Mac)
    seed()
