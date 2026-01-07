# services/notification_service.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from models.notification import (
    Notification,
    NotificationType,
    NotificationChannel,
    NotificationStatus,
)

# Ретрай-делеи (если канал временно недоступен)
RETRY_DELAYS = [
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=6),
]


class NotificationError(Exception):
    pass


class NotificationSendTemporaryError(NotificationError):
    """Временная ошибка канала (можно ретраить)."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def compute_retry_at(retry_index: int) -> datetime:
    """
    retry_index: 0..N-1
    """
    idx = min(max(retry_index, 0), len(RETRY_DELAYS) - 1)
    return now_utc() + RETRY_DELAYS[idx]


@dataclass(frozen=True)
class EnqueueResult:
    notification_id: object  # UUID
    status: NotificationStatus


@dataclass(frozen=True)
class SendResult:
    ok: bool
    notification_id: object  # UUID
    status: NotificationStatus
    message: str
    retry_at: datetime | None


class NotificationService:
    """
    Под твою модель Notification:
    - enqueue(): создаёт уведомление (можно с дедупликацией по (user_id,type,channel,payload,status=PENDING))
    - send_due(): отправляет одно уведомление, если оно due (PENDING и scheduled_at <= now)
    - send_due_batch(): отправляет пачку due уведомлений

    Ретраи: статус остаётся PENDING, scheduled_at переносится в будущее.
    """

    # -------------------- enqueue --------------------

    def enqueue(
        self,
        db: Session,
        *,
        user_id,
        type: NotificationType,
        channel: NotificationChannel,
        payload: str | None = None,
        scheduled_at: datetime | None = None,
        dedupe: bool = True,
    ) -> EnqueueResult:
        """
        dedupe=True: чтобы не плодить одинаковые уведомления при повторных вызовах.
        Дедупликация делается по существующему PENDING уведомлению с теми же полями.
        """
        if scheduled_at is not None and scheduled_at.tzinfo is None:
            # приводим к aware UTC на всякий
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

        if dedupe:
            existing = db.execute(
                select(Notification).where(
                    Notification.user_id == user_id,
                    Notification.type == type,
                    Notification.channel == channel,
                    Notification.status == NotificationStatus.PENDING,
                    Notification.payload == payload,
                )
            ).scalar_one_or_none()
            if existing:
                # если у нового есть scheduled_at, можно "подтянуть" на более раннее время
                if scheduled_at and (existing.scheduled_at is None or scheduled_at < existing.scheduled_at):
                    existing.scheduled_at = scheduled_at
                    db.flush()
                return EnqueueResult(notification_id=existing.id, status=existing.status)

        n = Notification(
            user_id=user_id,
            type=type,
            channel=channel,
            status=NotificationStatus.PENDING,
            payload=payload,
            scheduled_at=scheduled_at,
            sent_at=None,
        )
        db.add(n)
        db.flush()
        return EnqueueResult(notification_id=n.id, status=n.status)

    # -------------------- sending --------------------

    def send_due(self, db: Session, *, notification_id, retry_index: int = 0) -> SendResult:
        """
        Отправляет уведомление, если:
        - оно существует
        - status == PENDING
        - scheduled_at is NULL or <= now

        retry_index: какой по счёту ретрай планировать при временной ошибке (0..)
        (так как attempt_no в модели нет, этот счётчик хранить негде; его можно передавать извне,
         либо добавить колонку attempt_no в модель.)
        """
        n: Notification | None = db.execute(
            select(Notification).where(Notification.id == notification_id).with_for_update()
        ).scalar_one_or_none()

        if not n:
            return SendResult(False, notification_id, NotificationStatus.FAILED, "Not found", None)

        if n.status in (NotificationStatus.SENT, NotificationStatus.FAILED):
            return SendResult(True, n.id, n.status, "Already finalized", None)

        if n.status != NotificationStatus.PENDING:
            return SendResult(True, n.id, n.status, f"Skip status={n.status}", None)

        now = now_utc()
        if n.scheduled_at and n.scheduled_at > now:
            return SendResult(True, n.id, n.status, "Not due yet", n.scheduled_at)

        try:
            self._send_via_channel(n)
        except NotificationSendTemporaryError as e:
            retry_at = compute_retry_at(retry_index)
            n.scheduled_at = retry_at
            # статус остаётся PENDING
            return SendResult(False, n.id, n.status, f"Temporary failure: {e}. Rescheduled.", retry_at)
        except Exception as e:
            n.mark_failed()
            return SendResult(False, n.id, n.status, f"Fatal error: {e}", None)

        # успех
        n.mark_sent(when=now)
        n.scheduled_at = None
        return SendResult(True, n.id, n.status, "Sent", None)

    def send_due_batch(self, db: Session, *, limit: int = 50) -> list[SendResult]:
        """
        Отправляет пачку due уведомлений.
        """
        now = now_utc()

        due = (
            db.execute(
                select(Notification)
                .where(
                    Notification.status == NotificationStatus.PENDING,
                    (Notification.scheduled_at.is_(None)) | (Notification.scheduled_at <= now),
                )
                .order_by(Notification.created_at.asc())
                .limit(limit)
            )
            .scalars()
            .all()
        )

        results: list[SendResult] = []
        for n in due:
            # retry_index без attempt_no хранить негде -> используем 0 (первый делей)
            results.append(self.send_due(db, notification_id=n.id, retry_index=0))
        return results

    # -------------------- channel adapters --------------------

    def _send_via_channel(self, n: Notification) -> None:
        """
        Под твой enum NotificationChannel: IN_APP, EMAIL
        """
        if n.channel == NotificationChannel.IN_APP:
            # IN_APP считаем доставленным: запись уже есть в БД
            return

        if n.channel == NotificationChannel.EMAIL:
            self._send_email(n.user_id, n.payload or "")
            return

        raise Exception(f"Unsupported channel: {n.channel}")

    def _send_email(self, user_id, payload: str) -> None:
        """
        Фейковая отправка email.
        Чтобы симулировать сбои - раскомментируй:
        """
        # raise NotificationSendTemporaryError("SMTP timeout")
        return
