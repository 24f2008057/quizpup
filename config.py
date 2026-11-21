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
    UNVERIFIED_ACCOUNT_DAYS = int(os.environ.get('UNVERIFIED_ACCOUNT_DAYS', '7'))
    INACTIVE_ACCOUNT_DAYS = int(os.environ.get('INACTIVE_ACCOUNT_DAYS', '730'))
    GEMINI_PAID_TIER = os.environ.get('GEMINI_PAID_TIER', 'false').lower() == 'true'
    PRIVACY_CONTACT_EMAIL = os.environ.get('PRIVACY_CONTACT_EMAIL', '')