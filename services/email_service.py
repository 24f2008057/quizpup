import os
import logging
import requests

logger = logging.getLogger("quizpup")

BREVO_API_KEY = os.environ.get("BREVO_API_KEY")
BREVO_SENDER_EMAIL = os.environ.get("BREVO_SENDER_EMAIL")
MAIL_FROM_NAME = "QuizPup"

OTP_VALID_MINUTES = 10
OTP_MAX_ATTEMPTS = 5


def send_email(target_email, subject, html_body):
    if not BREVO_API_KEY or not BREVO_SENDER_EMAIL:
        logger.error("BREVO_API_KEY or BREVO_SENDER_EMAIL environment variable is missing!")
        return False

    try:
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={
                "api-key": BREVO_API_KEY,
                "Content-Type": "application/json",
                "Accept": "application/json"
            },
            json={
                "sender": {"name": MAIL_FROM_NAME, "email": BREVO_SENDER_EMAIL},
                "to": [{"email": target_email}],
                "subject": subject,
                "htmlContent": html_body
            },
            timeout=10
        )
        if response.status_code in (200, 201):
            return True
        logger.warning("Brevo rejected an email send with HTTP status %s", response.status_code)
        return False
    except Exception:
        logger.exception("Email send to the Brevo API failed")
        return False


def send_otp_email(target_email, otp_code):
    return send_email(
        target_email,
        "Quiz App Verification Code",
        f"<p>Your Quiz App verification security code is: <strong>{otp_code}</strong></p>"
    )


def send_verification_email(target_email, otp_code):
    return send_email(
        target_email,
        "Verify your QuizPup account",
        f"<p>Welcome to QuizPup! Your account verification code is: <strong>{otp_code}</strong></p>"
        f"<p>This code expires in {OTP_VALID_MINUTES} minutes.</p>"
    )