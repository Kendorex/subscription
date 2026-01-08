from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from db import get_db
from security.auth import require_admin
from tasks.billing_tasks import run_billing_due
from tasks.notification_tasks import send_due_notifications

router = APIRouter(prefix="/admin", tags=["admin:ops"], dependencies=[Depends(require_admin)])

@router.post("/billing/run-due")
def admin_run_billing(limit: int = 200):
    # Важно: это вызов синхронно (без celery). Для демо — ок.
    return run_billing_due(limit)

@router.post("/notifications/send-due")
def admin_send_notifications(limit: int = 200):
    return send_due_notifications(limit)
