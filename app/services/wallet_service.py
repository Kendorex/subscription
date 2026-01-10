from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.user import User
from models.balance_entry import BalanceEntry, BalanceEntryType


class InsufficientBalance(Exception):
    pass


class WalletService:
    def get_balance(self, db: Session, *, user_id) -> int:
        balance = db.execute(
            select(User.balance_cents).where(User.id == user_id)
        ).scalar_one()
        return int(balance or 0)

    def credit(
        self,
        db: Session,
        *,
        user_id,
        amount_cents: int,
        entry_type: BalanceEntryType,
        idempotency_key: str,
        related_invoice_id=None,
        related_subscription_id=None,
    ) -> None:
        if amount_cents <= 0:
            raise ValueError("amount_cents must be positive")

        user = db.execute(
            select(User).where(User.id == user_id).with_for_update()
        ).scalar_one()

        existing = db.execute(
            select(BalanceEntry).where(BalanceEntry.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing:
            return

        user.balance_cents += amount_cents

        entry = BalanceEntry(
            user_id=user_id,
            amount_cents=amount_cents,
            currency="RUB",
            entry_type=entry_type,
            idempotency_key=idempotency_key,
            related_invoice_id=related_invoice_id,
            related_subscription_id=related_subscription_id,
        )
        db.add(entry)

    def debit_if_possible(
        self,
        db: Session,
        *,
        user_id,
        amount_cents: int,
        entry_type: BalanceEntryType,
        idempotency_key: str,
        related_invoice_id=None,
        related_subscription_id=None,
    ) -> bool:
        if amount_cents <= 0:
            raise ValueError("amount_cents must be positive")

        user = db.execute(
            select(User).where(User.id == user_id).with_for_update()
        ).scalar_one()

        existing = db.execute(
            select(BalanceEntry).where(BalanceEntry.idempotency_key == idempotency_key)
        ).scalar_one_or_none()
        if existing:
            return True

        if user.balance_cents < amount_cents:
            return False

        user.balance_cents -= amount_cents

        entry = BalanceEntry(
            user_id=user_id,
            amount_cents=-amount_cents,
            currency="RUB",
            entry_type=entry_type,
            idempotency_key=idempotency_key,
            related_invoice_id=related_invoice_id,
            related_subscription_id=related_subscription_id,
        )
        db.add(entry)
        return True
