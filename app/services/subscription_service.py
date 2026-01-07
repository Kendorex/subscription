# services/subscription_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.user import User
from models.plan import Plan, BillingPeriod
from models.subscription import Subscription, SubscriptionStatus


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
    - expire trial/period (это удобно делать из scheduled task)
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

        plan: Plan | None = db.get(Plan, plan_id)
        if not plan or not plan.is_active:
            raise PlanNotAvailable("Plan not found or inactive")

        # Блокируем "активную подписку пользователя" от гонок:
        # если два запроса одновременно — один дождётся.
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

        # Trial: если trial_days > 0 => статус TRIAL и period_end = start + trial_days
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

        sub: Subscription | None = db.execute(
            select(Subscription).where(Subscription.id == subscription_id).with_for_update()
        ).scalar_one_or_none()
        if not sub:
            raise SubscriptionNotFound("Subscription not found")

        if sub.status in (SubscriptionStatus.CANCELED, SubscriptionStatus.EXPIRED):
            return sub  # идемпотентно

        # выставляем cancel_at = конец текущего периода
        sub.mark_canceled(when=when)
        return sub

    def cancel_immediately(self, db: Session, *, subscription_id, when: datetime | None = None) -> Subscription:
        when = when or utcnow()

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
        return sub

    # ---------- Change plan ----------

    def change_plan_next_period(self, db: Session, *, subscription_id, new_plan_id) -> Subscription:
        """
        Упрощённо: смена тарифа со следующего периода.
        Для зачёта ок. Для прод — нужно хранить pending_plan_id.
        Тут делаем так: если подписка уже due/закончилась — меняем сразу, иначе отменяем в конце периода
        и создаём новую подписку на новый план со стартом = current_period_end (можно scheduled task).
        """
        sub: Subscription | None = db.execute(
            select(Subscription).where(Subscription.id == subscription_id).with_for_update()
        ).scalar_one_or_none()
        if not sub:
            raise SubscriptionNotFound("Subscription not found")

        new_plan: Plan | None = db.get(Plan, new_plan_id)
        if not new_plan or not new_plan.is_active:
            raise PlanNotAvailable("New plan not found or inactive")

        # Если уже почти закончилось/просрочено — меняем сразу
        now = utcnow()
        if now >= sub.current_period_end:
            sub.plan_id = new_plan_id
            sub.status = SubscriptionStatus.ACTIVE
            sub.current_period_start = now
            sub.current_period_end = add_period(now, new_plan.period)
            sub.cancel_at = None
            sub.canceled_at = None
            return sub

        # Иначе — ставим отмену в конце периода, а создание новой подписки делай task'ом
        # (или можно вернуть клиенту информацию "смена будет применена тогда-то")
        sub.mark_canceled(when=now)
        return sub

    def change_plan_immediately(self, db: Session, *, subscription_id, new_plan_id) -> Subscription:
        """
        Немедленная смена тарифа:
        - закрываем старый период сейчас
        - меняем plan_id
        - стартуем новый период сейчас
        """
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

    # ---------- Expire helpers (for scheduled tasks) ----------

    def apply_time_based_transitions(self, db: Session, *, subscription_id, now: datetime | None = None) -> Subscription:
        """
        Полезно вызывать из scheduled task:
        - если подписка была TRIAL и trial закончился => ACTIVE (если не отменена)
        - если cancel_at наступил => CANCELED
        - если период закончился и подписка не оплачена => PAST_DUE / EXPIRED (тут лучше делегировать BillingService)
        """
        now = now or utcnow()

        sub: Subscription | None = db.execute(
            select(Subscription).where(Subscription.id == subscription_id).with_for_update()
        ).scalar_one_or_none()
        if not sub:
            raise SubscriptionNotFound("Subscription not found")

        # отмена по расписанию
        if sub.cancel_at and now >= sub.cancel_at and sub.status in (SubscriptionStatus.TRIAL, SubscriptionStatus.ACTIVE):
            sub.status = SubscriptionStatus.CANCELED
            if not sub.canceled_at:
                sub.canceled_at = now
            sub.current_period_end = min(sub.current_period_end, now)
            return sub

        # trial -> active (переход периода оплаты будет делаться биллингом)
        if sub.status == SubscriptionStatus.TRIAL and now >= sub.current_period_end:
            # trial закончился: либо сразу делаем ACTIVE (и дальше биллинг)
            sub.status = SubscriptionStatus.ACTIVE
            sub.current_period_start = now
            plan: Plan | None = db.get(Plan, sub.plan_id)
            if plan:
                sub.current_period_end = add_period(now, plan.period)
            else:
                # если план удалён/не найден — истекаем
                sub.status = SubscriptionStatus.EXPIRED

        return sub
