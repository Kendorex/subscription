from __future__ import annotations

import json
import os
import smtplib
import ssl
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import formatdate, make_msgid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.utils.json_utils import json_dumps
from models.notification import (
    Notification,
    NotificationType,
    NotificationChannel,
    NotificationStatus,
)
from models.user import User

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


class NotificationSendPermanentError(NotificationError):
    """Постоянная ошибка канала (ретраить не надо)."""

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
        return {"text": payload}


def _payload_dump(data: dict[str, Any]) -> str:
    return json_dumps(data, ensure_ascii=False)


@dataclass(frozen=True)
class EnqueueResult:
    notification_id: object
    status: NotificationStatus


@dataclass(frozen=True)
class SendResult:
    ok: bool
    notification_id: object
    status: NotificationStatus
    message: str
    retry_at: datetime | None


class NotificationService:
    
    def enqueue_subscription_created(
        self, db: Session, *, user_id, plan_name: str | None = None, when: datetime | None = None
    ) -> EnqueueResult:
        subject, text = self._tpl_subscription_created(plan_name=plan_name, when=when)
        return self._enqueue_email(
            db, user_id=user_id, type=NotificationType.SUBSCRIPTION_CREATED, subject=subject, text=text
        )

    def enqueue_subscription_renewed(
        self, db: Session, *, user_id, plan_name: str | None = None, when: datetime | None = None
    ) -> EnqueueResult:
        subject, text = self._tpl_subscription_renewed(plan_name=plan_name, when=when)
        return self._enqueue_email(
            db, user_id=user_id, type=NotificationType.SUBSCRIPTION_RENEWED, subject=subject, text=text
        )

    def enqueue_subscription_canceled(
        self, db: Session, *, user_id, plan_name: str | None = None, when: datetime | None = None
    ) -> EnqueueResult:
        subject, text = self._tpl_subscription_canceled(plan_name=plan_name, when=when)
        return self._enqueue_email(
            db, user_id=user_id, type=NotificationType.SUBSCRIPTION_CANCELED, subject=subject, text=text
        )

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
            f"Здравствуйте!\n\n"
            f"Оплата прошла успешно ({when_s}).\n"
            f"{plan_s}\n"
            f"{sub_s}\n"
            f"{inv_s}\n\n"
            f"Это автоматическое уведомление."
        ).strip()

        return self._enqueue_email(
            db,
            user_id=user_id,
            type=NotificationType.PAYMENT_OK,
            subject=subject,
            text=text,
            dedupe=dedupe,
        )

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

    def enqueue_insufficient_balance(
        self,
        db: Session,
        *,
        user_id,
        plan_name: str | None = None,
        required_cents: int | None = None,
        available_cents: int | None = None,
        when: datetime | None = None,
        dedupe: bool = True,
    ) -> EnqueueResult:
        when_s = self._fmt_when(when or now_utc())
        plan_s = f"Тариф: {plan_name}." if plan_name else ""
        need_s = f"Нужно: {required_cents} cents." if required_cents is not None else ""
        have_s = f"Доступно: {available_cents} cents." if available_cents is not None else ""

        subject = "Недостаточно средств на балансе"
        text = (
            f"Здравствуйте!\n\n"
            f"Не удалось списать оплату с баланса ({when_s}).\n"
            f"{plan_s}\n"
            f"{need_s}\n"
            f"{have_s}\n\n"
            "Пополните баланс или выберите оплату картой.\n"
            "Это автоматическое уведомление."
        ).strip()

        return self._enqueue_email(
            db,
            user_id=user_id,
            type=NotificationType.PAYMENT_FAIL,
            subject=subject,
            text=text,
            dedupe=dedupe,
        )

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
        payload = _payload_dump({"subject": subject, "text": text, "attempt": 0})
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

            print(
                f"[notification] TEMPFAIL id={n.id} user_id={n.user_id} "
                f"type={n.type} channel={n.channel} attempt={attempt+1} "
                f"retry_at={retry_at.isoformat()} err={repr(e)}"
            )
            return SendResult(False, n.id, n.status, f"Temporary failure: {e}. Rescheduled.", retry_at)

        except NotificationSendPermanentError as e:
            print(
                f"[notification] PERMFAIL id={n.id} user_id={n.user_id} "
                f"type={n.type} channel={n.channel} err={repr(e)}"
            )
            n.mark_failed()
            n.scheduled_at = None
            return SendResult(False, n.id, n.status, f"Permanent failure: {e}", None)

        except Exception as e:
            print(
                f"[notification] FATAL id={n.id} user_id={n.user_id} "
                f"type={n.type} channel={n.channel} err={repr(e)}"
            )
            n.mark_failed()
            n.scheduled_at = None
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

        user: Optional[str] = os.getenv("SMTP_USER")
        password: Optional[str] = os.getenv("SMTP_PASSWORD")

        from_email = os.getenv("SMTP_FROM") or user
        from_name = os.getenv("SMTP_FROM_NAME", "Subscription App")
        reply_to = os.getenv("SMTP_REPLY_TO") or from_email

        use_ssl = os.getenv("SMTP_USE_SSL", "0") == "1"
        use_starttls = os.getenv("SMTP_STARTTLS", "1") == "1"

        if not host or not from_email:
            print(f"[DEV EMAIL - SMTP not configured] to={to_email} subject={subject}\n{text}\n---")
            return

        msg = EmailMessage()
        msg["From"] = f"{from_name} <{from_email}>" if from_name else from_email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=False)
        msg["Message-ID"] = make_msgid(domain=(from_email.split("@")[-1] if "@" in from_email else None))
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(text, subtype="plain", charset="utf-8")

        context = ssl.create_default_context()

        def _raise_by_smtp_code(e: smtplib.SMTPResponseException) -> None:
            code = int(getattr(e, "smtp_code", 0) or 0)
            err = getattr(e, "smtp_error", b"")
            if isinstance(err, (bytes, bytearray)):
                err = err.decode("utf-8", errors="replace")
            full = f"{code} {err}"

            if 400 <= code < 500:
                raise NotificationSendTemporaryError(full)
            if 500 <= code < 600:
                raise NotificationSendPermanentError(full)
            raise NotificationSendTemporaryError(full)

        try:
            if use_ssl:
                with smtplib.SMTP_SSL(host, port, timeout=20, context=context) as server:
                    server.ehlo()
                    if user and password:
                        server.login(user, password)
                    server.send_message(msg)
            else:
                with smtplib.SMTP(host, port, timeout=20) as server:
                    server.ehlo()
                    if use_starttls:
                        server.starttls(context=context)
                        server.ehlo()
                    if user and password:
                        server.login(user, password)
                    server.send_message(msg)

        except (smtplib.SMTPServerDisconnected, smtplib.SMTPConnectError, TimeoutError, OSError) as e:
            raise NotificationSendTemporaryError(str(e))

        except smtplib.SMTPResponseException as e:
            _raise_by_smtp_code(e)

        except smtplib.SMTPException as e:
            raise NotificationSendTemporaryError(str(e))

    def _fmt_when(self, when: datetime | None) -> str:
        dt = when or now_utc()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    def _tpl_subscription_created(self, *, plan_name: str | None, when: datetime | None) -> tuple[str, str]:
        plan = plan_name or "ваш тариф"
        subject = "Подписка оформлена"
        text = (
            "Здравствуйте!\n\n"
            "Подписка успешно оформлена.\n"
            f"Тариф: {plan}\n"
            f"Дата: {self._fmt_when(when)}\n\n"
            "Это автоматическое уведомление."
        )
        return subject, text

    def _tpl_subscription_renewed(self, *, plan_name: str | None, when: datetime | None) -> tuple[str, str]:
        plan = plan_name or "ваш тариф"
        subject = "Подписка продлена"
        text = (
            "Здравствуйте!\n\n"
            "Подписка успешно продлена.\n"
            f"Тариф: {plan}\n"
            f"Дата: {self._fmt_when(when)}\n\n"
            "Это автоматическое уведомление."
        )
        return subject, text

    def _tpl_subscription_canceled(self, *, plan_name: str | None, when: datetime | None) -> tuple[str, str]:
        plan = plan_name or "ваш тариф"
        subject = "Подписка отменена"
        text = (
            "Здравствуйте!\n\n"
            "Подписка отменена.\n"
            f"Тариф: {plan}\n"
            f"Дата: {self._fmt_when(when)}\n\n"
            "Это автоматическое уведомление."
        )
        return subject, text

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
            "Здравствуйте!\n\n"
            f"Мы попытались списать оплату по вашей подписке ({when_s}).\n"
            f"{plan_s}\n"
            f"{reason_s}\n"
            f"{retry_s}\n\n"
            "Проверьте способ оплаты и попробуйте снова.\n"
            "Это автоматическое уведомление."
        ).strip()
        return subject, text
