import re
import hmac
import secrets

ALLOWED_NOTE_EXTENSIONS = {'.pdf', '.jpg', '.jpeg', '.png', '.webp'}

PASSWORD_PATTERN = re.compile(r'^(?=.*[A-Za-z])(?=.*\d)(?=.*[^A-Za-z0-9]).{8,}$')
PASSWORD_REQUIREMENTS_MESSAGE = (
    "Password must be at least 8 characters and include at least one letter, "
    "one number, and one special character."
)


EMAIL_PATTERN = re.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')
MAX_EMAIL_LENGTH = 150


def is_valid_email(email):
    return bool(email) and len(email) <= MAX_EMAIL_LENGTH and bool(EMAIL_PATTERN.match(email))


def normalize_email(email):
    return (email or '').strip().lower()


def detect_upload_mime(filepath):
    try:
        with open(filepath, 'rb') as f:
            h = f.read(12)
    except Exception:
        return None
    if h.startswith(b'%PDF-'):
        return 'application/pdf'
    if h.startswith(b'\xff\xd8\xff'):
        return 'image/jpeg'
    if h.startswith(b'\x89PNG\r\n\x1a\n'):
        return 'image/png'
    if h.startswith(b'RIFF') and h[8:12] == b'WEBP':
        return 'image/webp'
    return None


def is_allowed_upload_content(filepath, expect_pdf):
    detected = detect_upload_mime(filepath)
    if detected is None:
        return False
    return detected == 'application/pdf' if expect_pdf else detected != 'application/pdf'


def generate_otp():
    return str(secrets.randbelow(900000) + 100000)


def is_password_strong(password):
    return bool(password) and bool(PASSWORD_PATTERN.match(password))


def otp_matches(submitted, expected):
    if not submitted or not expected:
        return False
    return hmac.compare_digest(str(submitted), str(expected))


def clear_email_change_state(user):
    user.pending_email = None
    user.email_change_otp = None
    user.email_change_otp_expires = None