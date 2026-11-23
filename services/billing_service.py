import html
import logging
from datetime import datetime, timedelta

from flask import current_app
from sqlalchemy.exc import IntegrityError

from extensions import db
from models import Payment, PaymentEvent, Subscription, User
from services import credits, plans, razorpay_service
from services.razorpay_service import RazorpayError

logger = logging.getLogger("quizpup")

SUBSCRIPTION_EVENTS = {
    "subscription.authenticated", "subscription.activated", "subscription.charged",
    "subscription.pending", "subscription.halted", "subscription.cancelled",
    "subscription.completed", "subscription.paused", "subscription.resumed",
    "subscription.updated",
}
RECURRING_TOTAL_COUNT = 60


class BillingError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


def _from_ts(value):
    if not value:
        return None
    return datetime.utcfromtimestamp(int(value))


def sync_user_label(user):
    plan, _ = credits.effective_plan(user)
    user.subscription_status = "Free Tier" if plan == "free" else plans.display_name(plan)
    db.session.commit()


def billing_ready():
    return razorpay_service.configured()


def _require_ready():
    if not billing_ready():
        raise BillingError("Payments are not open yet.", 503)


def _require_adult(user):
    if user.age_group != "adult":
        raise BillingError("Paid plans are for adults. Please ask a parent or guardian.", 403)


def _require_no_active_plan(user):
    plan, _ = credits.effective_plan(user)
    if plan != "free":
        raise BillingError("You already have an active plan. You can buy again when it ends.", 409)


def start_checkout(user, plan, billing):
    _require_ready()
    _require_adult(user)
    if plan not in ("pro", "max"):
        raise BillingError("Unknown plan.")
    _require_no_active_plan(user)
    cfg = current_app.config
    prefill = {"name": f"{user.first_name} {user.last_name}".strip(), "email": user.email}
    if billing == "monthly":
        plan_id = razorpay_service.plan_id_for(plan)
        if not plan_id:
            raise BillingError("This plan is not available yet.", 503)
        try:
            remote_plan = razorpay_service.fetch_plan(plan_id)
            if int(remote_plan.get("item", {}).get("amount", -1)) != plans.monthly_paise(plan):
                logger.error("Razorpay plan amount does not match configured price for %s", plan)
                raise BillingError("This plan is not available right now.", 503)
            remote = razorpay_service.create_subscription(
                plan_id, RECURRING_TOTAL_COUNT, {"user_id": str(user.id), "plan": plan}
            )
        except RazorpayError as exc:
            raise BillingError(str(exc), 502)
        sub = Subscription.query.filter_by(user_id=user.id).first()
        if sub is None:
            sub = Subscription(user_id=user.id, plan=plan, billing="recurring")
            db.session.add(sub)
        sub.plan = plan
        sub.billing = "recurring"
        sub.status = "created"
        sub.provider_subscription_id = remote["id"]
        sub.cancel_at_period_end = False
        db.session.commit()
        return {
            "kind": "subscription", "key_id": cfg["RAZORPAY_KEY_ID"],
            "subscription_id": remote["id"], "prefill": prefill,
            "description": f"QuizPup {plans.display_name(plan)}, renews monthly",
        }
    months = plans.PASS_MONTHS.get(billing)
    if not months:
        raise BillingError("Unknown billing option.")
    amount = plans.pass_paise(plan, months)
    try:
        order = razorpay_service.create_order(
            amount, f"qp-{user.id}-{int(datetime.utcnow().timestamp())}",
            {"user_id": str(user.id), "plan": plan, "months": str(months)},
        )
    except RazorpayError as exc:
        raise BillingError(str(exc), 502)
    db.session.add(Payment(
        user_id=user.id, provider_order_id=order["id"], plan=plan, billing="pass",
        months=months, amount_paise=amount, status="created",
    ))
    db.session.commit()
    return {
        "kind": "order", "key_id": cfg["RAZORPAY_KEY_ID"], "order_id": order["id"],
        "amount": amount, "prefill": prefill,
        "description": f"QuizPup {plans.display_name(plan)}, {months * plans.PASS_DAYS_PER_MONTH}-day pass, no auto-renewal",
    }


def apply_subscription_entity(sub, entity):
    status = entity.get("status") or sub.status
    sub.status = status
    start = _from_ts(entity.get("current_start"))
    end = _from_ts(entity.get("current_end"))
    if status in ("active", "authenticated", "pending", "halted", "cancelled", "completed") and end:
        sub.current_period_start = start or sub.current_period_start
        sub.current_period_end = end
    db.session.commit()
    user = db.session.get(User, sub.user_id)
    if user:
        credits.ensure_period(user)
        sync_user_label(user)


def _record_subscription_payment(sub, payment_entity):
    payment_id = payment_entity.get("id")
    if not payment_id or Payment.query.filter_by(provider_payment_id=payment_id).first():
        return
    db.session.add(Payment(
        user_id=sub.user_id, provider_payment_id=payment_id,
        provider_subscription_id=sub.provider_subscription_id, plan=sub.plan, billing="recurring",
        months=1, amount_paise=int(payment_entity.get("amount", 0)),
        currency=payment_entity.get("currency", "INR"), status="captured",
    ))
    db.session.commit()


def verify_subscription_payment(user, subscription_id, payment_id, signature):
    if not razorpay_service.verify_checkout_signature(subscription_id, payment_id, signature, subscription=True):
        raise BillingError("Payment could not be verified.", 400)
    sub = Subscription.query.filter_by(user_id=user.id, provider_subscription_id=subscription_id).first()
    if sub is None:
        raise BillingError("Payment could not be verified.", 400)
    try:
        entity = razorpay_service.fetch_subscription(subscription_id)
    except RazorpayError as exc:
        raise BillingError(str(exc), 502)
    apply_subscription_entity(sub, entity)
    return sub


def activate_pass(payment, provider_payment_id):
    if payment.status == "captured":
        return False
    now = datetime.utcnow()
    payment.provider_payment_id = provider_payment_id
    payment.status = "captured"
    sub = Subscription.query.filter_by(user_id=payment.user_id).first()
    if sub is None:
        sub = Subscription(user_id=payment.user_id, plan=payment.plan, billing="pass")
        db.session.add(sub)
    sub.plan = payment.plan
    sub.billing = "pass"
    sub.status = "active"
    sub.provider_subscription_id = None
    sub.cancel_at_period_end = False
    sub.current_period_start = now
    sub.current_period_end = now + timedelta(days=payment.months * plans.PASS_DAYS_PER_MONTH)
    db.session.commit()
    user = db.session.get(User, payment.user_id)
    if user:
        credits.ensure_period(user, now)
        sync_user_label(user)
    return True


def verify_order_payment(user, order_id, payment_id, signature):
    if not razorpay_service.verify_checkout_signature(order_id, payment_id, signature):
        raise BillingError("Payment could not be verified.", 400)
    payment = Payment.query.filter_by(provider_order_id=order_id, user_id=user.id).first()
    if payment is None:
        raise BillingError("Payment could not be verified.", 400)
    try:
        remote = razorpay_service.fetch_payment(payment_id)
    except RazorpayError as exc:
        raise BillingError(str(exc), 502)
    if remote.get("order_id") != order_id or int(remote.get("amount", -1)) != payment.amount_paise:
        logger.error("Payment verification mismatch for order %s", order_id)
        raise BillingError("Payment could not be verified.", 400)
    if remote.get("status") != "captured":
        return payment, False
    activate_pass(payment, payment_id)
    return payment, True


def request_cancel(user):
    plan, sub = credits.effective_plan(user)
    if sub is None or plan == "free":
        raise BillingError("You have no active plan to cancel.", 409)
    if sub.billing == "pass":
        raise BillingError("Your pass does not renew, so there is nothing to cancel. It ends on its date.", 409)
    if sub.cancel_at_period_end:
        return sub
    _require_ready()
    try:
        razorpay_service.cancel_subscription(sub.provider_subscription_id, at_cycle_end=True)
    except RazorpayError as exc:
        raise BillingError(f"We could not cancel just now: {exc} Please try again or contact us.", 502)
    sub.cancel_at_period_end = True
    db.session.commit()
    return sub


def _handle_refund(event):
    entity = event["payload"]["refund"]["entity"]
    payment = Payment.query.filter_by(provider_payment_id=entity.get("payment_id")).first()
    if payment is None:
        return
    payment.refunded_paise = min(payment.amount_paise, payment.refunded_paise + int(entity.get("amount", 0)))
    full = payment.refunded_paise >= payment.amount_paise
    payment.status = "refunded" if full else "partially_refunded"
    db.session.commit()
    if not full or payment.user_id is None:
        return
    sub = Subscription.query.filter_by(user_id=payment.user_id).first()
    if sub is None:
        return
    sub.status = "refunded"
    sub.current_period_end = datetime.utcnow()
    db.session.commit()
    if sub.provider_subscription_id and sub.billing == "recurring":
        try:
            razorpay_service.cancel_subscription(sub.provider_subscription_id, at_cycle_end=False)
        except RazorpayError:
            logger.warning("Could not cancel provider subscription after a refund")
    user = db.session.get(User, payment.user_id)
    if user:
        sync_user_label(user)


def handle_webhook(event_id, event):
    try:
        db.session.add(PaymentEvent(event_id=event_id, event_type=str(event.get("event", ""))[:60]))
        db.session.commit()
    except IntegrityError:
        db.session.rollback()
        return "duplicate"
    kind = event.get("event")
    body = event.get("payload", {})
    if kind in SUBSCRIPTION_EVENTS:
        entity = body.get("subscription", {}).get("entity", {})
        sub = Subscription.query.filter_by(provider_subscription_id=entity.get("id")).first()
        if sub is None:
            logger.warning("Webhook for an unknown subscription ignored")
            return "ignored"
        apply_subscription_entity(sub, entity)
        payment_entity = body.get("payment", {}).get("entity")
        if kind == "subscription.charged" and payment_entity:
            _record_subscription_payment(sub, payment_entity)
        return "ok"
    if kind in ("payment.captured", "order.paid"):
        entity = body.get("payment", {}).get("entity", {})
        order_id = entity.get("order_id")
        payment = Payment.query.filter_by(provider_order_id=order_id).first() if order_id else None
        if payment and int(entity.get("amount", -1)) == payment.amount_paise:
            activate_pass(payment, entity.get("id"))
            return "ok"
        return "ignored"
    if kind in ("refund.processed", "refund.created"):
        _handle_refund(event)
        return "ok"
    if kind == "payment.failed":
        logger.info("A payment failed at the provider")
        return "ok"
    return "ignored"


def refund_payment(payment, amount_paise=None, reason="requested"):
    if payment.provider_payment_id is None or payment.status not in ("captured", "partially_refunded"):
        raise BillingError("That payment cannot be refunded.", 409)
    remaining = payment.amount_paise - payment.refunded_paise
    amount = amount_paise or remaining
    if amount <= 0 or amount > remaining:
        raise BillingError("Refund amount is not valid.", 400)
    try:
        return razorpay_service.refund_payment(payment.provider_payment_id, amount, {"reason": reason[:200]})
    except RazorpayError as exc:
        raise BillingError(str(exc), 502)


def _inr(paise):
    return f"₹{paise // 100:,}"


def send_renewal_reminders(now=None, sender=None):
    from services.email_service import send_email
    sender = sender or send_email
    now = now or datetime.utcnow()
    horizon = now + timedelta(days=current_app.config["RENEWAL_REMINDER_DAYS"])
    sent = 0
    rows = Subscription.query.filter(
        Subscription.plan.in_(("pro", "max")),
        Subscription.current_period_end > now,
        Subscription.current_period_end <= horizon,
    ).all()
    for sub in rows:
        if sub.reminder_sent_for == sub.current_period_end or sub.status == "refunded":
            continue
        user = db.session.get(User, sub.user_id)
        if user is None:
            continue
        name = plans.display_name(sub.plan)
        end = sub.current_period_end.strftime("%d %B %Y")
        renews = sub.billing == "recurring" and not sub.cancel_at_period_end and sub.status == "active"
        if renews:
            subject = f"Your QuizPup {name} plan renews on {end}"
            line = (f"Your {name} plan renews on {end} for {_inr(plans.monthly_paise(sub.plan))}. "
                    "To stop it, cancel any time on the Premium page before then.")
        else:
            subject = f"Your QuizPup {name} plan ends on {end}"
            line = f"Your {name} plan ends on {end} and will not renew. You can buy again from the Premium page."
        body = f"<p>Hi {html.escape(user.first_name)},</p><p>{html.escape(line)}</p>"
        if sender(user.email, subject, body):
            sub.reminder_sent_for = sub.current_period_end
            db.session.commit()
            sent += 1
    return sent