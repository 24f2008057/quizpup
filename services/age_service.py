from flask import current_app, url_for
from itsdangerous import BadSignature, URLSafeTimedSerializer

from extensions import db
from models import User
from services.email_service import send_parent_confirmed_email, send_parent_consent_email
from services.security_utils import is_valid_email, normalize_email

AGE_GROUPS = ("adult", "minor")


def parse_age_form(form, own_email=None):
    age_group = (form.get("age_group") or "").strip()
    if age_group not in AGE_GROUPS:
        return None, None, "Please tell us whether you are 18 or older."
    if age_group == "adult":
        return "adult", None, None
    parent_email = normalize_email(form.get("parent_email"))
    if not is_valid_email(parent_email):
        return None, None, "Please enter a valid parent or guardian email address."
    if own_email and parent_email == normalize_email(own_email):
        return None, None, "The parent or guardian email must be different from your own."
    return "minor", parent_email, None


def _serializer(salt):
    return URLSafeTimedSerializer(current_app.config["SECRET_KEY"], salt=salt)


def _user_from_token(salt, token, max_age):
    try:
        data = _serializer(salt).loads(token, max_age=max_age)
    except BadSignature:
        return None
    if not isinstance(data, dict):
        return None
    user = db.session.get(User, data.get("uid"))
    if user is None or user.age_group != "minor" or user.parent_email != data.get("pe"):
        return None
    return user


def make_parent_token(user):
    return _serializer("parent-consent").dumps({"uid": user.id, "pe": user.parent_email})


def read_parent_token(token):
    return _user_from_token("parent-consent", token, current_app.config["PARENT_CONSENT_MAX_AGE_SECONDS"])


def make_parent_manage_token(user):
    return _serializer("parent-manage").dumps({"uid": user.id, "pe": user.parent_email})


def read_parent_manage_token(token):
    return _user_from_token("parent-manage", token, current_app.config["PARENT_MANAGE_MAX_AGE_SECONDS"])


def send_parent_request(user):
    link = url_for("privacy.parent_confirm", token=make_parent_token(user), _external=True)
    return send_parent_consent_email(user.parent_email, user.first_name, link)


def parent_manage_url(user):
    return url_for("privacy.parent_manage", token=make_parent_manage_token(user), _external=True)


def send_parent_confirmed(user):
    return send_parent_confirmed_email(user.parent_email, user.first_name, parent_manage_url(user))