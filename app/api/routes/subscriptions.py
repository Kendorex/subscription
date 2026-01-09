from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.services.notification_service import NotificationService
from db import get_db
from models.plan import Plan
from models.subscription import Subscription, SubscriptionStatus
from schemas.subscription import SubscriptionOut, SubscriptionCreate
from security.auth import get_current_user

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])

def now_utc() -> datetime:
    return datetime.now(timezone.utc)

@router.get("/me", response_model=list[SubscriptionOut])
def my_subscriptions(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return db.execute(
        select(Subscription).where(Subscription.user_id == user.id)
    ).scalars().all()

@router.post("", response_model=SubscriptionOut)
def create_subscription(
    payload: SubscriptionCreate,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    plan = db.get(Plan, payload.plan_id)
    if not plan or not plan.is_active:
        raise HTTPException(400, "Plan is inactive or not found")

    active = db.execute(
        select(Subscription).where(
            Subscription.user_id == user.id,
            Subscription.status.in_(
                [SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE]
            ),
        )
    ).scalar_one_or_none()
    if active:
        raise HTTPException(409, "User already has an active subscription")

    start = now_utc()

    if plan.trial_days and plan.trial_days > 0:
        status = SubscriptionStatus.TRIAL
        period_end = start + timedelta(days=int(plan.trial_days))
    else:
        status = SubscriptionStatus.ACTIVE
        period_end = start

    sub = Subscription(
        user_id=user.id,
        plan_id=plan.id,
        status=status,
        started_at=start,
        current_period_start=start,
        current_period_end=period_end,
        pay_mode=payload.pay_mode,
    )

    db.add(sub)
    db.flush()

    NotificationService().enqueue_subscription_created(
        db,
        user_id=sub.user_id,
        plan_name=plan.name,
        when=start,
    )

    db.commit()
    db.refresh(sub)
    return sub


@router.post("/{sub_id}/cancel", response_model=SubscriptionOut)
def cancel_subscription(
    sub_id: str,
    db: Session = Depends(get_db),
    user=Depends(get_current_user),
):
    sub = db.get(Subscription, sub_id)
    if not sub or sub.user_id != user.id:
        raise HTTPException(404, "Subscription not found")

    if sub.status == SubscriptionStatus.CANCELED:
        return sub

    now = now_utc()

    sub.status = SubscriptionStatus.CANCELED
    sub.canceled_at = now
    sub.cancel_at = now
    sub.current_period_end = min(sub.current_period_end, now) if sub.current_period_end else now

    plan = db.get(Plan, sub.plan_id)
    plan_name = plan.name if plan else "Unknown"

    NotificationService().enqueue_subscription_canceled(
        db,
        user_id=sub.user_id,
        plan_name=plan_name,
        when=now,
    )

    db.commit()
    db.refresh(sub)
    return sub
