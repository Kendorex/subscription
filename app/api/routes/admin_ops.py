from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.payments.fake_gateway import FakeGatewayConfig, FakePaymentGateway
from app.services.billing_service import BillingService
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
    # 1) Лочим подписку, чтобы параллельно её не трогали
    sub: Subscription | None = (
        db.execute(select(Subscription).where(Subscription.id == sub_id).with_for_update())
        .scalar_one_or_none()
    )
    if not sub:
        raise HTTPException(404, "Subscription not found")

    now = datetime.now(timezone.utc)

    # 2) Форсим "к оплате"
    sub.current_period_end = now
    sub.cancel_at = None
    sub.canceled_at = None

    db.flush()
    db.commit()  # ВАЖНО: чтобы другие сессии (и биллинг) увидели due

    # 3) Списываем именно ЭТУ подписку (точечно)
    gateway = FakePaymentGateway(
        FakeGatewayConfig(p_success=0.80, p_insufficient=0.10, p_unavailable=0.05, p_declined=0.05)
    )
    billing = BillingService(gateway=gateway)

    try:
        result = billing.charge_subscription(db, subscription_id=sub.id)
        db.commit()
    except Exception as e:
        db.rollback()
        raise HTTPException(500, f"Billing failed: {e}")

    # 4) После коммита — рассылаем due-уведомления пачкой
    notif_result = send_due_notifications(limit=50)

    # 5) Вернём актуальную подписку
    db.refresh(sub)

    return {
        "subscription_id": str(sub.id),
        "forced_due_at": now.isoformat(),
        "billing": {
            "ok": result.ok,
            "status": result.status,
            "invoice_id": str(result.invoice_id) if result.invoice_id else None,
            "transaction_id": str(result.transaction_id) if result.transaction_id else None,
            "retry_at": result.retry_at.isoformat() if result.retry_at else None,
            "message": result.message,
        },
        "subscription": {
            "id": str(sub.id),
            "status": str(sub.status),
            "current_period_start": sub.current_period_start.isoformat() if sub.current_period_start else None,
            "current_period_end": sub.current_period_end.isoformat() if sub.current_period_end else None,
            "pay_mode": getattr(sub, "pay_mode", None),
        },
        "notifications": notif_result,
    }