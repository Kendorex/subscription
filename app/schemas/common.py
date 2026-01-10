from __future__ import annotations
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class APIModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)

class Message(APIModel):
    message: str

class IdResponse(APIModel):
    id: str

class DatetimeRange(APIModel):
    start: datetime | None = None
    end: datetime | None = None
