from datetime import datetime, timedelta
from unittest.mock import patch

from extensions import db
from models import ConsentRecord, User
from services.age_service import make_parent_manage_token, make_parent_token
from services.privacy_service import (
    has_parent_consent, parent_consent_withdrawn, purge_expired, record_parental_consent
)
from tests.conftest import login, make_user


def _confirmed_minor(email="kid@example.com", parent="parent@example.com"):
    user = make_user(email=email, age_group="minor", parent_email=parent)
    record_parental_consent(user)
    db.session.commit()
    return user


def _manage_token(app, user):
    with app.test_request_context():
        return make_parent_manage_token(user)


def test_minor_page_is_marked_and_has_no_allow_optional_button(client):
    _confirmed_minor()
    login(client, "kid@example.com", "Correct-Horse9!")
    html = client.get("/home").get_data(as_text=True)
    assert 'data-minor="1"' in html
    assert 'id="consent-accept"' not in html


def test_adult_page_keeps_the_banner_choice(client):
    make_user(email="adult@example.com", age_group="adult")
    login(client, "adult@example.com", "Correct-Horse9!")
    html = client.get("/home").get_data(as_text=True)
    assert 'data-minor' not in html
    assert 'id="consent-accept"' in html


def test_consent_script_refuses_optional_categories_for_minors():
    with open("static/js/consent.js", encoding="utf-8") as handle:
        script = handle.read()
    assert "data-minor" in script
    assert "if (isMinor()) return false;" in script


@patch("blueprints.privacy.routes.send_parent_confirmed", return_value=True)
def test_confirmation_shows_manage_link_and_sends_receipt(mock_receipt, app, client):
    user = make_user(email="kid@example.com", age_group="minor", parent_email="parent@example.com")
    with app.test_request_context():
        token = make_parent_token(user)
    resp = client.post(f"/parent/confirm/{token}", data={"is_parent": "on", "accept_terms": "on"})
    assert resp.status_code == 200
    assert "/parent/manage/" in resp.get_data(as_text=True)
    mock_receipt.assert_called_once()


def test_manage_page_shows_status(app, client):
    user = _confirmed_minor()
    page = client.get(f"/parent/manage/{_manage_token(app, user)}")
    assert page.status_code == 200
    assert "you have confirmed" in page.get_data(as_text=True)


def test_parent_can_withdraw_consent_and_child_is_locked(app, client):
    user = _confirmed_minor()
    token = _manage_token(app, user)
    resp = client.post(f"/parent/manage/{token}", data={"action": "withdraw"})
    assert resp.status_code == 200 and "withdrawn consent" in resp.get_data(as_text=True)
    assert not has_parent_consent(user) and parent_consent_withdrawn(user)
    record = ConsentRecord.query.filter_by(user_id=user.id, consent_type="parental").order_by(ConsentRecord.id.desc()).first()
    assert record.granted is False and record.source == "parent_withdrawal"
    login(client, "kid@example.com", "Correct-Horse9!")
    held = client.get("/home")
    assert held.status_code == 302 and "/parent-pending" in held.headers["Location"]
    assert "withdrawn consent" in client.get("/parent-pending").get_data(as_text=True)


def test_parent_can_confirm_again_after_withdrawal(app, client):
    user = _confirmed_minor()
    client.post(f"/parent/manage/{_manage_token(app, user)}", data={"action": "withdraw"})
    with app.test_request_context():
        token = make_parent_token(user)
    client.post(f"/parent/confirm/{token}", data={"is_parent": "on", "accept_terms": "on"})
    assert has_parent_consent(user)


@patch("services.privacy_service._delete_supabase_identity", return_value=True)
def test_parent_can_delete_the_childs_account(mock_identity, app, client):
    user = _confirmed_minor()
    user_id = user.id
    token = _manage_token(app, user)
    missing = client.post(f"/parent/manage/{token}", data={"action": "delete"})
    assert missing.status_code == 400 and db.session.get(User, user_id) is not None
    done = client.post(f"/parent/manage/{token}", data={"action": "delete", "confirm_delete": "on"})
    assert done.status_code == 200 and "deleted" in done.get_data(as_text=True)
    assert db.session.get(User, user_id) is None
    assert ConsentRecord.query.filter_by(user_id=user_id).count() == 0


def test_manage_link_is_invalid_after_parent_email_changes_or_when_tampered(app, client):
    user = _confirmed_minor()
    token = _manage_token(app, user)
    user.parent_email = "other@example.com"
    db.session.commit()
    assert client.get(f"/parent/manage/{token}").status_code == 400
    assert client.get("/parent/manage/garbage").status_code == 400


def test_consent_link_cannot_be_used_as_manage_link(app, client):
    user = _confirmed_minor()
    with app.test_request_context():
        token = make_parent_token(user)
    assert client.get(f"/parent/manage/{token}").status_code == 400


def test_unknown_manage_action_is_rejected(app, client):
    user = _confirmed_minor()
    resp = client.post(f"/parent/manage/{_manage_token(app, user)}", data={"action": "other"})
    assert resp.status_code == 400


def test_purge_removes_minors_never_confirmed_by_a_parent(app):
    old = datetime.utcnow() - timedelta(days=app.config["PARENT_PENDING_ACCOUNT_DAYS"] + 1)
    stale = make_user(email="stale@example.com", age_group="minor", parent_email="p1@example.com", created_at=old)
    withdrawn = make_user(email="withdrawn@example.com", age_group="minor", parent_email="p2@example.com", created_at=old)
    record_parental_consent(withdrawn)
    from services.privacy_service import record_parental_withdrawal
    record_parental_withdrawal(withdrawn)
    fresh = make_user(email="fresh@example.com", age_group="minor", parent_email="p3@example.com")
    adult = make_user(email="grown@example.com", age_group="adult", created_at=old)
    db.session.commit()
    ids = (stale.id, withdrawn.id, fresh.id, adult.id)
    with patch("services.privacy_service._delete_supabase_identity", return_value=True):
        result = purge_expired()
    assert result["unconfirmed_minor_accounts_removed"] == 1
    assert db.session.get(User, ids[0]) is None
    assert all(db.session.get(User, i) is not None for i in ids[1:])


def test_export_includes_parent_consent_history(app):
    from services.privacy_service import export_user_data
    user = _confirmed_minor()
    data = export_user_data(user)
    parental = [c for c in data["consents"] if c["type"] == "parental"]
    assert parental and parental[0]["parent_email"] == "parent@example.com"
    assert data["account"]["age_group"] == "minor"