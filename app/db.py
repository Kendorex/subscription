# app/db.py
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
    """
    PowerShell/копипаст иногда добавляет невидимые символы (NBSP \u00A0, BOM и т.п.),
    из-за чего psycopg2 падает с UnicodeDecodeError при разборе DSN.

    Здесь мы:
    - удаляем BOM
    - заменяем NBSP на обычный пробел
    - убираем все пробелы вокруг, и вообще вычищаем пробелы внутри URL (их там быть не должно)
    """
    if raw is None:
        return raw

    s = str(raw)

    # BOM (на всякий)
    s = s.replace("\ufeff", "")

    # NBSP -> обычный пробел
    s = s.replace("\u00a0", " ")

    # trim
    s = s.strip()

    # В URL пробелов быть не должно — часто именно они ломают DSN
    s = s.replace(" ", "")

    return s


def _get_database_url() -> str:
    raw = os.environ.get("DATABASE_URL")  # именно env, без getenv сюрпризов
    if not raw:
        raise RuntimeError(
            "DATABASE_URL env var is not set.\n"
            "Example:\n"
            "  postgresql+psycopg2://postgres:postgres@localhost:5432/subscriptions"
        )

    cleaned = _clean_database_url(raw)

    # Мини-диагностика (очень помогает, если снова будет странный символ)
    if cleaned != raw:
        print("⚠️ DATABASE_URL was sanitized (invisible chars/spaces removed).")
        print("  raw     :", repr(raw))
        print("  cleaned :", repr(cleaned))

    return cleaned


DATABASE_URL = _get_database_url()

engine = create_engine(
    DATABASE_URL,
    future=True,
    json_serializer=lambda obj: json_dumps(obj, ensure_ascii=False),  # ✅
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)

Base = declarative_base()


def get_db() -> Generator[Session, None, None]:
    """
    FastAPI dependency:
      def endpoint(db: Session = Depends(get_db)): ...
    """
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
    """Create all tables (fast for a student project; Alembic is better for prod)."""
    Base.metadata.create_all(bind=engine)


