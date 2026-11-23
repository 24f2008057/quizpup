import json
import logging

import click
from flask import current_app, jsonify, request
from flask_login import current_user, login_required

from extensions import csrf, db, limiter
from models import Payment, PaymentEvent, Subscription
from services import billing_service, credits, plans, razorpay_service
from services.billing_service import BillingError

from . import billing_bp

logger = logging.getLogger("quizpup")


@billing_bp.app_context_processor
def inject_credit_summary():
    if not current_user.is_authenticated or request.endpoint in (None, 'static'):
        return {}
    try:
        return {"credit_summary": credits.peek_balance(current_user)}
    except Exception:
        db.session.rollback()
        return {}


def _error(exc):
    return jsonify({"success": False, "error": exc.message}), exc.status


def page_context(user):
    credits.ensure_period(user)
    plan, sub = credits.effective_plan(user)
    table = plans.plan_table()
    renews = bool(sub and plan != "free" and sub.billing == "recurring" and not sub.cancel_at_period_end and sub.status == "active")
    offers = []
    for key in ("pro", "max"):
        offers.append({
            "key": key,
            "name": table[key]["name"],
            "credits": table[key]["credits"],
            "monthly": table[key]["price_inr"],
            "pass3": plans.pass_paise(key, 3) // 100,
            "pass6": plans.pass_paise(key, 6) // 100,
        })
    return {
        "billing_ready": billing_service.billing_ready(),
        "plan_key": plan,
        "plan_name": plans.display_name(plan),
        "sub": sub if plan != "free" else None,
        "renews": renews,
        "credits_left": credits.peek_balance(user),
        "next_reset": credits.next_reset(user),
        "free_credits": table["free"]["credits"],
        "offers": offers,
        "is_adult": user.age_group == "adult",
        "pass_days": {"pass3": 3 * plans.PASS_DAYS_PER_MONTH, "pass6": 6 * plans.PASS_DAYS_PER_MONTH},
    }


@billing_bp.route('/billing/checkout', methods=['POST'])
@login_required
@limiter.limit("10 per hour")
def checkout():
    data = request.get_json(silent=True) or {}
    try:
        payload = billing_service.start_checkout(current_user, data.get('plan'), data.get('billing'))
    except BillingError as exc:
        return _error(exc)
    return jsonify({"success": True, **payload})


@billing_bp.route('/billing/verify', methods=['POST'])
@login_required
@limiter.limit("30 per hour")
def verify():
    data = request.get_json(silent=True) or {}
    try:
        if data.get('razorpay_subscription_id'):
            billing_service.verify_subscription_payment(
                current_user, data['razorpay_subscription_id'],
                data.get('razorpay_payment_id', ''), data.get('razorpay_signature', ''))
        elif data.get('razorpay_order_id'):
            billing_service.verify_order_payment(
                current_user, data['razorpay_order_id'],
                data.get('razorpay_payment_id', ''), data.get('razorpay_signature', ''))
        else:
            raise BillingError("Payment could not be verified.", 400)
    except BillingError as exc:
        return _error(exc)
    plan, _ = credits.effective_plan(current_user)
    return jsonify({"success": True, "active": plan != "free"})


@billing_bp.route('/billing/status')
@login_required
def status():
    plan, _ = credits.effective_plan(current_user)
    return jsonify({"plan": plan, "active": plan != "free"})


@billing_bp.route('/billing/cancel', methods=['POST'])
@login_required
@limiter.limit("10 per hour")
def cancel():
    try:
        sub = billing_service.request_cancel(current_user)
    except BillingError as exc:
        return _error(exc)
    return jsonify({
        "success": True,
        "ends_on": sub.current_period_end.strftime("%d %B %Y") if sub.current_period_end else None,
    })


@billing_bp.route('/billing/webhook', methods=['POST'])
@csrf.exempt
@limiter.exempt
def webhook():
    raw = request.get_data()
    signature = request.headers.get('X-Razorpay-Signature', '')
    event_id = request.headers.get('X-Razorpay-Event-Id', '')
    if not event_id or not razorpay_service.verify_webhook_signature(raw, signature):
        return jsonify({"status": "invalid"}), 400
    try:
        event = json.loads(raw)
    except ValueError:
        return jsonify({"status": "invalid"}), 400
    try:
        outcome = billing_service.handle_webhook(event_id, event)
    except Exception:
        db.session.rollback()
        PaymentEvent.query.filter_by(event_id=event_id).delete()
        db.session.commit()
        logger.exception("Webhook processing failed")
        return jsonify({"status": "error"}), 500
    return jsonify({"status": outcome}), 200


BILLING_REQUIRED_SETTINGS = (
    'RAZORPAY_KEY_ID', 'RAZORPAY_KEY_SECRET', 'RAZORPAY_WEBHOOK_SECRET',
    'RAZORPAY_PLAN_PRO', 'RAZORPAY_PLAN_MAX',
)


def register_billing_cli(app):
    @app.cli.command('billing-check')
    def billing_check_command():
        missing = [name for name in BILLING_REQUIRED_SETTINGS if not app.config.get(name)]
        for name in missing:
            click.echo(f'missing: {name}')
        if not app.config.get('BILLING_ENABLED'):
            click.echo('BILLING_ENABLED is not true')
        key = app.config.get('RAZORPAY_KEY_ID', '')
        if key:
            click.echo('mode: ' + ('test' if key.startswith('rzp_test_') else 'LIVE'))
        if missing or not app.config.get('BILLING_ENABLED'):
            raise SystemExit(1)
        click.echo('billing settings complete')

    @app.cli.command('send-renewal-reminders')
    def send_renewal_reminders_command():
        click.echo(f'sent {billing_service.send_renewal_reminders()} reminders')

    @app.cli.command('refund-payment')
    @click.argument('provider_payment_id')
    @click.option('--amount-inr', type=int, default=None)
    @click.option('--reason', default='requested by user')
    def refund_payment_command(provider_payment_id, amount_inr, reason):
        payment = Payment.query.filter_by(provider_payment_id=provider_payment_id).first()
        if payment is None:
            click.echo('payment not found')
            raise SystemExit(1)
        try:
            billing_service.refund_payment(payment, amount_inr * 100 if amount_inr else None, reason)
        except BillingError as exc:
            click.echo(exc.message)
            raise SystemExit(1)
        click.echo('refund requested; access changes when the provider confirms it')