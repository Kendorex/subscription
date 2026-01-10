from __future__ import annotations

import traceback
from datetime import datetime, timezone
from uuid import UUID

from celery import shared_task
from sqlalchemy import select

from db import SessionLocal
from models.plan import Plan
from models.subscription import Subscription, SubscriptionStatus
from models.notification import NotificationType
from services.billing_service import BillingService
from services.notification_service import NotificationService
from payments.fake_gateway import FakePaymentGateway, FakeGatewayConfig

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

@shared_task(name="tasks.billing_tasks.run_billing_due")
def run_billing_due(limit: int = 200) -> dict:
    stats = {"processed": 0, "paid": 0, "failed": 0, "retry": 0, "skipped": 0}

    gateway = FakePaymentGateway(
        FakeGatewayConfig(p_success=0.80, p_insufficient=0.10, p_unavailable=0.05, p_declined=0.05)
    )
    billing = BillingService(gateway=gateway)
    notifier = NotificationService()

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
            user_id = _get_user_id(db, sub_id)
            plan_name = _get_plan_name(db, sub_id)

            result = billing.charge_subscription(db, subscription_id=sub_id)

            if result.status.startswith("SKIP"):
                stats["skipped"] += 1

                if result.status == "SKIP_CANCELED_AT_PERIOD_END":
                    notifier.enqueue_subscription_canceled(
                        db,
                        user_id=user_id,
                        plan_name=plan_name,
                        when=now_utc(),
                    )
                db.commit()
                continue

            if result.ok:
                stats["paid"] += 1

                invoice_id_str = str(result.invoice_id) if result.invoice_id else None
                sub_id_str = str(sub_id)

                notifier.enqueue_payment_ok(
                    db,
                    user_id=user_id,
                    plan_name=plan_name,
                    subscription_id=sub_id_str,
                    invoice_id=invoice_id_str,
                    when=now_utc(),
                )

                notifier.enqueue_subscription_renewed(
                    db,
                    user_id=user_id,
                    plan_name=plan_name,
                    when=now_utc(),
                )

                db.commit()

            elif result.status in ("RETRY", "DECLINED_RETRY"):
                stats["retry"] += 1
                notifier.enqueue_payment_failed(
                    db,
                    user_id=user_id,
                    plan_name=plan_name,
                    reason=result.status,
                    retry_at=result.retry_at,
                    when=now_utc(),
                    scheduled_at=None,
                    dedupe=False,
                )
                db.commit()

            else:
                stats["failed"] += 1
                notifier.enqueue_payment_failed(
                    db,
                    user_id=user_id,
                    plan_name=plan_name,
                    reason=result.status,
                    retry_at=result.retry_at,
                    when=now_utc(),
                    scheduled_at=None,
                    dedupe=False,
                )
                db.commit()

        except Exception as e:
            db.rollback()
            print(f"[celery billing] ERROR sub_id={sub_id}: {e}")
            traceback.print_exc()
        finally:
            db.close()

    return stats


def _get_user_id(db, subscription_id):
    result = db.execute(select(Subscription.user_id).where(Subscription.id == subscription_id)).scalar_one()
    return str(result) if isinstance(result, UUID) else result


def _get_plan_name(db, subscription_id) -> str:
    plan_id = db.execute(select(Subscription.plan_id).where(Subscription.id == subscription_id)).scalar_one()
    plan = db.get(Plan, plan_id)
    return plan.name if plan else "Unknown"
