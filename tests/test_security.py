import os
import re
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import g
from werkzeug.security import generate_password_hash

from extensions import db
from models import User, QuizSession
from services.security_utils import detect_upload_mime, is_allowed_upload_content, is_valid_email, normalize_email
from tests.conftest import make_user, make_quiz_session, login

SQL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "sql")

QUESTION = {
    "question_text": "Q1", "option_1": "a", "option_2": "b", "option_3": "c", "option_4": "d",
    "correct_option": 2,
}


def _new_request_state():
    g.pop("_login_user", None)


def _logged_in_client(app, email="owner@example.com", password="Correct-Horse9!"):
    user = make_user(email=email, password=password)
    client = app.test_client()
    assert login(client, email, password).status_code == 302
    return client, user


def test_password_change_invalidates_existing_sessions(app):
    client, user = _logged_in_client(app)
    assert client.get("/home").status_code == 200

    user.password_hash = generate_password_hash("Brand-New-Pass1!", method="scrypt")
    db.session.commit()

    _new_request_state()
    response = client.get("/home")
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_legacy_plain_id_session_is_rejected(app):
    user = make_user()
    client = app.test_client()
    with client.session_transaction() as sess:
        sess["_user_id"] = str(user.id)
        sess["_fresh"] = True
    assert client.get("/home").status_code == 302


def test_registration_blocks_case_variant_duplicate_email(client):
    payload = {
        "username": "first", "password": "Correct-Horse9!", "first_name": "A",
        "last_name": "B", "email": "Victim@Example.com", "phone": "9999999999",
    }
    with patch("blueprints.auth.routes.send_verification_email", return_value=True):
        client.post("/register", data=payload)
        payload.update(username="second", email="victim@example.com")
        response = client.post("/register", data=payload)
    assert response.status_code == 302
    assert User.query.count() == 1
    assert User.query.first().email == "victim@example.com"


def test_registration_rejects_invalid_email_and_overlong_fields(client):
    base = {
        "username": "u", "password": "Correct-Horse9!", "first_name": "A",
        "last_name": "B", "email": "not-an-email", "phone": "9999999999",
    }
    client.post("/register", data=base)
    assert User.query.count() == 0
    base.update(email="ok@example.com", username="x" * 151)
    client.post("/register", data=base)
    assert User.query.count() == 0


def test_forgot_password_matches_email_case_insensitively(client):
    make_user(email="mixed@example.com")
    with patch("blueprints.auth.routes.send_otp_email", return_value=True) as sender:
        client.post("/forgot-password", data={"email": "MIXED@Example.com"})
    assert sender.called


def _oauth_user(email_confirmed_at, email="owner@example.com", uid="uid-1"):
    return SimpleNamespace(
        id=uid, email=email, email_confirmed_at=email_confirmed_at, confirmed_at=None,
        app_metadata={"provider": "google"}, user_metadata={"full_name": "Owner Name"},
    )


def _oauth_post(client, supa_user):
    with patch("blueprints.auth.routes.supabase_client") as sb:
        sb.auth.get_user.return_value = SimpleNamespace(user=supa_user)
        return client.post("/auth/callback", json={"access_token": "token"})


def test_oauth_refuses_unconfirmed_provider_email(client):
    make_user(email="owner@example.com")
    response = _oauth_post(client, _oauth_user(None))
    assert response.status_code == 403
    with client.session_transaction() as sess:
        assert "_user_id" not in sess


def test_oauth_links_confirmed_email_to_verified_account(client):
    user = make_user(email="owner@example.com")
    response = _oauth_post(client, _oauth_user("2026-01-01T00:00:00Z"))
    assert response.status_code == 200
    assert response.get_json()["success"] is True
    assert db.session.get(User, user.id).supabase_uid == "uid-1"


def test_oauth_prehijack_resets_attacker_password_on_unverified_account(client):
    attacker_password = "Attacker-Pass1!"
    user = make_user(email="owner@example.com", password=attacker_password, is_verified=False)
    old_hash = user.password_hash
    response = _oauth_post(client, _oauth_user("2026-01-01T00:00:00Z"))
    assert response.status_code == 200
    refreshed = db.session.get(User, user.id)
    assert refreshed.is_verified is True
    assert refreshed.password_hash != old_hash
    from werkzeug.security import check_password_hash
    assert not check_password_hash(refreshed.password_hash, attacker_password)


def test_oauth_refuses_identity_mismatch(client):
    make_user(email="owner@example.com", supabase_uid="uid-original")
    response = _oauth_post(client, _oauth_user("2026-01-01T00:00:00Z", uid="uid-attacker"))
    assert response.status_code == 403
    with client.session_transaction() as sess:
        assert "_user_id" not in sess


@pytest.fixture()
def csrf_on(app):
    app.config["WTF_CSRF_ENABLED"] = True
    yield
    app.config["WTF_CSRF_ENABLED"] = False


def _csrf_token(client):
    page = client.get("/home").get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', page).group(1)


def test_delete_quiz_requires_csrf_token(app, csrf_on):
    app.config["WTF_CSRF_ENABLED"] = False
    client, user = _logged_in_client(app)
    quiz = make_quiz_session(user, [QUESTION])
    app.config["WTF_CSRF_ENABLED"] = True

    assert client.delete(f"/api/quiz/{quiz.id}").status_code == 400
    token = _csrf_token(client)
    assert client.delete(f"/api/quiz/{quiz.id}", headers={"X-CSRFToken": token}).status_code == 200


def test_save_progress_requires_csrf_and_accepts_beacon_form(app, csrf_on):
    app.config["WTF_CSRF_ENABLED"] = False
    client, user = _logged_in_client(app)
    quiz = make_quiz_session(user, [QUESTION])
    app.config["WTF_CSRF_ENABLED"] = True

    url = f"/api/quiz/{quiz.id}/save-progress"
    assert client.post(url, json={"answers": {}}).status_code == 400
    token = _csrf_token(client)
    response = client.post(url, data={"csrf_token": token, "payload": '{"answers": {}, "seconds_remaining": 42}'})
    assert response.status_code == 200
    assert db.session.get(QuizSession, quiz.id).seconds_remaining == 42


def test_quiz_and_deck_ownership_is_enforced(app):
    client, _ = _logged_in_client(app, email="intruder@example.com")
    victim = make_user(email="victim@example.com")
    quiz = make_quiz_session(victim, [QUESTION])
    assert client.get(f"/api/quiz/{quiz.id}/questions").status_code == 403
    assert client.post(f"/api/quiz/{quiz.id}/submit", json={"answers": {}}).status_code == 403
    assert client.delete(f"/api/quiz/{quiz.id}").status_code in (400, 403)
    assert db.session.get(QuizSession, quiz.id) is not None


def test_correct_answers_are_hidden_until_quiz_is_completed(app):
    client, user = _logged_in_client(app)
    quiz = make_quiz_session(user, [QUESTION])
    body = client.get(f"/api/quiz/{quiz.id}/questions").get_json()
    assert body["explanations"] == {}
    assert "correct" not in str(body["questions"])


def test_authenticated_pages_are_not_cached(app):
    client, _ = _logged_in_client(app)
    assert client.get("/home").headers["Cache-Control"] == "no-store"


def test_security_headers_present(client):
    headers = client.get("/").headers
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]


def test_upload_mime_is_detected_from_content_not_client_header(tmp_path):
    files = {
        "a.pdf": (b"%PDF-1.7 rest", "application/pdf"),
        "b.jpg": (b"\xff\xd8\xff\xe0 rest", "image/jpeg"),
        "c.png": (b"\x89PNG\r\n\x1a\n rest", "image/png"),
        "d.webp": (b"RIFF\x00\x00\x00\x00WEBPVP8 ", "image/webp"),
        "e.bin": (b"<script>alert(1)</script>", None),
    }
    for name, (content, expected) in files.items():
        path = tmp_path / name
        path.write_bytes(content)
        assert detect_upload_mime(str(path)) == expected
    assert is_allowed_upload_content(str(tmp_path / "a.pdf"), expect_pdf=True)
    assert not is_allowed_upload_content(str(tmp_path / "a.pdf"), expect_pdf=False)
    assert not is_allowed_upload_content(str(tmp_path / "b.jpg"), expect_pdf=True)
    assert not is_allowed_upload_content(str(tmp_path / "e.bin"), expect_pdf=False)
    assert not is_allowed_upload_content(str(tmp_path / "missing"), expect_pdf=True)


@pytest.mark.parametrize("value,expected", [
    ("a@b.co", True), ("a b@c.com", False), ("no-at-sign", False), ("a@b", False), ("", False), (None, False),
    ("x" * 150 + "@b.co", False),
])
def test_email_validation(value, expected):
    assert is_valid_email(normalize_email(value)) is expected


def test_rls_script_covers_every_model_table(app):
    sql = open(os.path.join(SQL_DIR, "enable_rls.sql")).read()
    for table in db.metadata.tables:
        quoted = f'public."{table}"' if table == "user" else f"public.{table}"
        assert f"alter table {quoted} enable row level security;" in sql
        assert f"revoke all on table {quoted} from anon, authenticated;" in sql


PUBLIC_ENDPOINTS = {
    "static", "auth.index", "auth.login", "auth.register", "auth.verify_email",
    "auth.resend_verification_email", "auth.oauth_callback_page", "auth.oauth_callback_exchange",
    "auth.complete_oauth_profile_page", "auth.complete_oauth_profile_submit", "auth.ping_server",
    "auth.forgot_password", "auth.verify_otp",
}

UNSAFE = {"POST", "PUT", "PATCH", "DELETE"}


def _concrete_path(rule):
    path = rule.rule
    for arg in rule.arguments:
        path = re.sub(rf"<[^>]*{arg}>", "1", path)
    return path


def _rules(app):
    return [r for r in app.url_map.iter_rules()]


def test_every_non_public_route_rejects_anonymous_users(app):
    client = app.test_client()
    checked = 0
    for rule in _rules(app):
        if rule.endpoint in PUBLIC_ENDPOINTS:
            continue
        for method in rule.methods - {"HEAD", "OPTIONS"}:
            response = client.open(_concrete_path(rule), method=method)
            assert response.status_code in (302, 400, 401), (rule.endpoint, method, response.status_code)
            if response.status_code == 302:
                assert "/login" in response.headers["Location"], (rule.endpoint, method)
            checked += 1
    assert checked >= 25


def test_every_state_changing_route_requires_csrf_token(app, csrf_on):
    client = app.test_client()
    checked = 0
    for rule in _rules(app):
        if rule.endpoint in ("static", "auth.ping_server"):
            continue
        for method in rule.methods & UNSAFE:
            response = client.open(_concrete_path(rule), method=method)
            assert response.status_code == 400, (rule.endpoint, method, response.status_code)
            checked += 1
    assert checked >= 15


def test_no_route_serves_dotfiles_or_source(client):
    for path in ("/.env", "/.git/config", "/config.py", "/app.py", "/static/../config.py", "/uploads/", "/instance/quiz_saas.db"):
        assert client.get(path).status_code in (404, 400), path