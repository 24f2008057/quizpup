import os
import html
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


def send_parent_consent_email(parent_email, child_first_name, link):
    name = html.escape(child_first_name or "A student")
    safe_link = html.escape(link, quote=True)
    return send_email(
        parent_email,
        "Please confirm your child's QuizPup account",
        f"<p>Hello,</p>"
        f"<p>{name} has signed up for QuizPup, an exam preparation app, and gave this address as their parent or guardian. "
        f"Because they are under 18, they cannot use QuizPup until a parent or guardian confirms.</p>"
        f"<p><a href=\"{safe_link}\">Review and confirm</a></p>"
        f"<p>This link expires in 7 days. If you do not know this person or do not agree, ignore this email and the account will stay locked.</p>"
    )


def send_parent_confirmed_email(parent_email, child_first_name, manage_link):
    name = html.escape(child_first_name or "Your child")
    safe_link = html.escape(manage_link, quote=True)
    return send_email(
        parent_email,
        "You confirmed your child's QuizPup account",
        f"<p>Hello,</p>"
        f"<p>Thank you. {name}'s QuizPup account is now confirmed.</p>"
        f"<p>You can withdraw your consent or ask us to delete the account and its data at any time here:</p>"
        f"<p><a href=\"{safe_link}\">Manage consent</a></p>"
        f"<p>Keep this email. The link works for one year.</p>"
    )