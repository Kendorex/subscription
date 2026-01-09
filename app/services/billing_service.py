# services/billing_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.subscription import Subscription, SubscriptionStatus
from models.plan import Plan, BillingPeriod
from models.payment_method import PaymentMethod
from models.invoice import Invoice, InvoiceStatus
from models.transaction import Transaction, TransactionType, TransactionStatus

from payments.gateway import (
    PaymentGateway,
    PaymentTemporaryUnavailable,
    PaymentInsufficientFunds,
    PaymentDeclined,
)

MAX_ATTEMPTS = 3
RETRY_DELAYS = [timedelta(minutes=5), timedelta(hours=1), timedelta(hours=6)]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def add_period(dt: datetime, period: BillingPeriod) -> datetime:
    if period == BillingPeriod.MONTH:
        return dt + timedelta(days=30)
    if period == BillingPeriod.YEAR:
        return dt + timedelta(days=365)
    return dt + timedelta(days=30)


@dataclass(frozen=True)
class BillingResult:
    ok: bool
    status: str
    invoice_id: int | None
    transaction_id: int | None
    retry_at: datetime | None
    message: str


class BillingService:
    """
    Делает одну попытку списания для конкретной подписки.
    Автопродление/первое списание после trial происходит строго когда now >= current_period_end.
    """

    def __init__(self, gateway: PaymentGateway):
        self.gateway = gateway

    def charge_subscription(self, db: Session, subscription_id) -> BillingResult:
        sub: Subscription | None = db.execute(
            select(Subscription).where(Subscription.id == subscription_id).with_for_update()
        ).scalar_one_or_none()

        if not sub:
            return BillingResult(False, "NOT_FOUND", None, None, None, "Subscription not found")

        now = now_utc()

        # не пора списывать — выходим
        if sub.current_period_end and sub.current_period_end > now:
            return BillingResult(True, "SKIP_NOT_DUE", None, None, None, "Not due yet")

        # подписка неактивна
        if sub.status in {SubscriptionStatus.CANCELED, SubscriptionStatus.EXPIRED}:
            return BillingResult(True, "SKIP_INACTIVE", None, None, None, "Subscription inactive")

        # ✅ если наступил cancel_at (отмена в конце периода) — НЕ списываем, отменяем
        if sub.cancel_at and now >= sub.cancel_at and sub.status in {
            SubscriptionStatus.TRIAL,
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.PAST_DUE,
        }:
            sub.status = SubscriptionStatus.CANCELED
            if not sub.canceled_at:
                sub.canceled_at = now
            if sub.current_period_end:
                sub.current_period_end = min(sub.current_period_end, now)
            return BillingResult(True, "SKIP_CANCELED_AT_PERIOD_END", None, None, None, "Canceled at period end")

        # план
        plan: Plan | None = db.get(Plan, sub.plan_id)
        if not plan or not plan.is_active:
            sub.status = SubscriptionStatus.EXPIRED
            return BillingResult(False, "PLAN_INACTIVE", None, None, None, "Plan is inactive")

        # дефолтный способ оплаты
        pm: PaymentMethod | None = db.execute(
            select(PaymentMethod)
            .where(PaymentMethod.user_id == sub.user_id, PaymentMethod.is_default == True)  # noqa: E712
            .order_by(PaymentMethod.id.desc())
        ).scalar_one_or_none()

        if not pm:
            sub.status = SubscriptionStatus.PAST_DUE
            return BillingResult(False, "NO_PAYMENT_METHOD", None, None, None, "No payment method")

        # attempt_no по прошлым инвойсам
        attempt_no = self._next_attempt_no(db, sub.id)

        if attempt_no > MAX_ATTEMPTS:
            sub.status = SubscriptionStatus.PAST_DUE
            return BillingResult(False, "MAX_ATTEMPTS_REACHED", None, None, None, "Max attempts reached")

        idempotency_key = f"sub:{sub.id}:attempt:{attempt_no}"

        invoice = db.execute(select(Invoice).where(Invoice.idempotency_key == idempotency_key)).scalar_one_or_none()
        if not invoice:
            invoice = Invoice(
                subscription_id=sub.id,
                amount_cents=plan.price_cents,
                currency="RUB",
                status=InvoiceStatus.PENDING,
                attempt_no=attempt_no,
                due_at=now,
                paid_at=None,
                idempotency_key=idempotency_key,
            )
            db.add(invoice)
            db.flush()

        tx = db.execute(select(Transaction).where(Transaction.idempotency_key == idempotency_key)).scalar_one_or_none()
        if not tx:
            tx = Transaction(
                user_id=sub.user_id,
                invoice_id=invoice.id,
                type=TransactionType.CHARGE,
                status=TransactionStatus.PENDING,
                amount_cents=invoice.amount_cents,
                currency=invoice.currency,
                provider=pm.provider,
                provider_payment_id=None,
                idempotency_key=idempotency_key,
            )
            db.add(tx)
            db.flush()

        # попытка списания
        try:
            res = self.gateway.charge(
                token_ref=pm.token_ref,
                amount_cents=invoice.amount_cents,
                currency=invoice.currency,
                idempotency_key=idempotency_key,
                description=f"Subscription charge sub#{sub.id} plan={plan.name}",
            )

        except PaymentTemporaryUnavailable as e:
            retry_at = self._compute_retry_at(attempt_no)
            invoice.status = InvoiceStatus.PENDING
            invoice.due_at = retry_at

            tx.status = TransactionStatus.FAILED
            sub.status = SubscriptionStatus.PAST_DUE

            return BillingResult(False, "RETRY", invoice.id, tx.id, retry_at, str(e))

        except PaymentInsufficientFunds as e:
            invoice.status = InvoiceStatus.FAILED
            tx.status = TransactionStatus.FAILED
            sub.status = SubscriptionStatus.PAST_DUE
            return BillingResult(False, "INSUFFICIENT_FUNDS", invoice.id, tx.id, None, str(e))

        except PaymentDeclined as e:
            if attempt_no < MAX_ATTEMPTS:
                retry_at = self._compute_retry_at(attempt_no)

                invoice.status = InvoiceStatus.PENDING
                invoice.due_at = retry_at

                tx.status = TransactionStatus.FAILED
                sub.status = SubscriptionStatus.PAST_DUE

                return BillingResult(False, "DECLINED_RETRY", invoice.id, tx.id, retry_at, str(e))

            invoice.status = InvoiceStatus.FAILED
            tx.status = TransactionStatus.FAILED
            sub.status = SubscriptionStatus.PAST_DUE
            return BillingResult(False, "DECLINED", invoice.id, tx.id, None, str(e))

        # успех
        invoice.status = InvoiceStatus.PAID
        invoice.paid_at = now_utc()

        tx.status = TransactionStatus.SUCCEEDED
        tx.provider_payment_id = res.provider_payment_id

        # ✅ продлеваем период (или начинаем платный после trial) ТОЛЬКО ПОСЛЕ УСПЕШНОЙ ОПЛАТЫ
        start = now_utc()
        sub.current_period_start = start
        sub.current_period_end = add_period(start, plan.period)
        sub.status = SubscriptionStatus.ACTIVE

        return BillingResult(True, "PAID", invoice.id, tx.id, None, "Payment succeeded")

    def _next_attempt_no(self, db: Session, subscription_id) -> int:
        last = db.execute(
            select(Invoice.attempt_no)
            .where(Invoice.subscription_id == subscription_id)
            .order_by(Invoice.attempt_no.desc())
        ).scalars().first()
        return int(last or 0) + 1

    def _compute_retry_at(self, attempt_no: int) -> datetime:
        idx = min(attempt_no - 1, len(RETRY_DELAYS) - 1)
        return now_utc() + RETRY_DELAYS[idx]
