from __future__ import annotations

from dotenv import load_dotenv

from utils.json_utils import json_dumps
load_dotenv()

import os
import os
from typing import Generator, Optional

from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base, sessionmaker, Session

def _clean_database_url(raw: str) -> str:
    if raw is None:
        return raw

    s = str(raw)

    s = s.replace("\ufeff", "")

    s = s.replace("\u00a0", " ")

    s = s.strip()

    s = s.replace(" ", "")

    return s


def _get_database_url() -> str:
    raw = os.environ.get("DATABASE_URL")
    if not raw:
        raise RuntimeError(
            "DATABASE_URL env var is not set.\n"
            "Example:\n"
            "  postgresql+psycopg2://postgres:postgres@localhost:5432/subscriptions"
        )

    cleaned = _clean_database_url(raw)

    if cleaned != raw:
        print("⚠️ DATABASE_URL was sanitized (invisible chars/spaces removed).")
        print("  raw     :", repr(raw))
        print("  cleaned :", repr(cleaned))

    return cleaned


DATABASE_URL = _get_database_url()

engine = create_engine(
    DATABASE_URL,
    future=True,
    json_serializer=lambda obj: json_dumps(obj, ensure_ascii=False),
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


