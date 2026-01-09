# services/notification_service.py
from __future__ import annotations

import json
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.utils.json_utils import json_dumps
from models.notification import (
    Notification,
    NotificationType,
    NotificationChannel,
    NotificationStatus,
)
from models.user import User  # <-- путь поправь под свой проект

RETRY_DELAYS = [
    timedelta(minutes=1),
    timedelta(minutes=5),
    timedelta(minutes=15),
    timedelta(hours=1),
    timedelta(hours=6),
]

FAIL_THROTTLE_WINDOW = timedelta(minutes=10)


class NotificationError(Exception):
    pass


class NotificationSendTemporaryError(NotificationError):
    """Временная ошибка канала (можно ретраить)."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def compute_retry_at(retry_index: int) -> datetime:
    idx = min(max(retry_index, 0), len(RETRY_DELAYS) - 1)
    return now_utc() + RETRY_DELAYS[idx]


def _payload_load(payload: str | None) -> dict[str, Any]:
    if not payload:
        return {}
    try:
        return json.loads(payload)
    except Exception:
        # на случай если раньше payload был обычной строкой
        return {"text": payload}


def _payload_dump(data: dict[str, Any]) -> str:
    return json_dumps(data, ensure_ascii=False)


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
    - enqueue(): создаёт уведомление
    - send_due(): отправляет уведомление, если оно due
    - send_due_batch(): отправляет пачку due уведомлений

    Ретраи:
      - статус остаётся PENDING
      - scheduled_at переносится
      - attempt хранится в payload["attempt"] (без миграции БД)
    """

    # -------------------- public: enqueue helpers --------------------

    def enqueue_subscription_created(
        self, db: Session, *, user_id, plan_name: str | None = None, when: datetime | None = None
    ) -> EnqueueResult:
        subject, text = self._tpl_subscription_created(plan_name=plan_name, when=when)
        return self._enqueue_email(db, user_id=user_id, type=NotificationType.SUBSCRIPTION_CREATED,
                                  subject=subject, text=text)

    def enqueue_subscription_renewed(
        self, db: Session, *, user_id, plan_name: str | None = None, when: datetime | None = None
    ) -> EnqueueResult:
        subject, text = self._tpl_subscription_renewed(plan_name=plan_name, when=when)
        return self._enqueue_email(db, user_id=user_id, type=NotificationType.SUBSCRIPTION_RENEWED,
                                  subject=subject, text=text)

    def enqueue_subscription_canceled(
        self, db: Session, *, user_id, plan_name: str | None = None, when: datetime | None = None
    ) -> EnqueueResult:
        subject, text = self._tpl_subscription_canceled(plan_name=plan_name, when=when)
        return self._enqueue_email(db, user_id=user_id, type=NotificationType.SUBSCRIPTION_CANCELED,
                                  subject=subject, text=text)

    def enqueue_payment_ok(
        self,
        db: Session,
        *,
        user_id,
        plan_name: str | None = None,
        subscription_id: str | None = None,
        invoice_id: str | None = None,
        when: datetime | None = None,
        dedupe: bool = True,
    ) -> EnqueueResult:
        when_s = self._fmt_when(when or now_utc())
        plan_s = f"Тариф: {plan_name}." if plan_name else ""
        sub_s = f"Subscription: {subscription_id}." if subscription_id else ""
        inv_s = f"Invoice: {invoice_id}." if invoice_id else ""

        subject = "Оплата по подписке прошла успешно"
        text = (
            f"Оплата прошла успешно ({when_s}).\n"
            f"{plan_s}\n"
            f"{sub_s}\n"
            f"{inv_s}\n"
        ).strip()

        return self._enqueue_email(
            db,
            user_id=user_id,
            type=NotificationType.PAYMENT_OK,
            subject=subject,
            text=text,
            dedupe=dedupe,
        )



    def _fmt_dt(self, when: datetime | None) -> str:
        # backward-compat: older templates call _fmt_dt
        return self._fmt_when(when)


    def enqueue_payment_failed(
        self,
        db: Session,
        *,
        user_id,
        plan_name: str | None = None,
        reason: str | None = None,
        retry_at: datetime | None = None,
        when: datetime | None = None,
        scheduled_at: datetime | None = None,
        dedupe: bool = True,
    ) -> EnqueueResult:
        subject, text = self._tpl_payment_failed(
            plan_name=plan_name,
            reason=reason,
            retry_at=retry_at,
            when=when,
        )
        return self._enqueue_email(
            db,
            user_id=user_id,
            type=NotificationType.PAYMENT_FAIL,
            subject=subject,
            text=text,
            scheduled_at=scheduled_at,
            dedupe=dedupe,
        )

    def _tpl_payment_failed(
        self,
        *,
        plan_name: str | None = None,
        reason: str | None = None,
        retry_at: datetime | None = None,
        when: datetime | None = None,
    ) -> tuple[str, str]:
        when_s = self._fmt_when(when or now_utc())
        plan_s = f"Тариф: {plan_name}." if plan_name else ""
        reason_s = f"Причина: {reason}." if reason else ""
        retry_s = f"Следующая попытка: {self._fmt_when(retry_at)}." if retry_at else ""
        subject = "Не удалось списать оплату по подписке"
        text = (
            f"Мы попытались списать оплату по вашей подписке ({when_s}).\n"
            f"{plan_s}\n"
            f"{reason_s}\n"
            f"{retry_s}\n"
            "Проверьте способ оплаты и попробуйте снова."
        ).strip()
        return subject, text

    def _enqueue_email(
            self,
            db: Session,
            *,
            user_id,
            type: NotificationType,
            subject: str,
            text: str,
            scheduled_at: datetime | None = None,
            dedupe: bool = True,
        ) -> EnqueueResult:
            payload = _payload_dump(
                {
                    "subject": subject,
                    "text": text,
                    "attempt": 0,
                }
            )
            return self.enqueue(
                db,
                user_id=user_id,
                type=type,
                channel=NotificationChannel.EMAIL,
                payload=payload,
                scheduled_at=scheduled_at,
                dedupe=dedupe,
                throttle=(type == NotificationType.PAYMENT_FAIL),
            )
    # -------------------- enqueue (core) --------------------

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
        throttle: bool = True,
    ) -> EnqueueResult:
        if scheduled_at is not None and scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)

        # анти-спам для PAYMENT_FAIL (по недавно SENT)
        if throttle and type == NotificationType.PAYMENT_FAIL:
            since = now_utc() - FAIL_THROTTLE_WINDOW
            recent_sent = db.execute(
                select(Notification).where(
                    Notification.user_id == user_id,
                    Notification.type == type,
                    Notification.channel == channel,
                    Notification.sent_at.is_not(None),
                    Notification.sent_at >= since,
                )
            ).scalar_one_or_none()
            if recent_sent:
                return EnqueueResult(notification_id=recent_sent.id, status=recent_sent.status)

        # дедупликация: не плодим одинаковые PENDING
        if dedupe:
            existing_pending = db.execute(
                select(Notification).where(
                    Notification.user_id == user_id,
                    Notification.type == type,
                    Notification.channel == channel,
                    Notification.status == NotificationStatus.PENDING,
                    Notification.payload == payload,
                )
            ).scalars().first()
            if existing_pending:
                if scheduled_at and (
                    existing_pending.scheduled_at is None or scheduled_at < existing_pending.scheduled_at
                ):
                    existing_pending.scheduled_at = scheduled_at
                    db.flush()
                return EnqueueResult(notification_id=existing_pending.id, status=existing_pending.status)

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

    def send_due(self, db: Session, *, notification_id) -> SendResult:
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

        payload = _payload_load(n.payload)
        attempt = int(payload.get("attempt", 0))

        try:
            self._send_via_channel(db, n, payload)
        except NotificationSendTemporaryError as e:
            retry_at = compute_retry_at(attempt)
            payload["attempt"] = attempt + 1
            n.payload = _payload_dump(payload)
            n.scheduled_at = retry_at
            # статус остаётся PENDING
            return SendResult(False, n.id, n.status, f"Temporary failure: {e}. Rescheduled.", retry_at)
        except Exception as e:
            print(
                f"[notification] FATAL id={n.id} user_id={n.user_id} "
                f"type={n.type} channel={n.channel} err={repr(e)}"
            )
            n.mark_failed()
            return SendResult(False, n.id, n.status, f"Fatal error: {e}", None)


        n.mark_sent(when=now)
        n.scheduled_at = None
        return SendResult(True, n.id, n.status, "Sent", None)

    def send_due_batch(self, db: Session, *, limit: int = 50) -> list[SendResult]:
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

        return [self.send_due(db, notification_id=n.id) for n in due]

    # -------------------- channel adapters --------------------

    def _send_via_channel(self, db: Session, n: Notification, payload: dict[str, Any]) -> None:
        if n.channel == NotificationChannel.IN_APP:
            return

        if n.channel == NotificationChannel.EMAIL:
            user_email = db.execute(select(User.email).where(User.id == n.user_id)).scalar_one_or_none()
            if not user_email:
                raise Exception("User not found or user.email is empty")

            subject = str(payload.get("subject") or "Notification").strip()
            text = str(payload.get("text") or "").strip()

            self._send_email(to_email=user_email, subject=subject, text=text)
            return

        raise Exception(f"Unsupported channel: {n.channel}")

    def _send_email(self, *, to_email: str, subject: str, text: str) -> None:
        host = os.getenv("SMTP_HOST")
        port = int(os.getenv("SMTP_PORT", "587"))
        user = os.getenv("SMTP_USER")
        password = os.getenv("SMTP_PASSWORD")
        from_email = os.getenv("SMTP_FROM") or user

        if not host or not user or not password or not from_email:
            raise Exception("SMTP env is not configured (SMTP_HOST/SMTP_USER/SMTP_PASSWORD/SMTP_FROM)")

        msg = EmailMessage()
        msg["From"] = from_email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.set_content(text)

        context = ssl.create_default_context()

        try:
            with smtplib.SMTP(host, port, timeout=20) as server:
                server.ehlo()
                server.starttls(context=context)
                server.ehlo()
                server.login(user, password)
                server.send_message(msg)
        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, TimeoutError) as e:
            raise NotificationSendTemporaryError(str(e))
        except smtplib.SMTPException as e:
            # часто тоже временно (лимиты, greylisting, etc.)
            raise NotificationSendTemporaryError(str(e))

    # -------------------- templates --------------------

    def _fmt_when(self, when: datetime | None) -> str:
        dt = when or now_utc()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def _tpl_subscription_created(self, *, plan_name: str | None, when: datetime | None) -> tuple[str, str]:
        plan = plan_name or "ваш тариф"
        subject = "Подписка оформлена"
        text = f"Подписка оформлена.\nТариф: {plan}\nДата: {self._fmt_when(when)}\n"
        return subject, text

    def _tpl_subscription_renewed(self, *, plan_name: str | None, when: datetime | None) -> tuple[str, str]:
        plan = plan_name or "ваш тариф"
        subject = "Подписка продлена"
        text = f"Подписка продлена.\nТариф: {plan}\nДата: {self._fmt_when(when)}\n"
        return subject, text

    def _tpl_subscription_canceled(self, *, plan_name: str | None, when: datetime | None) -> tuple[str, str]:
        plan = plan_name or "ваш тариф"
        subject = "Подписка отменена"
        text = f"Подписка отменена.\nТариф: {plan}\nДата: {self._fmt_when(when)}\n"
        return subject, text
