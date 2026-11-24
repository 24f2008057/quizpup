import hashlib
import hmac
import json
from datetime import datetime, timedelta

import pytest

from extensions import db
from models import CreditLedger, Payment, PaymentEvent, Subscription, User
from services import billing_service, credits, plans, razorpay_service
from tests.conftest import login, make_user

SECRET = "test_secret"
WEBHOOK = "whsec_test"


@pytest.fixture
def billing(app, monkeypatch):
    for key, value in dict(
        BILLING_ENABLED=True, RAZORPAY_KEY_ID="rzp_test_x", RAZORPAY_KEY_SECRET=SECRET,
        RAZORPAY_WEBHOOK_SECRET=WEBHOOK, RAZORPAY_PLAN_PRO="plan_pro", RAZORPAY_PLAN_MAX="plan_max",
    ).items():
        monkeypatch.setitem(app.config, key, value)
    calls = []

    def fake_request(method, path, payload=None):
        calls.append((method, path, payload))
        if path == "/plans/plan_pro":
            return {"item": {"amount": 39900}}
        if path == "/plans/plan_max":
            return {"item": {"amount": 79900}}
        if path == "/subscriptions":
            return {"id": "sub_1"}
        if path.startswith("/subscriptions/") and path.endswith("/cancel"):
            return {"status": "active"}
        if path.startswith("/subscriptions/"):
            now = int(datetime.utcnow().timestamp())
            return {"id": "sub_1", "status": "active", "current_start": now, "current_end": now + 30 * 86400}
        if path.startswith("/payments/") and path.endswith("/refund"):
            return {"id": "rfnd_1"}
        raise AssertionError(path)

    monkeypatch.setattr(razorpay_service, "_request", fake_request)
    return calls


def adult(email="pay@example.com"):
    user = make_user(email=email)
    user.age_group = "adult"
    db.session.commit()
    return user


def sign(message, secret=SECRET):
    return hmac.new(secret.encode(), message.encode(), hashlib.sha256).hexdigest()


def hook(client, event, event_id="evt_1", secret=WEBHOOK):
    raw = json.dumps(event).encode()
    sig = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return client.post("/billing/webhook", data=raw, headers={
        "X-Razorpay-Signature": sig, "X-Razorpay-Event-Id": event_id, "Content-Type": "application/json"})


def test_prices_and_credits_match_the_agreed_plans(app):
    with app.app_context():
        table = plans.plan_table()
        assert (table["free"]["credits"], table["pro"]["price_inr"], table["pro"]["credits"]) == (10, 399, 200)
        assert (table["max"]["price_inr"], table["max"]["credits"]) == (799, 500)
        assert plans.quiz_cost("easy", 10) == 1 and plans.quiz_cost("medium", 25) == 3
        assert plans.quiz_cost("hard", 50) == 10 and plans.quiz_cost("god mode", 100) == 40
        assert plans.notes_cost(12) == 12


def test_free_user_gets_monthly_grant_and_charge_refund_is_exact(app):
    user = adult()
    assert credits.ensure_period(user) == 10
    assert credits.charge(user, 6, "quiz-a") == 4
    with pytest.raises(credits.InsufficientCredits):
        credits.charge(user, 5, "quiz-b")
    assert credits.refund_charge(user.id, "quiz-a") is True
    assert credits.refund_charge(user.id, "quiz-a") is False
    assert credits.ensure_period(user) == 10


def test_month_change_expires_leftovers_and_regrants(app):
    user = adult()
    credits.ensure_period(user, datetime(2027, 1, 10))
    credits.charge(user, 5, "quiz-c")
    assert credits.ensure_period(user, datetime(2027, 2, 2)) == 10


def test_checkout_needs_adult_and_ready_provider(client, billing):
    make_user(email="kid@example.com")
    login(client, "kid@example.com", "Correct-Horse9!")
    resp = client.post("/billing/checkout", json={"plan": "pro", "billing": "monthly"})
    assert resp.status_code == 403


def test_monthly_checkout_verifies_plan_amount_then_activates_after_signed_payment(client, billing):
    user = adult()
    login(client, "pay@example.com", "Correct-Horse9!")
    data = client.post("/billing/checkout", json={"plan": "pro", "billing": "monthly"}).get_json()
    assert data["subscription_id"] == "sub_1" and "rzp_test_x" == data["key_id"]
    bad = client.post("/billing/verify", json={
        "razorpay_subscription_id": "sub_1", "razorpay_payment_id": "pay_1", "razorpay_signature": "nope"})
    assert bad.status_code == 400
    good = client.post("/billing/verify", json={
        "razorpay_subscription_id": "sub_1", "razorpay_payment_id": "pay_1",
        "razorpay_signature": sign("pay_1|sub_1")})
    assert good.get_json()["active"] is True
    assert db.session.get(User, user.id).subscription_status == "Pro"
    assert credits.ensure_period(db.session.get(User, user.id)) == 200


def test_plan_price_mismatch_blocks_checkout(client, billing, monkeypatch):
    adult()
    login(client, "pay@example.com", "Correct-Horse9!")
    original = razorpay_service._request
    monkeypatch.setattr(razorpay_service, "_request",
                        lambda m, p, b=None: {"item": {"amount": 100}} if p.startswith("/plans/") else original(m, p, b))
    assert client.post("/billing/checkout", json={"plan": "pro", "billing": "monthly"}).status_code == 503


def test_cannot_buy_while_a_plan_is_active(client, billing):
    user = adult()
    db.session.add(Subscription(user_id=user.id, plan="pro", billing="pass", status="active",
                                current_period_start=datetime.utcnow(),
                                current_period_end=datetime.utcnow() + timedelta(days=5)))
    db.session.commit()
    login(client, "pay@example.com", "Correct-Horse9!")
    assert client.post("/billing/checkout", json={"plan": "max", "billing": "monthly"}).status_code == 409


def test_second_checkout_that_gets_paid_is_adopted_not_lost(client, billing):
    user = adult()
    db.session.add(Subscription(user_id=user.id, plan="pro", billing="recurring", status="created",
                                provider_subscription_id="sub_old"))
    db.session.commit()
    now = int(datetime.utcnow().timestamp())
    event = {"event": "subscription.charged", "payload": {
        "subscription": {"entity": {"id": "sub_new", "status": "active", "current_start": now,
                                    "current_end": now + 2592000, "notes": {"user_id": str(user.id), "plan": "pro"}}},
        "payment": {"entity": {"id": "pay_n", "amount": 39900, "currency": "INR"}}}}
    assert hook(client, event, "evt_adopt").status_code == 200
    assert Subscription.query.filter_by(user_id=user.id).first().provider_subscription_id == "sub_new"
    assert credits.effective_plan(user)[0] == "pro"
    assert any(c[1] == "/subscriptions/sub_old/cancel" for c in billing)


def test_duplicate_paid_subscription_is_cancelled_not_stacked(client, billing):
    user = adult()
    now = datetime.utcnow()
    db.session.add(Subscription(user_id=user.id, plan="pro", billing="recurring", status="active",
                                provider_subscription_id="sub_live", current_period_start=now,
                                current_period_end=now + timedelta(days=20)))
    db.session.commit()
    ts = int(now.timestamp())
    event = {"event": "subscription.activated", "payload": {"subscription": {"entity": {
        "id": "sub_dup", "status": "active", "current_start": ts, "current_end": ts + 2592000,
        "notes": {"user_id": str(user.id), "plan": "max"}}}}}
    assert hook(client, event, "evt_dup2").get_json()["status"] == "ignored"
    assert Subscription.query.filter_by(user_id=user.id).first().provider_subscription_id == "sub_live"
    assert any(c[1] == "/subscriptions/sub_dup/cancel" and c[2] == {"cancel_at_cycle_end": 0} for c in billing)


def test_billing_stays_closed_until_webhook_secret_is_set(app, billing, monkeypatch):
    with app.app_context():
        assert razorpay_service.configured() is True
        monkeypatch.setitem(app.config, "RAZORPAY_WEBHOOK_SECRET", "")
        assert razorpay_service.configured() is False


def test_oversized_webhook_body_is_refused(client, billing):
    resp = client.post("/billing/webhook", data=b"x" * (1024 * 1024 + 1),
                       headers={"X-Razorpay-Signature": "a", "X-Razorpay-Event-Id": "e"})
    assert resp.status_code == 413


def test_cancel_keeps_access_until_period_end_and_pass_has_nothing_to_cancel(client, billing):
    user = adult()
    end = datetime.utcnow() + timedelta(days=10)
    db.session.add(Subscription(user_id=user.id, plan="max", billing="recurring", status="active",
                                provider_subscription_id="sub_1",
                                current_period_start=datetime.utcnow(), current_period_end=end))
    db.session.commit()
    login(client, "pay@example.com", "Correct-Horse9!")
    assert client.post("/billing/cancel").status_code == 200
    assert any(c[1] == "/subscriptions/sub_1/cancel" and c[2] == {"cancel_at_cycle_end": 1} for c in billing)
    assert credits.effective_plan(user)[0] == "max"
    assert Subscription.query.filter_by(user_id=user.id).first().cancel_at_period_end is True


def test_webhook_rejects_bad_signature_and_ignores_duplicates(client, billing):
    event = {"event": "payment.failed", "payload": {}}
    assert hook(client, event, secret="wrong").status_code == 400
    assert hook(client, event, "evt_dup").get_json()["status"] == "ok"
    assert hook(client, event, "evt_dup").get_json()["status"] == "duplicate"
    assert PaymentEvent.query.filter_by(event_id="evt_dup").count() == 1


def test_webhook_charge_activates_plan_and_full_refund_revokes_it(client, billing):
    user = adult()
    db.session.add(Subscription(user_id=user.id, plan="pro", billing="recurring", status="created",
                                provider_subscription_id="sub_1"))
    db.session.commit()
    now = int(datetime.utcnow().timestamp())
    charged = {"event": "subscription.charged", "payload": {
        "subscription": {"entity": {"id": "sub_1", "status": "active", "current_start": now, "current_end": now + 2592000}},
        "payment": {"entity": {"id": "pay_9", "amount": 39900, "currency": "INR"}}}}
    assert hook(client, charged, "evt_c").status_code == 200
    assert credits.effective_plan(user)[0] == "pro"
    assert Payment.query.filter_by(provider_payment_id="pay_9").first().status == "captured"
    refund = {"event": "refund.processed", "payload": {"refund": {"entity": {"payment_id": "pay_9", "amount": 39900}}}}
    assert hook(client, refund, "evt_r").status_code == 200
    assert credits.effective_plan(user)[0] == "free"
    assert db.session.get(User, user.id).subscription_status == "Free Tier"


def test_failed_quiz_generation_returns_the_credits(client, billing, monkeypatch):
    user = adult()
    login(client, "pay@example.com", "Correct-Horse9!")
    from blueprints.quiz import routes as quiz_routes
    monkeypatch.setattr(quiz_routes, "call_gemini", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
    resp = client.post("/generate-quiz", data={"topic": "physics", "difficulty": "hard", "total_questions": "10", "duration": "10"})
    assert resp.status_code >= 400
    assert credits.ensure_period(db.session.get(User, user.id)) == 10


def test_quiz_without_enough_credits_is_refused_with_upgrade_link(client, billing):
    user = adult()
    credits.ensure_period(user)
    credits.charge(user, 9, "quiz-spent")
    login(client, "pay@example.com", "Correct-Horse9!")
    resp = client.post("/generate-quiz", data={"topic": "physics", "difficulty": "hard", "total_questions": "20", "duration": "10"})
    assert resp.status_code == 402
    assert resp.get_json()["code"] == "insufficient_credits"


def test_renewal_reminder_is_sent_once(app, billing):
    user = adult()
    end = datetime.utcnow() + timedelta(days=2)
    db.session.add(Subscription(user_id=user.id, plan="pro", billing="recurring", status="active",
                                provider_subscription_id="sub_1", current_period_start=datetime.utcnow(),
                                current_period_end=end))
    db.session.commit()
    sent = []
    sender = lambda to, subject, body: sent.append((to, subject)) or True
    assert billing_service.send_renewal_reminders(sender=sender) == 1
    assert billing_service.send_renewal_reminders(sender=sender) == 0
    assert "renews on" in sent[0][1]


def test_deleting_an_account_cancels_the_subscription_and_keeps_only_anonymous_payments(client, billing):
    user = adult()
    db.session.add(Subscription(user_id=user.id, plan="pro", billing="recurring", status="active",
                                provider_subscription_id="sub_1", current_period_start=datetime.utcnow(),
                                current_period_end=datetime.utcnow() + timedelta(days=9)))
    db.session.add(Payment(user_id=user.id, provider_payment_id="pay_k", plan="pro", billing="recurring",
                           amount_paise=39900, status="captured"))
    db.session.commit()
    from services.privacy_service import delete_user_account
    ok, _ = delete_user_account(user)
    assert ok
    assert any(c[1] == "/subscriptions/sub_1/cancel" and c[2] == {"cancel_at_cycle_end": 0} for c in billing)
    assert Payment.query.filter_by(provider_payment_id="pay_k").first().user_id is None
    assert Subscription.query.count() == 0


def test_premium_page_shows_plans_credits_and_cancel_terms(client, billing):
    adult()
    login(client, "pay@example.com", "Correct-Horse9!")
    html = client.get("/account").get_data(as_text=True)
    assert "₹399" in html and "₹799" in html and "200 credits" in html and "500 credits" in html and "no renewal" not in html
    assert "every month until you cancel" in html and "once for" not in html
    assert "checkout.razorpay.com" in client.get("/account").headers["Content-Security-Policy"]
    assert "razorpay" not in client.get("/home").headers["Content-Security-Policy"]


def test_billing_check_command_lists_missing_settings(app):
    result = app.test_cli_runner().invoke(args=["billing-check"])
    assert result.exit_code == 1 and "missing: RAZORPAY_KEY_ID" in result.output