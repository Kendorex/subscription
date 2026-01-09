from __future__ import annotations
from dotenv import load_dotenv
load_dotenv()
import os
from celery import Celery

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", "redis://localhost:6379/1")

celery_app = Celery(
    "subscription_app",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=[
        "tasks.billing_tasks",
        "tasks.notification_tasks",
    ],
)

celery_app.conf.update(
    timezone="UTC",
    enable_utc=True,
    task_track_started=True,
    worker_prefetch_multiplier=1,
)

celery_app.conf.beat_schedule = {
    "billing-every-30-sec": {
        "task": "tasks.billing_tasks.run_billing_due",
        "schedule": 30.0,
        "args": (200,),
    },
    "notifications-every-60-sec": {
        "task": "tasks.notification_tasks.send_due_notifications",
        "schedule": 60.0,
        "args": (200,),
    },
}
