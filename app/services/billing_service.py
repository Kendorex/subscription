# billing_service.py
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
from models.balance_entry import BalanceEntryType
from models.user import User
from services.notification_service import NotificationService
from services.wallet_service import WalletService

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
    if period == BillingPeriod.DAY:
        return dt + timedelta(days=1)
    if period == BillingPeriod.MONTH:
        return dt + timedelta(days=30)
    if period == BillingPeriod.YEAR:
        return dt + timedelta(days=365)
    return dt + timedelta(days=30)


def _fmt_period_key(dt: datetime) -> str:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


@dataclass(frozen=True)
class BillingResult:
    ok: bool
    status: str
    invoice_id: object | None
    transaction_id: object | None
    retry_at: datetime | None
    message: str


class BillingService:
    def __init__(self, gateway: PaymentGateway):
        self.gateway = gateway
        self.wallet = WalletService()
        self.notifier = NotificationService()

    def charge_subscription(self, db: Session, subscription_id) -> BillingResult:
        sub: Subscription | None = db.execute(
            select(Subscription).where(Subscription.id == subscription_id).with_for_update()
        ).scalar_one_or_none()

        if not sub:
            return BillingResult(False, "NOT_FOUND", None, None, None, "Subscription not found")

        now = now_utc()

        if sub.current_period_end and sub.current_period_end > now:
            return BillingResult(True, "SKIP_NOT_DUE", None, None, None, "Not due yet")

        if sub.status in {SubscriptionStatus.CANCELED, SubscriptionStatus.EXPIRED}:
            return BillingResult(True, "SKIP_INACTIVE", None, None, None, "Subscription inactive")

        # cancel-at-period-end reached -> EXPIRED and skip charging
        if sub.cancel_at and now >= sub.cancel_at and sub.status in {
            SubscriptionStatus.TRIAL,
            SubscriptionStatus.ACTIVE,
            SubscriptionStatus.PAST_DUE,
        }:
            sub.status = SubscriptionStatus.EXPIRED
            if sub.current_period_end:
                sub.current_period_end = min(sub.current_period_end, sub.cancel_at)
            else:
                sub.current_period_end = sub.cancel_at
            return BillingResult(True, "SKIP_EXPIRED_AFTER_CANCEL", None, None, None, "Expired after cancel_at")

        plan: Plan | None = db.get(Plan, sub.plan_id)
        if not plan or not plan.is_active:
            sub.status = SubscriptionStatus.EXPIRED
            return BillingResult(False, "PLAN_INACTIVE", None, None, None, "Plan is inactive")

        period_due_at = sub.current_period_end or now
        if period_due_at.tzinfo is None:
            period_due_at = period_due_at.replace(tzinfo=timezone.utc)
        period_key = _fmt_period_key(period_due_at)

        attempt_no = self._next_attempt_no_period(db, subscription_id=sub.id, period_key=period_key)
        if attempt_no > MAX_ATTEMPTS:
            sub.status = SubscriptionStatus.PAST_DUE
            return BillingResult(False, "MAX_ATTEMPTS_REACHED", None, None, None, "Max attempts reached")

        new_idem_key = f"sub:{sub.id}:period:{period_key}:attempt:{attempt_no}"
        legacy_idem_key = f"sub:{sub.id}:attempt:{attempt_no}"

        invoice = db.execute(select(Invoice).where(Invoice.idempotency_key == new_idem_key)).scalar_one_or_none()
        tx = db.execute(select(Transaction).where(Transaction.idempotency_key == new_idem_key)).scalar_one_or_none()

        if not invoice:
            invoice = db.execute(select(Invoice).where(Invoice.idempotency_key == legacy_idem_key)).scalar_one_or_none()
        if not tx:
            tx = db.execute(select(Transaction).where(Transaction.idempotency_key == legacy_idem_key)).scalar_one_or_none()

        active_idem_key = legacy_idem_key if (invoice and invoice.idempotency_key == legacy_idem_key) else new_idem_key
        if tx and tx.idempotency_key == legacy_idem_key:
            active_idem_key = legacy_idem_key

        if not invoice:
            invoice = Invoice(
                subscription_id=sub.id,
                amount_cents=plan.price_cents,
                currency="RUB",
                status=InvoiceStatus.PENDING,
                attempt_no=attempt_no,
                due_at=period_due_at,
                paid_at=None,
                idempotency_key=active_idem_key,
            )
            db.add(invoice)
            db.flush()

        if not tx:
            tx = Transaction(
                user_id=sub.user_id,
                invoice_id=invoice.id,
                type=TransactionType.CHARGE,
                status=TransactionStatus.PENDING,
                amount_cents=invoice.amount_cents,
                currency=invoice.currency,
                provider="fake",
                provider_payment_id=None,
                idempotency_key=active_idem_key,
            )
            db.add(tx)
            db.flush()

        pay_mode = getattr(sub, "pay_mode", "balance") or "balance"

        if pay_mode == "balance":
            paid_from_balance = self.wallet.debit_if_possible(
                db,
                user_id=sub.user_id,
                amount_cents=invoice.amount_cents,
                entry_type=BalanceEntryType.SUBSCRIPTION_CHARGE,
                idempotency_key=active_idem_key,
                related_invoice_id=invoice.id,
                related_subscription_id=sub.id,
            )

            if paid_from_balance:
                invoice.status = InvoiceStatus.PAID
                invoice.paid_at = now

                tx.status = TransactionStatus.SUCCEEDED
                tx.provider = "BALANCE"
                tx.provider_payment_id = "balance"

                start = now
                sub.current_period_start = start
                sub.current_period_end = add_period(start, plan.period)
                sub.status = SubscriptionStatus.ACTIVE

                u: User | None = db.get(User, sub.user_id)
                if u and u.trial_used_at is None:
                    u.trial_used_at = now

                return BillingResult(True, "PAID_BALANCE", invoice.id, tx.id, None, "Paid from balance")

            available = self.wallet.get_balance(db, user_id=sub.user_id)

            invoice.status = InvoiceStatus.FAILED
            tx.status = TransactionStatus.FAILED
            sub.status = SubscriptionStatus.PAST_DUE

            self.notifier.enqueue_insufficient_balance(
                db,
                user_id=sub.user_id,
                plan_name=plan.name,
                required_cents=invoice.amount_cents,
                available_cents=available,
                when=now,
                dedupe=False,
            )

            return BillingResult(
                False,
                "INSUFFICIENT_FUNDS_BALANCE",
                invoice.id,
                tx.id,
                None,
                "Insufficient balance funds",
            )

        pm: PaymentMethod | None = db.execute(
            select(PaymentMethod)
            .where(PaymentMethod.user_id == sub.user_id, PaymentMethod.is_default == True)
            .order_by(PaymentMethod.id.desc())
        ).scalar_one_or_none()

        if not pm:
            invoice.status = InvoiceStatus.FAILED
            tx.status = TransactionStatus.FAILED
            sub.status = SubscriptionStatus.PAST_DUE
            return BillingResult(False, "NO_PAYMENT_METHOD", invoice.id, tx.id, None, "No payment method")

        tx.provider = pm.provider

        try:
            res = self.gateway.charge(
                token_ref=pm.token_ref,
                amount_cents=invoice.amount_cents,
                currency=invoice.currency,
                idempotency_key=active_idem_key,
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

        invoice.status = InvoiceStatus.PAID
        invoice.paid_at = now

        tx.status = TransactionStatus.SUCCEEDED
        tx.provider_payment_id = res.provider_payment_id

        start = now
        sub.current_period_start = start
        sub.current_period_end = add_period(start, plan.period)
        sub.status = SubscriptionStatus.ACTIVE

        u: User | None = db.get(User, sub.user_id)
        if u and u.trial_used_at is None:
            u.trial_used_at = now

        return BillingResult(True, "PAID", invoice.id, tx.id, None, "Payment succeeded")

    def _next_attempt_no_period(self, db: Session, *, subscription_id: str, period_key: str) -> int:
        prefix = f"sub:{subscription_id}:period:{period_key}:attempt:"
        last = db.execute(
            select(Invoice.attempt_no)
            .where(
                Invoice.subscription_id == subscription_id,
                Invoice.idempotency_key.like(prefix + "%"),
            )
            .order_by(Invoice.attempt_no.desc())
        ).scalars().first()
        return int(last or 0) + 1

    def _compute_retry_at(self, attempt_no: int) -> datetime:
        idx = min(attempt_no - 1, len(RETRY_DELAYS) - 1)
        return now_utc() + RETRY_DELAYS[idx]
