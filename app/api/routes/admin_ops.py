from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db import get_db
from security.auth import require_admin
from tasks.billing_tasks import run_billing_due
from tasks.notification_tasks import send_due_notifications
from datetime import datetime, timezone
from fastapi import HTTPException
from sqlalchemy import select
from models.subscription import Subscription
from tasks.billing_tasks import run_billing_due
from tasks.notification_tasks import send_due_notifications

router = APIRouter(prefix="/admin", tags=["admin:ops"], dependencies=[Depends(require_admin)])

@router.post("/billing/run-due")
def admin_run_billing(limit: int = 200):
    return run_billing_due(limit)

@router.post("/notifications/send-due")
def admin_send_notifications(limit: int = 200):
    return send_due_notifications(limit)

@router.post("/subscriptions/{sub_id}/simulate-renewal")
def admin_simulate_renewal(sub_id: str, db: Session = Depends(get_db)):
    sub = db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "Subscription not found")

    now = datetime.now(timezone.utc)
    sub.current_period_end = now
    sub.cancel_at = None
    sub.canceled_at = None

    db.flush()

    billing_result = run_billing_due(limit=1)

    notif_result = send_due_notifications(limit=10)

    return {
        "subscription_id": str(sub.id),
        "forced_due_at": now,
        "billing": billing_result,
        "notifications": notif_result,
    }