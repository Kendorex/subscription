# print_state.py
from __future__ import annotations

from sqlalchemy import select

from db import SessionLocal
from models.user import User
from models.plan import Plan
from models.subscription import Subscription
from models.invoice import Invoice
from models.transaction import Transaction
from models.notification import Notification


def hr(title: str):
    print("\n" + "=" * 80)
    print(title)
    print("=" * 80)


def print_users(db):
    hr("USERS")
    rows = db.execute(select(User)).scalars().all()
    for u in rows:
        print(f"- {u.id} | {u.email} | role={u.role}")


def print_plans(db):
    hr("PLANS")
    rows = db.execute(select(Plan)).scalars().all()
    for p in rows:
        print(f"- {p.id} | {p.name} | price={p.price_cents} | period={p.period} | active={p.is_active}")


def print_subscriptions(db):
    hr("SUBSCRIPTIONS")
    rows = db.execute(select(Subscription)).scalars().all()
    for s in rows:
        print(
            f"- {s.id} | user={s.user_id} | plan={s.plan_id} | "
            f"status={s.status} | "
            f"period=({s.current_period_start} -> {s.current_period_end})"
        )


def print_invoices(db):
    hr("INVOICES")
    rows = db.execute(select(Invoice).order_by(Invoice.id.desc())).scalars().all()
    for i in rows:
        print(
            f"- {i.id} | sub={i.subscription_id} | "
            f"status={i.status} | amount={i.amount_cents} | "
            f"attempt={i.attempt_no} | due_at={i.due_at} | paid_at={i.paid_at}"
        )


def print_transactions(db):
    hr("TRANSACTIONS")
    rows = db.execute(select(Transaction).order_by(Transaction.id.desc())).scalars().all()
    for t in rows:
        print(
            f"- {t.id} | user={t.user_id} | invoice={t.invoice_id} | "
            f"type={t.type} | status={t.status} | "
            f"amount={t.amount_cents} | provider={t.provider}"
        )


def print_notifications(db):
    hr("NOTIFICATIONS")
    rows = db.execute(select(Notification).order_by(Notification.id.desc())).scalars().all()
    for n in rows:
        print(
            f"- {n.id} | user={n.user_id} | "
            f"type={n.type} | channel={n.channel} | status={n.status} | "
            f"scheduled_at={n.scheduled_at} | sent_at={n.sent_at}"
        )


def main():
    db = SessionLocal()
    try:
        print_users(db)
        print_plans(db)
        print_subscriptions(db)
        print_invoices(db)
        print_transactions(db)
        print_notifications(db)
    finally:
        db.close()


if __name__ == "__main__":
    main()
