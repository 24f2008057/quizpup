from datetime import datetime

from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import CreditLedger, Subscription, User
from services import plans


class InsufficientCredits(Exception):
    def __init__(self, needed, available):
        super().__init__("insufficient credits")
        self.needed = needed
        self.available = available


def effective_plan(user, now=None):
    now = now or datetime.utcnow()
    sub = Subscription.query.filter_by(user_id=user.id).first()
    if sub and sub.plan in ("pro", "max") and sub.current_period_end and sub.status != "refunded":
        if now < sub.current_period_end:
            return sub.plan, sub
    return "free", sub


def _period_key(plan, sub, now):
    if plan == "free":
        return f"free-{now.strftime('%Y-%m')}"
    start = sub.current_period_start or now
    return f"sub-{sub.id}-{int(start.timestamp())}"


def _balance(user_id):
    total = db.session.query(func.coalesce(func.sum(CreditLedger.delta), 0)).filter(
        CreditLedger.user_id == user_id
    ).scalar()
    return int(total)


def _has_grant(user_id, key):
    return db.session.query(CreditLedger.id).filter_by(
        user_id=user_id, reason="grant", period_key=key
    ).first() is not None


def peek_balance(user, now=None):
    now = now or datetime.utcnow()
    plan, sub = effective_plan(user, now)
    key = _period_key(plan, sub, now)
    if _has_grant(user.id, key):
        return _balance(user.id)
    return plans.plan_credits(plan)


def ensure_period(user, now=None):
    now = now or datetime.utcnow()
    plan, sub = effective_plan(user, now)
    key = _period_key(plan, sub, now)
    if _has_grant(user.id, key):
        return _balance(user.id)
    leftover = _balance(user.id)
    try:
        if leftover:
            db.session.add(CreditLedger(user_id=user.id, delta=-leftover, reason="expire", period_key=key))
        db.session.add(CreditLedger(
            user_id=user.id, delta=plans.plan_credits(plan), reason="grant", period_key=key
        ))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
    return _balance(user.id)


def charge(user, amount, ref):
    if amount <= 0:
        return _balance(user.id)
    db.session.query(User).filter_by(id=user.id).with_for_update().first()
    available = ensure_period(user)
    db.session.query(User).filter_by(id=user.id).with_for_update().first()
    if available < amount:
        db.session.rollback()
        raise InsufficientCredits(amount, available)
    db.session.add(CreditLedger(user_id=user.id, delta=-amount, reason="charge", ref=ref))
    db.session.commit()
    return available - amount


def refund_charge(user_id, ref):
    if not ref:
        return False
    spent = db.session.query(func.coalesce(func.sum(CreditLedger.delta), 0)).filter_by(
        user_id=user_id, reason="charge", ref=ref
    ).scalar()
    if not spent:
        return False
    already = db.session.query(CreditLedger.id).filter_by(
        user_id=user_id, reason="refund", ref=ref
    ).first()
    if already:
        return False
    db.session.add(CreditLedger(user_id=user_id, delta=-int(spent), reason="refund", ref=ref))
    db.session.commit()
    return True


def next_reset(user, now=None):
    now = now or datetime.utcnow()
    plan, sub = effective_plan(user, now)
    if plan == "free":
        year = now.year + (1 if now.month == 12 else 0)
        month = 1 if now.month == 12 else now.month + 1
        return datetime(year, month, 1)
    return sub.current_period_end