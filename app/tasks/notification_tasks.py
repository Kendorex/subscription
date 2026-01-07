# tasks/notification_tasks.py
from __future__ import annotations

from celery import shared_task

from db import SessionLocal
from services.notification_service import NotificationService


@shared_task(name="tasks.notification_tasks.send_due_notifications")
def send_due_notifications(limit: int = 200) -> dict:
    svc = NotificationService()

    db = SessionLocal()
    try:
        results = svc.send_due_batch(db, limit=limit)
        db.commit()

        sent = sum(1 for r in results if r.ok and r.status.name == "SENT")
        retry = sum(1 for r in results if r.status.name == "RETRY")
        failed = sum(1 for r in results if r.status.name == "FAILED")

        return {
            "attempted": len(results),
            "sent": sent,
            "retry_scheduled": retry,
            "failed": failed,
        }
    except Exception as e:
        db.rollback()
        print(f"[celery notifications] ERROR: {e}")
        return {"attempted": 0, "sent": 0, "retry_scheduled": 0, "failed": 0}
    finally:
        db.close()
