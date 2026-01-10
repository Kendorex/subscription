# subscription_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.plan import Plan, BillingPeriod
from models.subscription import Subscription, SubscriptionStatus
from models.user import User
from services.notification_service import NotificationService


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
    if period == BillingPeriod.DAY:
        return dt + timedelta(days=1)
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
    def __init__(self) -> None:
        self.notifier = NotificationService()

    def create_subscription(
        self,
        db: Session,
        *,
        user_id,
        plan_id,
        start_at: datetime | None = None,
        pay_mode: str | None = None,
    ) -> CreateSubscriptionResult:
        """
        Создать подписку.
        Правило: trial можно дать только 1 раз на аккаунт (User.trial_used_at).
        """
        start_at = start_at or utcnow()
        if start_at.tzinfo is None:
            start_at = start_at.replace(tzinfo=timezone.utc)

        plan: Plan | None = db.get(Plan, plan_id)
        if not plan or not plan.is_active:
            raise PlanNotAvailable("Plan not found or inactive")

        # Лочим пользователя, чтобы при параллельных запросах не выдать trial дважды
        db_user: User = db.execute(
            select(User).where(User.id == user_id).with_for_update()
        ).scalar_one()

        # Лочим потенциально активную подписку
        existing_active = db.execute(
            select(Subscription)
            .where(
                Subscription.user_id == user_id,
                Subscription.status.in_(
                    [SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE, SubscriptionStatus.PAST_DUE]
                ),
            )
            .with_for_update()
        ).scalar_one_or_none()

        if existing_active:
            raise ActiveSubscriptionExists("User already has an active subscription")

        eligible_trial = bool(plan.trial_days and plan.trial_days > 0 and db_user.trial_used_at is None)

        if eligible_trial:
            status = SubscriptionStatus.TRIAL
            period_start = start_at
            period_end = add_trial(start_at, int(plan.trial_days or 0))

            # trial считается использованным для аккаунта сразу при выдаче
            db_user.trial_used_at = start_at
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

        # если у модели есть pay_mode — установим
        if hasattr(sub, "pay_mode"):
            sub.pay_mode = pay_mode or getattr(sub, "pay_mode", None)

        db.add(sub)
        db.flush()

        # Уведомление: у тебя есть enqueue_subscription_created
        # Для trial тоже используем created (если хочешь — потом добавим отдельный метод trial_started)
        self.notifier.enqueue_subscription_created(
            db,
            user_id=user_id,
            plan_name=plan.name,
            when=start_at,
        )

        db.flush()

        return CreateSubscriptionResult(
            subscription_id=str(sub.id),
            status=sub.status,
            current_period_start=sub.current_period_start,
            current_period_end=sub.current_period_end,
            cancel_at=sub.cancel_at,
        )

    def cancel_at_period_end(self, db: Session, *, subscription_id, when: datetime | None = None) -> Subscription:
        """
        Отмена "в конце периода":
        - cancel_at = current_period_end (а НЕ now)
        - статус НЕ меняем (подписка должна работать до конца периода)
        - после наступления cancel_at подписка перейдёт в EXPIRED (см. apply_time_based_transitions / billing_service)
        """
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

        period_end = sub.current_period_end or when
        if period_end.tzinfo is None:
            period_end = period_end.replace(tzinfo=timezone.utc)

        # Если период уже закончился — сразу EXPIRED
        if period_end <= when:
            sub.status = SubscriptionStatus.EXPIRED
            sub.cancel_at = period_end
            if not sub.canceled_at:
                sub.canceled_at = when
            if sub.current_period_end:
                sub.current_period_end = min(sub.current_period_end, period_end)
            else:
                sub.current_period_end = period_end

            self.notifier.enqueue_subscription_canceled(
                db,
                user_id=sub.user_id,
                plan_name=None,
                when=when,
            )
            return sub

        # Нормальный кейс: подписка работает до конца оплаченного периода
        sub.cancel_at = period_end
        if not sub.canceled_at:
            # время клика "отменить"
            sub.canceled_at = when

        # ВАЖНО: статус не меняем здесь

        self.notifier.enqueue_subscription_canceled(
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

        self.notifier.enqueue_subscription_canceled(
            db,
            user_id=sub.user_id,
            plan_name=None,
            when=when,
        )
        return sub

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

        if sub.current_period_end and now >= sub.current_period_end:
            sub.plan_id = new_plan_id
            sub.status = SubscriptionStatus.ACTIVE
            sub.current_period_start = now
            sub.current_period_end = add_period(now, new_plan.period)
            sub.cancel_at = None
            sub.canceled_at = None
            return sub

        # если мы "меняем со следующего периода", то по сути текущую подписку отменяем на конец периода
        sub.cancel_at = sub.current_period_end or now
        if not sub.canceled_at:
            sub.canceled_at = now
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

    def apply_time_based_transitions(self, db: Session, *, subscription_id, now: datetime | None = None) -> Subscription:
        """
        Переходы по времени:
        - если достигли cancel_at (т.е. конец периода после "cancel at period end") -> EXPIRED
        """
        now = now or utcnow()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)

        sub: Subscription | None = db.execute(
            select(Subscription).where(Subscription.id == subscription_id).with_for_update()
        ).scalar_one_or_none()
        if not sub:
            raise SubscriptionNotFound("Subscription not found")

        if sub.cancel_at and now >= sub.cancel_at and sub.status in (
            SubscriptionStatus.TRIAL,
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.PAST_DUE,
        ):
            sub.status = SubscriptionStatus.EXPIRED
            if sub.current_period_end:
                sub.current_period_end = min(sub.current_period_end, sub.cancel_at)
            else:
                sub.current_period_end = sub.cancel_at
            return sub

        return sub
