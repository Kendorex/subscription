# tasks/billing_tasks.py
from __future__ import annotations

from datetime import datetime, timezone

from celery import shared_task
from sqlalchemy import select

from db import SessionLocal
from models.subscription import Subscription, SubscriptionStatus
from models.notification import NotificationType, NotificationChannel
from services.billing_service import BillingService
from services.notification_service import NotificationService
from payments.fake_gateway import FakePaymentGateway, FakeGatewayConfig


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@shared_task(name="tasks.billing_tasks.run_billing_due")
def run_billing_due(limit: int = 200) -> dict:
    """
    Celery task: находит due подписки и делает одну попытку списания.
    """
    stats = {"processed": 0, "paid": 0, "failed": 0, "retry": 0, "skipped": 0}

    gateway = FakePaymentGateway(
        FakeGatewayConfig(p_success=0.80, p_insufficient=0.10, p_unavailable=0.05, p_declined=0.05)
    )
    billing = BillingService(gateway=gateway)
    notifier = NotificationService()

    # Берём due подписки
    db = SessionLocal()
    try:
        now = now_utc()
        due_ids = (
            db.execute(
                select(Subscription.id)
                .where(
                    Subscription.current_period_end.is_not(None),
                    Subscription.current_period_end <= now,
                    Subscription.status.in_(
                        [SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE]
                    ),
                )
                .order_by(Subscription.current_period_end.asc())
                .limit(limit)
            )
            .scalars()
            .all()
        )
    finally:
        db.close()

    for sub_id in due_ids:
        stats["processed"] += 1
        db = SessionLocal()
        try:
            result = billing.charge_subscription(db, subscription_id=sub_id)

            if result.status.startswith("SKIP"):
                stats["skipped"] += 1
                db.commit()
                continue

            user_id = _get_user_id(db, sub_id)

            if result.ok:
                stats["paid"] += 1
                notifier.enqueue(
                    db,
                    user_id=user_id,
                    type=NotificationType.PAYMENT_OK,
                    channel=NotificationChannel.IN_APP,
                    payload=f'{{"subscription_id":"{sub_id}","invoice_id":{result.invoice_id}}}',
                    dedupe=True,
                )
                db.commit()
                continue

            if result.status in ("RETRY", "DECLINED_RETRY"):
                stats["retry"] += 1
                notifier.enqueue(
                    db,
                    user_id=user_id,
                    type=NotificationType.PAYMENT_FAIL,
                    channel=NotificationChannel.EMAIL,
                    payload=f'{{"subscription_id":"{sub_id}","invoice_id":{result.invoice_id},"reason":"{result.status}"}}',
                    scheduled_at=result.retry_at,
                    dedupe=True,
                )
                db.commit()
                continue

            stats["failed"] += 1
            notifier.enqueue(
                db,
                user_id=user_id,
                type=NotificationType.PAYMENT_FAIL,
                channel=NotificationChannel.EMAIL,
                payload=f'{{"subscription_id":"{sub_id}","invoice_id":{result.invoice_id},"reason":"{result.status}"}}',
                dedupe=True,
            )
            db.commit()

        except Exception as e:
            db.rollback()
            print(f"[celery billing] ERROR sub_id={sub_id}: {e}")
        finally:
            db.close()

    return stats


def _get_user_id(db, subscription_id):
    return db.execute(select(Subscription.user_id).where(Subscription.id == subscription_id)).scalar_one()
