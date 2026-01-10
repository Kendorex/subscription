from datetime import datetime, timedelta, timezone
from sqlalchemy import select

from db import SessionLocal
from models.subscription import Subscription, SubscriptionStatus
from models.plan import Plan

from tasks.billing_tasks import run_billing_due
from tasks.notification_tasks import send_due_notifications

def make_one_subscription_due():
    db = SessionLocal()
    try:
        sub = db.execute(
            select(Subscription)
            .where(Subscription.status.in_([SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE, SubscriptionStatus.TRIAL]))
            .order_by(Subscription.id.asc())
        ).scalars().first()

        if not sub:
            print("Нет подписок для демо. Сначала запусти seed_db.py")
            return None

        sub.current_period_end = datetime.now(timezone.utc) - timedelta(seconds=5)
        db.commit()
        print(f"Сделал subscription due: {sub.id} (status={sub.status})")
        return sub.id
    finally:
        db.close()

if __name__ == "__main__":
    sub_id = make_one_subscription_due()

    print("\n--- RUN BILLING ONCE ---")
    print(run_billing_due(50))

    print("\n--- RUN NOTIFICATIONS ONCE ---")
    print(send_due_notifications(50))
