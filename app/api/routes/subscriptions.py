from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import get_db
from models.plan import Plan
from models.subscription import Subscription, SubscriptionStatus
from models.user import User
from schemas.subscription import SubscriptionOut, SubscriptionCreate
from security.auth import get_current_user
from services.notification_service import NotificationService


router = APIRouter(prefix="/subscriptions", tags=["subscriptions"])


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


@router.get("", response_model=list[SubscriptionOut])
def list_my_subscriptions(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return db.execute(select(Subscription).where(Subscription.user_id == user.id)).scalars().all()


@router.get("/me", response_model=list[SubscriptionOut])
def get_my_subscriptions_me(db: Session = Depends(get_db), user=Depends(get_current_user)):
    subs = db.execute(
        select(Subscription)
        .where(Subscription.user_id == user.id)
        .order_by(Subscription.started_at.desc())
    ).scalars().all()

    return subs



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
            Subscription.status.in_([SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE]),
        )
    ).scalar_one_or_none()

    if active:
        raise HTTPException(400, "Active subscription already exists")

    start = now_utc()

    db_user: User = db.execute(
        select(User).where(User.id == user.id).with_for_update()
    ).scalar_one()

    eligible_trial = bool(plan.trial_days and plan.trial_days > 0 and db_user.trial_used_at is None)

    if eligible_trial:
        status = SubscriptionStatus.TRIAL
        period_end = start + timedelta(days=int(plan.trial_days))
        db_user.trial_used_at = start
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

    notifier = NotificationService()
    notifier.enqueue_subscription_created(
        db,
        user_id=user.id,
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

    now = now_utc()
    if sub.status == SubscriptionStatus.EXPIRED:
        return sub

    sub.status = SubscriptionStatus.CANCELED
    sub.canceled_at = sub.canceled_at or now
    sub.cancel_at = sub.current_period_end or now

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
