import secrets
from flask import g, request
from flask_login import current_user

from config import IS_PRODUCTION
from services.supabase_client import SUPABASE_URL, SUPABASE_ANON_KEY


def register_security_hooks(app):
    @app.before_request
    def _set_csp_nonce():
        g.csp_nonce = secrets.token_urlsafe(16)

    @app.context_processor
    def _inject_csp_nonce():
        return {"csp_nonce": lambda: g.csp_nonce}

    @app.context_processor
    def _inject_supabase_config():
        return {"supabase_url": SUPABASE_URL, "supabase_anon_key": SUPABASE_ANON_KEY}

    @app.after_request
    def set_security_headers(response):
        nonce = getattr(g, 'csp_nonce', '')
        csp = (
            "default-src 'self'; "
            f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net; "
            "script-src-attr 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "font-src 'self' data: https://cdn.jsdelivr.net; "
            "img-src 'self' data: blob:; "
            f"connect-src 'self' {SUPABASE_URL or ''}; "
            "frame-ancestors 'none'; "
            "base-uri 'self'; "
            "form-action 'self'; "
            "object-src 'none'"
        )
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
        response.headers['Permissions-Policy'] = 'camera=(), microphone=(), geolocation=(), payment=()'
        response.headers['Content-Security-Policy'] = csp
        response.headers['Cross-Origin-Opener-Policy'] = 'same-origin'
        if current_user.is_authenticated and not request.path.startswith('/static/'):
            response.headers['Cache-Control'] = 'no-store'
        if IS_PRODUCTION:
            response.headers['Strict-Transport-Security'] = 'max-age=63072000; includeSubDomains; preload'
        return response