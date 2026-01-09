from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from db import get_db
from security.auth import get_current_user, require_admin
from models.user import User
from models.balance_entry import BalanceEntry, BalanceEntryType

from schemas.balance import BalanceOut, BalanceTopUpIn, BalanceEntryOut
from services.wallet_service import WalletService

router = APIRouter(prefix="/balance", tags=["balance"])

@router.get("", response_model=BalanceOut)
def get_my_balance(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BalanceOut:
    return BalanceOut(balance_cents=int(user.balance_cents or 0), currency="RUB")


@router.post("/topup", response_model=BalanceOut)
def topup_my_balance(
    body: BalanceTopUpIn,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> BalanceOut:
    WalletService().credit(
        db,
        user_id=user.id,
        amount_cents=body.amount_cents,
        entry_type=BalanceEntryType.TOPUP,
        idempotency_key=f"topup:{user.id}:{body.idempotency_key}",
        related_invoice_id=None,
        related_subscription_id=None,
    )
    db.flush()
    db.refresh(user)
    return BalanceOut(balance_cents=int(user.balance_cents or 0), currency="RUB")


@router.get("/entries", response_model=list[BalanceEntryOut])
def list_my_balance_entries(
    limit: int = Query(50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
) -> list[BalanceEntryOut]:
    rows = (
        db.execute(
            select(BalanceEntry)
            .where(BalanceEntry.user_id == user.id)
            .order_by(BalanceEntry.created_at.desc())
            .limit(limit)
        )
        .scalars()
        .all()
    )

    return [
        BalanceEntryOut(
            id=r.id,
            amount_cents=r.amount_cents,
            currency=r.currency,
            entry_type=str(r.entry_type),
            created_at=r.created_at,
            idempotency_key=r.idempotency_key,
            related_invoice_id=r.related_invoice_id,
            related_subscription_id=r.related_subscription_id,
        )
        for r in rows
    ]

@router.post("/admin/topup/{user_id}", response_model=BalanceOut, include_in_schema=True)
def admin_topup_user_balance(
    user_id: str,
    body: BalanceTopUpIn,
    db: Session = Depends(get_db),
    _admin: User = Depends(require_admin),
) -> BalanceOut:
    target: User | None = db.get(User, user_id)
    if not target:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found")

    WalletService().credit(
        db,
        user_id=target.id,
        amount_cents=body.amount_cents,
        entry_type=BalanceEntryType.ADJUSTMENT,
        idempotency_key=f"admin_topup:{target.id}:{body.idempotency_key}",
        related_invoice_id=None,
        related_subscription_id=None,
    )
    db.flush()
    db.refresh(target)
    return BalanceOut(balance_cents=int(target.balance_cents or 0), currency="RUB")
