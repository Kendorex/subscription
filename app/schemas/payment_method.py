from __future__ import annotations
from pydantic import Field
from .common import APIModel

class PaymentMethodOut(APIModel):
    id: int
    user_id: str
    provider: str
    token_ref: str
    is_default: bool

class PaymentMethodCreate(APIModel):
    provider: str = Field(default="fake", min_length=2, max_length=50)
    token_ref: str = Field(min_length=3, max_length=200)
    is_default: bool = True
