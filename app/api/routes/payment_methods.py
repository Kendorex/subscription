from __future__ import annotations
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select, update

from db import get_db
from models.payment_method import PaymentMethod
from schemas.payment_method import PaymentMethodOut, PaymentMethodCreate
from security.auth import get_current_user

router = APIRouter(prefix="/payment-methods", tags=["payment-methods"])

@router.get("", response_model=list[PaymentMethodOut])
def list_my_methods(db: Session = Depends(get_db), user=Depends(get_current_user)):
    return db.execute(select(PaymentMethod).where(PaymentMethod.user_id == user.id)).scalars().all()

@router.post("", response_model=PaymentMethodOut)
def add_method(payload: PaymentMethodCreate, db: Session = Depends(get_db), user=Depends(get_current_user)):
    if payload.is_default:
        db.execute(
            update(PaymentMethod)
            .where(PaymentMethod.user_id == user.id)
            .values(is_default=False)
        )
    pm = PaymentMethod(user_id=user.id, **payload.model_dump())
    db.add(pm)
    db.flush()
    return pm

@router.post("/{pm_id}/make-default", response_model=PaymentMethodOut)
def make_default(pm_id: UUID, db: Session = Depends(get_db), user=Depends(get_current_user)):
    pm = db.get(PaymentMethod, pm_id)
    if not pm or pm.user_id != user.id:
        raise HTTPException(404, "Payment method not found")
    db.execute(
        update(PaymentMethod)
        .where(PaymentMethod.user_id == user.id)
        .values(is_default=False)
    )
    pm.is_default = True
    db.flush()
    return pm
