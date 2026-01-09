# services/subscription_service.py
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.utils.json_utils import json_dumps
from models.plan import Plan, BillingPeriod
from models.subscription import Subscription, SubscriptionStatus

from models.notification import NotificationType, NotificationChannel
from services.notification_service import NotificationService
from services.billing_service import BillingService
from payments.fake_gateway import FakePaymentGateway, FakeGatewayConfig


class SubscriptionError(Exception):
    pass


class ActiveSubscriptionExists(SubscriptionError):
    pass


class PlanNotAvailable(SubscriptionError):
    pass


class SubscriptionNotFound(SubscriptionError):
    pass


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def add_period(dt: datetime, period: BillingPeriod) -> datetime:
    # Упрощение для зачёта: месяц=30 дней, год=365 дней
    if period == BillingPeriod.MONTH:
        return dt + timedelta(days=30)
    if period == BillingPeriod.YEAR:
        return dt + timedelta(days=365)
    return dt + timedelta(days=30)


def add_trial(dt: datetime, trial_days: int) -> datetime:
    return dt + timedelta(days=max(int(trial_days or 0), 0))


@dataclass(frozen=True)
class CreateSubscriptionResult:
    subscription_id: str
    status: SubscriptionStatus
    current_period_start: datetime
    current_period_end: datetime
    cancel_at: datetime | None


class SubscriptionService:
    """
    Жизненный цикл подписки:
    - create
    - cancel (end of period / immediately)
    - change plan (next period / immediately)
    - time-based transitions (только отмена по cancel_at; никаких "trial->active" без оплаты)
    """

    # ---------- Create ----------

    def create_subscription(
        self,
        db: Session,
        *,
        user_id,
        plan_id,
        start_at: datetime | None = None,
    ) -> CreateSubscriptionResult:
        start_at = start_at or utcnow()
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=timezone.utc)

        plan: Plan | None = db.get(Plan, plan_id)
        if not plan or not plan.is_active:
            raise PlanNotAvailable("Plan not found or inactive")

        # блокируем активную подписку пользователя от гонок
        existing_active = db.execute(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status.in_([SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE]),
            )
            .with_for_update()
        ).scalar_one_or_none()

        if existing_active:
            raise ActiveSubscriptionExists("User already has an active subscription")

        # trial или активная сразу
        if plan.trial_days and plan.trial_days > 0:
            status = SubscriptionStatus.TRIAL
            period_start = start_at
            period_end = add_trial(start_at, plan.trial_days)
        else:
            status = SubscriptionStatus.ACTIVE
            period_start = start_at
            period_end = add_period(start_at, plan.period)

        sub = Subscription(
            user_id=user_id,
            plan_id=plan_id,
            status=status,
            started_at=start_at,
            current_period_start=period_start,
            current_period_end=period_end,
            cancel_at=None,
            canceled_at=None,
        )

        db.add(sub)
        db.flush()
        # --- immediate charge: если нет trial, пытаемся списать сразу ---
        immediate_payment_attempted = False
        if status == SubscriptionStatus.ACTIVE and (not plan.trial_days or plan.trial_days <= 0):
            immediate_payment_attempted = True

            # делаем подписку 'due now', чтобы BillingService выполнил попытку списания немедленно
            sub.current_period_end = start_at

            gateway = FakePaymentGateway(
                FakeGatewayConfig(p_success=0.80, p_insufficient=0.10, p_unavailable=0.05, p_declined=0.05)
            )
            billing = BillingService(gateway=gateway)
            billing_result = billing.charge_subscription(db, subscription_id=sub.id)

            # если оплата не прошла — шлём уведомление (и оставляем ретраи на billing_tasks)
            if not billing_result.ok:
                NotificationService().enqueue_payment_failed(
                    db,
                    user_id=user_id,
                    plan_name=plan.name,
                    reason=billing_result.status,
                    retry_at=billing_result.retry_at,
                    when=start_at,
                    scheduled_at=None,
                    dedupe=False,
                )


        # ✅ СРАЗУ после оформления — уведомление "подписка оформлена"
        NotificationService().enqueue_subscription_created(
            db,
            user_id=user_id,
            plan_name=plan.name,
            when=start_at,
        )
        return CreateSubscriptionResult(
            subscription_id=str(sub.id),
            status=sub.status,
            current_period_start=sub.current_period_start,
            current_period_end=sub.current_period_end,
            cancel_at=sub.cancel_at,
        )

    # ---------- Cancel ----------

    def cancel_at_period_end(self, db: Session, *, subscription_id, when: datetime | None = None) -> Subscription:
        when = when or utcnow()
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)

        sub: Subscription | None = db.execute(
            select(Subscription).where(Subscription.id == subscription_id).with_for_update()
        ).scalar_one_or_none()
        if not sub:
            raise SubscriptionNotFound("Subscription not found")

        if sub.status in (SubscriptionStatus.CANCELED, SubscriptionStatus.EXPIRED):
            return sub  # идемпотентно

        # выставляем cancel_at = конец текущего периода
        sub.mark_canceled(when=when)

        # ✅ уведомление "подписка отменена" (факт отмены пользователем, даже если действует до конца периода)
        NotificationService().enqueue_subscription_canceled(
            db,
            user_id=sub.user_id,
            plan_name=None,
            when=when,
        )
        return sub

    def cancel_immediately(self, db: Session, *, subscription_id, when: datetime | None = None) -> Subscription:
        when = when or utcnow()
        if when.tzinfo is None:
            when = when.replace(tzinfo=timezone.utc)

        sub: Subscription | None = db.execute(
            select(Subscription).where(Subscription.id == subscription_id).with_for_update()
        ).scalar_one_or_none()
        if not sub:
            raise SubscriptionNotFound("Subscription not found")

        if sub.status in (SubscriptionStatus.CANCELED, SubscriptionStatus.EXPIRED):
            return sub

        sub.canceled_at = when
        sub.cancel_at = when
        sub.current_period_end = when
        sub.status = SubscriptionStatus.CANCELED

        NotificationService().enqueue_subscription_canceled(
            db,
            user_id=sub.user_id,
            plan_name=None,
            when=when,
        )
        return sub

    # ---------- Change plan ----------

    def change_plan_next_period(self, db: Session, *, subscription_id, new_plan_id) -> Subscription:
        sub: Subscription | None = db.execute(
            select(Subscription).where(Subscription.id == subscription_id).with_for_update()
        ).scalar_one_or_none()
        if not sub:
            raise SubscriptionNotFound("Subscription not found")

        new_plan: Plan | None = db.get(Plan, new_plan_id)
        if not new_plan or not new_plan.is_active:
            raise PlanNotAvailable("New plan not found or inactive")

        now = utcnow()

        if now >= sub.current_period_end:
            sub.plan_id = new_plan_id
            sub.status = SubscriptionStatus.ACTIVE
            sub.current_period_start = now
            sub.current_period_end = add_period(now, new_plan.period)
            sub.cancel_at = None
            sub.canceled_at = None
            return sub

        sub.mark_canceled(when=now)
        return sub

    def change_plan_immediately(self, db: Session, *, subscription_id, new_plan_id) -> Subscription:
        sub: Subscription | None = db.execute(
            select(Subscription).where(Subscription.id == subscription_id).with_for_update()
        ).scalar_one_or_none()
        if not sub:
            raise SubscriptionNotFound("Subscription not found")

        new_plan: Plan | None = db.get(Plan, new_plan_id)
        if not new_plan or not new_plan.is_active:
            raise PlanNotAvailable("New plan not found or inactive")

        now = utcnow()

        sub.plan_id = new_plan_id
        sub.status = SubscriptionStatus.ACTIVE
        sub.current_period_start = now
        sub.current_period_end = add_period(now, new_plan.period)
        sub.cancel_at = None
        sub.canceled_at = None
        return sub

    # ---------- Time-based transitions ----------

    def apply_time_based_transitions(self, db: Session, *, subscription_id, now: datetime | None = None) -> Subscription:
        """
        ВАЖНО: здесь НЕ делаем trial->active и НЕ продлеваем период.
        Этим занимается BillingService строго в момент current_period_end.
        Здесь только:
        - отмена по cancel_at
        """
        now = now or utcnow()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        sub: Subscription | None = db.execute(
            select(Subscription).where(Subscription.id == subscription_id).with_for_update()
        ).scalar_one_or_none()
        if not sub:
            raise SubscriptionNotFound("Subscription not found")

        # отмена по расписанию (в конце периода)
        if sub.cancel_at and now >= sub.cancel_at and sub.status in (SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE):
            sub.status = SubscriptionStatus.CANCELED
            if not sub.canceled_at:
                sub.canceled_at = now
            sub.current_period_end = min(sub.current_period_end, now)
            return sub

        return sub