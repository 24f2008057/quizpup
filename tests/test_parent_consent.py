from unittest.mock import patch

from extensions import db
from models import ConsentRecord, User
from services.age_service import make_parent_token
from services.privacy_service import has_parent_consent
from tests.conftest import login, make_user

SIGNUP = {
    "username": "kidstudent", "password": "Correct-Horse9!",
    "first_name": "Ravi", "last_name": "Kumar", "email": "ravi@example.com",
    "accept_terms": "on", "age_group": "minor", "parent_email": "parent@example.com",
}


def _minor(**extra):
    return make_user(
        email="kid@example.com", age_group="minor", parent_email="parent@example.com", **extra
    )


def _token(app, user):
    with app.test_request_context():
        return make_parent_token(user)


@patch("blueprints.auth.routes.send_parent_request", return_value=True)
@patch("blueprints.auth.routes.send_verification_email", return_value=True)
def test_minor_signup_sends_parent_email(mock_verify, mock_parent, client):
    client.post("/register", data=SIGNUP)
    user = User.query.filter_by(email="ravi@example.com").first()
    mock_parent.assert_called_once()
    assert mock_parent.call_args[0][0].id == user.id


@patch("blueprints.auth.routes.send_parent_request", return_value=True)
@patch("blueprints.auth.routes.send_verification_email", return_value=True)
def test_adult_signup_sends_no_parent_email(mock_verify, mock_parent, client):
    client.post("/register", data={**SIGNUP, "age_group": "adult", "parent_email": ""})
    mock_parent.assert_not_called()


def test_minor_without_parent_confirmation_is_held_on_pending_page(client):
    _minor()
    login(client, "kid@example.com", "Correct-Horse9!")
    resp = client.get("/home")
    assert resp.status_code == 302 and "/parent-pending" in resp.headers["Location"]
    page = client.get("/parent-pending")
    assert page.status_code == 200
    assert "parent@example.com" in page.get_data(as_text=True)


def test_minor_api_calls_are_blocked_with_a_clear_code(client):
    _minor()
    login(client, "kid@example.com", "Correct-Horse9!")
    resp = client.get("/api/flashcards/my-decks")
    assert resp.status_code == 403
    assert resp.get_json()["code"] == "parental_consent_required"


def test_adult_is_not_held(client):
    make_user(email="adult@example.com", age_group="adult")
    login(client, "adult@example.com", "Correct-Horse9!")
    assert client.get("/home").status_code == 200


def test_parent_link_shows_form_and_requires_both_boxes(app, client):
    user = _minor()
    token = _token(app, user)
    page = client.get(f"/parent/confirm/{token}")
    assert page.status_code == 200 and "Ada" in page.get_data(as_text=True)
    bad = client.post(f"/parent/confirm/{token}", data={"is_parent": "on"})
    assert bad.status_code == 400
    assert not has_parent_consent(user)


def test_parent_confirmation_is_recorded_and_unlocks_the_account(app, client):
    user = _minor()
    token = _token(app, user)
    resp = client.post(f"/parent/confirm/{token}", data={"is_parent": "on", "accept_terms": "on"})
    assert resp.status_code == 200
    record = ConsentRecord.query.filter_by(user_id=user.id, consent_type="parental").one()
    assert record.granted and record.parent_email == "parent@example.com"
    assert record.policy_version == app.config["TERMS_VERSION"] and record.created_at
    assert has_parent_consent(user)
    login(client, "kid@example.com", "Correct-Horse9!")
    assert client.get("/home").status_code == 200


def test_invalid_and_tampered_tokens_are_rejected(app, client):
    user = _minor()
    token = _token(app, user)
    assert client.get("/parent/confirm/garbage").status_code == 400
    assert client.get(f"/parent/confirm/{token[:-3]}abc").status_code == 400


def test_expired_token_is_rejected(app, client):
    user = _minor()
    token = _token(app, user)
    app.config["PARENT_CONSENT_MAX_AGE_SECONDS"] = -1
    try:
        assert client.get(f"/parent/confirm/{token}").status_code == 400
    finally:
        app.config["PARENT_CONSENT_MAX_AGE_SECONDS"] = 7 * 24 * 3600


def test_changing_parent_email_invalidates_old_link_and_old_confirmation(app, client):
    user = _minor()
    token = _token(app, user)
    client.post(f"/parent/confirm/{token}", data={"is_parent": "on", "accept_terms": "on"})
    user.parent_email = "other@example.com"
    db.session.commit()
    assert client.get(f"/parent/confirm/{token}").status_code == 400
    assert not has_parent_consent(user)


@patch("blueprints.privacy.routes.send_parent_request", return_value=True)
def test_resend_and_change_parent_email(mock_send, client):
    user = _minor()
    login(client, "kid@example.com", "Correct-Horse9!")
    client.post("/parent-pending/resend")
    assert mock_send.call_count == 1
    client.post("/parent-pending/change", data={"parent_email": "not-an-email"})
    client.post("/parent-pending/change", data={"parent_email": "kid@example.com"})
    assert mock_send.call_count == 1
    client.post("/parent-pending/change", data={"parent_email": "New@Example.com"})
    assert mock_send.call_count == 2
    assert db.session.get(User, user.id).parent_email == "new@example.com"


def test_pending_routes_require_login(client):
    for path, method in (("/parent-pending", "get"), ("/parent-pending/resend", "post"), ("/parent-pending/change", "post")):
        resp = getattr(client, method)(path)
        assert resp.status_code == 302 and "/login" in resp.headers["Location"]