from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr
from sqlalchemy.orm import Session
from sqlalchemy import select

from db import get_db
from models.user import User, UserRole

router = APIRouter(prefix="/dev", tags=["dev-auth"])

class RegisterIn(BaseModel):
    email: EmailStr
    role: UserRole = UserRole.USER
    password: str | None = None

class RegisterOut(BaseModel):
    id: str
    email: EmailStr
    role: UserRole

def _dev_password_hash(password: str | None) -> str:
    raw = (password or "dev").strip()
    return f"DEV_HASH::{raw}"

@router.post("/register", response_model=RegisterOut)
def dev_register(payload: RegisterIn, db: Session = Depends(get_db)):
    email = str(payload.email).strip().lower()

    existing = db.execute(select(User).where(User.email == email)).scalar_one_or_none()
    if existing:
        return RegisterOut(id=str(existing.id), email=existing.email, role=existing.role)

    user = User(
        email=email,
        role=payload.role,
        password_hash=_dev_password_hash(payload.password),
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return RegisterOut(id=str(user.id), email=user.email, role=user.role)

@router.get("/whoami", response_model=RegisterOut)
def dev_whoami(email: EmailStr, db: Session = Depends(get_db)):
    e = str(email).strip().lower()
    user = db.execute(select(User).where(User.email == e)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return RegisterOut(id=str(user.id), email=user.email, role=user.role)
