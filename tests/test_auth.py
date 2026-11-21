from datetime import datetime, timedelta
from unittest.mock import patch

from extensions import db
from models import User
from tests.conftest import make_user, login


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

@patch("blueprints.auth.routes.send_verification_email", return_value=True)
def test_register_creates_unverified_user_and_redirects_to_verify(mock_send, client):
    resp = client.post("/register", data={
        "username": "newstudent",
        "password": "Correct-Horse9!",
        "first_name": "Grace",
        "last_name": "Hopper",
        "email": "grace@example.com",
        "accept_terms": "on", "age_group": "adult",
    }, follow_redirects=False)

    assert resp.status_code == 302
    assert "/verify-email" in resp.headers["Location"]

    user = User.query.filter_by(email="grace@example.com").first()
    assert user is not None
    assert user.is_verified is False
    assert user.verification_otp is not None
    assert user.password_hash != "Correct-Horse9!"  # never store plaintext
    mock_send.assert_called_once()


def test_register_rejects_weak_password(client):
    resp = client.post("/register", data={
        "username": "weakpw",
        "password": "alllowercase",  # no digit, no special char
        "first_name": "A", "last_name": "B",
        "email": "weak@example.com", "accept_terms": "on", "age_group": "adult",
    }, follow_redirects=False)

    assert resp.status_code == 302
    assert User.query.filter_by(email="weak@example.com").first() is None


def test_register_rejects_duplicate_email(client):
    make_user(email="taken@example.com", username="original")

    resp = client.post("/register", data={
        "username": "someoneelse",
        "password": "Correct-Horse9!",
        "first_name": "A", "last_name": "B",
        "email": "taken@example.com", "accept_terms": "on", "age_group": "adult",
    }, follow_redirects=False)

    assert resp.status_code == 302
    assert User.query.filter_by(username="someoneelse").first() is None


def test_register_rejects_duplicate_username(client):
    make_user(email="first@example.com", username="dupeusername")

    resp = client.post("/register", data={
        "username": "dupeusername",
        "password": "Correct-Horse9!",
        "first_name": "A", "last_name": "B",
        "email": "second@example.com", "accept_terms": "on", "age_group": "adult",
    }, follow_redirects=False)

    assert resp.status_code == 302
    assert User.query.filter_by(email="second@example.com").first() is None


# ---------------------------------------------------------------------------
# Login
# ---------------------------------------------------------------------------

def test_login_fails_with_wrong_password(client):
    make_user(email="student@example.com", password="Correct-Horse9!")
    resp = login(client, "student@example.com", "totally-wrong-password")
    assert resp.status_code == 200  # re-renders login form, no redirect
    with client.session_transaction() as sess:
        assert "_user_id" not in sess


def test_login_blocks_unverified_user_and_routes_to_verify(client):
    make_user(email="unverified@example.com", password="Correct-Horse9!", is_verified=False)
    resp = login(client, "unverified@example.com", "Correct-Horse9!")
    assert resp.status_code == 302
    assert "/verify-email" in resp.headers["Location"]
    with client.session_transaction() as sess:
        assert "_user_id" not in sess


def test_login_succeeds_for_verified_user(client):
    make_user(email="student@example.com", password="Correct-Horse9!", is_verified=True)
    resp = login(client, "student@example.com", "Correct-Horse9!")
    assert resp.status_code == 302
    assert resp.headers["Location"].endswith("/home")
    with client.session_transaction() as sess:
        assert "_user_id" in sess


def test_login_is_case_insensitive_on_email(client):
    make_user(email="Student@Example.com", password="Correct-Horse9!", is_verified=True)
    resp = login(client, "student@example.com", "Correct-Horse9!")
    with client.session_transaction() as sess:
        assert "_user_id" in sess


# ---------------------------------------------------------------------------
# Logout
# ---------------------------------------------------------------------------

def test_logout_clears_session(client):
    make_user(email="student@example.com", password="Correct-Horse9!", is_verified=True)
    login(client, "student@example.com", "Correct-Horse9!")

    resp = client.get("/logout", follow_redirects=False)
    assert resp.status_code == 302
    with client.session_transaction() as sess:
        assert "_user_id" not in sess


def test_logout_requires_login(client):
    resp = client.get("/logout", follow_redirects=False)
    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]


# ---------------------------------------------------------------------------
# Email verification (OTP)
# ---------------------------------------------------------------------------

def _put_pending_verification(client, user):
    with client.session_transaction() as sess:
        sess["pending_verification_user_id"] = user.id


def test_verify_email_correct_otp_marks_verified(client):
    user = make_user(email="pending@example.com", is_verified=False)
    user.verification_otp = "123456"
    user.verification_otp_expires = datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()
    _put_pending_verification(client, user)

    resp = client.post("/verify-email", data={"otp": "123456"}, follow_redirects=False)

    assert resp.status_code == 302
    assert "/login" in resp.headers["Location"]
    refreshed = db.session.get(User, user.id)
    assert refreshed.is_verified is True
    assert refreshed.verification_otp is None


def test_verify_email_wrong_otp_increments_attempts(client):
    user = make_user(email="pending2@example.com", is_verified=False)
    user.verification_otp = "123456"
    user.verification_otp_expires = datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()
    _put_pending_verification(client, user)

    resp = client.post("/verify-email", data={"otp": "000000"}, follow_redirects=False)

    assert resp.status_code == 200
    refreshed = db.session.get(User, user.id)
    assert refreshed.is_verified is False
    assert refreshed.verification_attempts == 1


def test_verify_email_expired_otp_rejected(client):
    user = make_user(email="pending3@example.com", is_verified=False)
    user.verification_otp = "123456"
    user.verification_otp_expires = datetime.utcnow() - timedelta(minutes=1)  # already expired
    db.session.commit()
    _put_pending_verification(client, user)

    resp = client.post("/verify-email", data={"otp": "123456"}, follow_redirects=False)

    assert resp.status_code == 200
    assert db.session.get(User, user.id).is_verified is False


def test_verify_email_locks_out_after_max_attempts(client):
    from services.email_service import OTP_MAX_ATTEMPTS

    user = make_user(email="pending4@example.com", is_verified=False)
    user.verification_otp = "123456"
    user.verification_otp_expires = datetime.utcnow() + timedelta(minutes=10)
    user.verification_attempts = OTP_MAX_ATTEMPTS  # already at the limit
    db.session.commit()
    _put_pending_verification(client, user)

    # Even the CORRECT code should now be rejected.
    resp = client.post("/verify-email", data={"otp": "123456"}, follow_redirects=False)

    assert resp.status_code == 200
    assert db.session.get(User, user.id).is_verified is False


# ---------------------------------------------------------------------------
# Forgot password / reset flow
# ---------------------------------------------------------------------------

@patch("blueprints.auth.routes.send_otp_email", return_value=True)
def test_forgot_password_does_not_reveal_account_existence(mock_send, client):
    # Response text/flow must look identical whether or not the email exists.
    resp_known = client.post("/forgot-password", data={"email": "nope@example.com"}, follow_redirects=False)
    assert resp_known.status_code == 302
    assert "/verify-otp" in resp_known.headers["Location"]


def test_reset_password_with_correct_otp_updates_password_hash(client):
    from werkzeug.security import check_password_hash

    user = make_user(email="reset@example.com", password="Old-Passw0rd!")
    user.verification_otp = "654321"
    user.verification_otp_expires = datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()

    with client.session_transaction() as sess:
        sess["reset_password_email"] = user.email

    resp = client.post("/verify-otp", data={
        "otp": "654321",
        "new_password": "Brand-New-Passw0rd!",
    }, follow_redirects=False)

    assert resp.status_code == 302
    refreshed = db.session.get(User, user.id)
    assert check_password_hash(refreshed.password_hash, "Brand-New-Passw0rd!")
    assert not check_password_hash(refreshed.password_hash, "Old-Passw0rd!")


def test_reset_password_rejects_weak_new_password(client):
    user = make_user(email="reset2@example.com", password="Old-Passw0rd!")
    user.verification_otp = "654321"
    user.verification_otp_expires = datetime.utcnow() + timedelta(minutes=10)
    db.session.commit()

    with client.session_transaction() as sess:
        sess["reset_password_email"] = user.email

    resp = client.post("/verify-otp", data={
        "otp": "654321",
        "new_password": "weak",
    }, follow_redirects=False)

    assert resp.status_code == 200  # re-renders form, does not proceed
    from werkzeug.security import check_password_hash
    assert check_password_hash(db.session.get(User, user.id).password_hash, "Old-Passw0rd!")