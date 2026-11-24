import os
from datetime import timedelta

IS_PRODUCTION = os.environ.get('FLASK_ENV') == 'production' or os.environ.get('RENDER') is not None


def resolve_secret_key():
    secret_key = os.environ.get('SECRET_KEY')
    if not secret_key:
        if IS_PRODUCTION:
            raise RuntimeError(
                "SECRET_KEY environment variable is not set. Refusing to start in "
                "production with an insecure fallback key. Set SECRET_KEY to a long "
                "random value (e.g. `python -c \"import secrets; print(secrets.token_hex(32))\"`)."
            )
        print("--- WARNING: SECRET_KEY is not set. Using an insecure development-only "
              "fallback. This is only acceptable for local dev. ---")
    return secret_key or 'dev-secret-key-change-this-in-production'


class Config:
    SECRET_KEY = resolve_secret_key()
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL', 'sqlite:///quiz_saas.db')
    if SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace("postgres://", "postgresql://", 1)
    SQLALCHEMY_ENGINE_OPTIONS = {
        "pool_pre_ping": True,
        "pool_recycle": 280,
        "pool_size": 5,
        "max_overflow": 5,
        "connect_args": (
            {"connect_timeout": 10, "prepare_threshold": None}
            if "+psycopg://" in SQLALCHEMY_DATABASE_URI
            else {"connect_timeout": 10} if SQLALCHEMY_DATABASE_URI.startswith("postgresql")
            else {}
        ),
    }
    UPLOAD_FOLDER = 'uploads'
    MAX_CONTENT_LENGTH = 60 * 1024 * 1024
    REMEMBER_COOKIE_DURATION = timedelta(days=30)
    PERMANENT_SESSION_LIFETIME = timedelta(days=30)
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE = IS_PRODUCTION
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SAMESITE = 'Lax'
    REMEMBER_COOKIE_SECURE = IS_PRODUCTION
    RATELIMIT_STORAGE_URI = os.environ.get('RATELIMIT_STORAGE_URI', 'memory://')

    TERMS_VERSION = os.environ.get('TERMS_VERSION', '2026-11-draft-1')
    PRIVACY_VERSION = os.environ.get('PRIVACY_VERSION', '2026-11-draft-1')
    PARENT_CONSENT_MAX_AGE_SECONDS = int(os.environ.get('PARENT_CONSENT_MAX_AGE_SECONDS', str(7 * 24 * 3600)))
    UNVERIFIED_ACCOUNT_DAYS = int(os.environ.get('UNVERIFIED_ACCOUNT_DAYS', '7'))
    INACTIVE_ACCOUNT_DAYS = int(os.environ.get('INACTIVE_ACCOUNT_DAYS', '730'))
    GEMINI_PAID_TIER = os.environ.get('GEMINI_PAID_TIER', 'false').lower() == 'true'
    PRIVACY_CONTACT_EMAIL = os.environ.get('PRIVACY_CONTACT_EMAIL', '')

    LEGAL_ENTITY_NAME = os.environ.get('LEGAL_ENTITY_NAME', '')
    LEGAL_ADDRESS = os.environ.get('LEGAL_ADDRESS', '')
    SUPPORT_EMAIL = os.environ.get('SUPPORT_EMAIL', '')
    GRIEVANCE_OFFICER_NAME = os.environ.get('GRIEVANCE_OFFICER_NAME', '')
    GRIEVANCE_OFFICER_EMAIL = os.environ.get('GRIEVANCE_OFFICER_EMAIL', '')
    LEGAL_REPLY_TIME = os.environ.get('LEGAL_REPLY_TIME', '')
    LEGAL_GOVERNING_LAW = os.environ.get('LEGAL_GOVERNING_LAW', '')
    LEGAL_COURT_PLACE = os.environ.get('LEGAL_COURT_PLACE', '')
    LEGAL_EFFECTIVE_DATE = os.environ.get('LEGAL_EFFECTIVE_DATE', '')
    LEGAL_REVIEWED_BY_LAWYER = os.environ.get('LEGAL_REVIEWED_BY_LAWYER', 'false').lower() == 'true'

    RAZORPAY_KEY_ID = os.environ.get('RAZORPAY_KEY_ID', '')
    RAZORPAY_KEY_SECRET = os.environ.get('RAZORPAY_KEY_SECRET', '')
    RAZORPAY_WEBHOOK_SECRET = os.environ.get('RAZORPAY_WEBHOOK_SECRET', '')
    RAZORPAY_PLAN_PRO = os.environ.get('RAZORPAY_PLAN_PRO', '')
    RAZORPAY_PLAN_MAX = os.environ.get('RAZORPAY_PLAN_MAX', '')
    BILLING_ENABLED = os.environ.get('BILLING_ENABLED', 'false').lower() == 'true'
    FREE_CREDITS = int(os.environ.get('FREE_CREDITS', '10'))
    PRO_PRICE_INR = int(os.environ.get('PRO_PRICE_INR', '399'))
    PRO_CREDITS = int(os.environ.get('PRO_CREDITS', '200'))
    MAX_PRICE_INR = int(os.environ.get('MAX_PRICE_INR', '799'))
    MAX_CREDITS = int(os.environ.get('MAX_CREDITS', '500'))
    RENEWAL_REMINDER_DAYS = int(os.environ.get('RENEWAL_REMINDER_DAYS', '3'))