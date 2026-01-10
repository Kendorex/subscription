from __future__ import annotations

from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session
from sqlalchemy import select

from db import get_db
from models.user import User, UserRole

def get_current_user(
    db: Session = Depends(get_db),
    x_user_email: str | None = Header(default=None, alias="X-User-Email"),
):
    if not x_user_email:
        raise HTTPException(status_code=401, detail="Missing X-User-Email header")

    user = db.execute(select(User).where(User.email == x_user_email)).scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="Unknown user")
    return user

def require_admin(user: User = Depends(get_current_user)):
    if user.role != UserRole.ADMIN:
        raise HTTPException(status_code=403, detail="Admin only")
    return user
