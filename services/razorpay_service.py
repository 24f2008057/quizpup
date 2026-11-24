import hashlib
import hmac
import logging

import requests
from flask import current_app

API = "https://api.razorpay.com/v1"
TIMEOUT = 15

logger = logging.getLogger("quizpup")


class RazorpayError(Exception):
    pass


def configured():
    cfg = current_app.config
    return bool(
        cfg["BILLING_ENABLED"] and cfg["RAZORPAY_KEY_ID"] and cfg["RAZORPAY_KEY_SECRET"]
        and cfg["RAZORPAY_WEBHOOK_SECRET"] and cfg["RAZORPAY_PLAN_PRO"] and cfg["RAZORPAY_PLAN_MAX"]
    )


def _auth():
    cfg = current_app.config
    return (cfg["RAZORPAY_KEY_ID"], cfg["RAZORPAY_KEY_SECRET"])


def _request(method, path, payload=None):
    try:
        response = requests.request(
            method, f"{API}{path}", auth=_auth(), json=payload, timeout=TIMEOUT
        )
    except requests.RequestException as exc:
        logger.error("Razorpay request failed: %s", exc.__class__.__name__)
        raise RazorpayError("Could not reach the payment provider.") from exc
    if response.status_code >= 400:
        try:
            description = response.json().get("error", {}).get("description", "")
        except ValueError:
            description = ""
        logger.warning("Razorpay %s %s returned %s", method, path, response.status_code)
        raise RazorpayError(description or "The payment provider rejected the request.")
    return response.json()


def plan_id_for(plan):
    return current_app.config["RAZORPAY_PLAN_PRO" if plan == "pro" else "RAZORPAY_PLAN_MAX"]


def fetch_plan(plan_id):
    return _request("GET", f"/plans/{plan_id}")


def create_subscription(plan_id, total_count, notes):
    return _request("POST", "/subscriptions", {
        "plan_id": plan_id,
        "total_count": total_count,
        "customer_notify": True,
        "notes": notes,
    })


def fetch_subscription(subscription_id):
    return _request("GET", f"/subscriptions/{subscription_id}")


def cancel_subscription(subscription_id, at_cycle_end=True):
    return _request("POST", f"/subscriptions/{subscription_id}/cancel", {
        "cancel_at_cycle_end": 1 if at_cycle_end else 0
    })


def fetch_payment(payment_id):
    return _request("GET", f"/payments/{payment_id}")


def refund_payment(payment_id, amount_paise=None, notes=None):
    body = {"notes": notes or {}}
    if amount_paise:
        body["amount"] = amount_paise
    return _request("POST", f"/payments/{payment_id}/refund", body)


def _hmac_hex(secret, message):
    return hmac.new(secret.encode("utf-8"), message, hashlib.sha256).hexdigest()


def verify_checkout_signature(subscription_id, payment_id, signature):
    secret = current_app.config["RAZORPAY_KEY_SECRET"]
    if not secret or not signature:
        return False
    message = f"{payment_id}|{subscription_id}"
    return hmac.compare_digest(_hmac_hex(secret, message.encode("utf-8")), str(signature))


def verify_webhook_signature(raw_body, signature):
    secret = current_app.config["RAZORPAY_WEBHOOK_SECRET"]
    if not secret or not signature:
        return False
    return hmac.compare_digest(_hmac_hex(secret, raw_body), str(signature))