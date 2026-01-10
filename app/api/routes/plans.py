from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from db import get_db
from models.plan import Plan
from schemas.plan import PlanOut, PlanCreate, PlanUpdate
from security.auth import require_admin

router = APIRouter(prefix="/plans", tags=["plans"])

@router.get("", response_model=list[PlanOut])
def list_plans(db: Session = Depends(get_db)):
    return db.execute(select(Plan).where(Plan.is_active == True)).scalars().all()


admin_router = APIRouter(prefix="/admin/plans", tags=["admin:plans"])

@admin_router.post("", response_model=PlanOut, dependencies=[Depends(require_admin)])
def create_plan(payload: PlanCreate, db: Session = Depends(get_db)) -> Plan:
    plan = Plan(
        name=payload.name,
        price_cents=payload.price_cents,
        period=payload.period,
        trial_days=payload.trial_days,
        is_active=True,
    )
    db.add(plan)

    try:
        db.flush()
    except IntegrityError as e:
        db.rollback()
        raise HTTPException(status_code=409, detail="Plan name already exists") from e

    return plan

@admin_router.patch("/{plan_id}", response_model=PlanOut, dependencies=[Depends(require_admin)])
def update_plan(plan_id: str, payload: PlanUpdate, db: Session = Depends(get_db)):
    p = db.get(Plan, plan_id)
    if not p:
        from fastapi import HTTPException
        raise HTTPException(404, "Plan not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(p, k, v)
    db.flush()
    return p
