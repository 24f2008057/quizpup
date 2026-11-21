import json
from datetime import datetime, timedelta
from unittest.mock import patch

from extensions import db
from models import (
    User, ConsentRecord, QuizSession, QuizQuestion, FlashcardDeck, SuperNote
)
from tests.conftest import login, make_user, make_quiz_session

QUESTION = [{
    "question_text": "Q", "option_1": "a", "option_2": "b",
    "option_3": "c", "option_4": "d", "correct_option": 1,
}]

SIGNUP = {
    "username": "newstudent", "password": "Correct-Horse9!",
    "first_name": "Grace", "last_name": "Hopper", "email": "grace@example.com",
}


@patch("blueprints.auth.routes.send_verification_email", return_value=True)
def test_register_records_consent_with_version_and_timestamp(mock_send, client, app):
    resp = client.post("/register", data={**SIGNUP, "accept_terms": "on", "confirm_age": "on"})
    assert resp.status_code == 302
    user = User.query.filter_by(email="grace@example.com").first()
    records = ConsentRecord.query.filter_by(user_id=user.id).all()
    assert {r.consent_type for r in records} == {"terms", "privacy"}
    assert all(r.granted and r.created_at for r in records)
    assert all(r.policy_version == app.config["TERMS_VERSION"] for r in records)
    assert all(r.source == "signup" for r in records)


def test_register_requires_both_checkboxes(client):
    for extra in ({}, {"accept_terms": "on"}, {"confirm_age": "on"}):
        client.post("/register", data={**SIGNUP, **extra})
        assert User.query.filter_by(email="grace@example.com").first() is None


def test_register_form_has_unticked_required_consent_checkboxes_and_no_phone(client):
    html = client.get("/register").get_data(as_text=True)
    assert 'name="accept_terms"' in html and 'name="confirm_age"' in html
    assert "checked" not in html.split('name="accept_terms"')[1].split(">")[0]
    assert 'name="phone"' not in html


def test_existing_user_without_consent_is_sent_to_accept_terms(client):
    make_user(email="old@example.com", consented=False)
    login(client, "old@example.com", "Correct-Horse9!")
    resp = client.get("/home", follow_redirects=False)
    assert resp.status_code == 302 and "/accept-terms" in resp.headers["Location"]
    api = client.get("/api/study/notes/history")
    assert api.status_code == 403 and api.get_json()["code"] == "consent_required"


def test_accept_terms_records_consent_and_unlocks_app(client):
    user = make_user(email="old2@example.com", consented=False)
    login(client, "old2@example.com", "Correct-Horse9!")
    bad = client.post("/accept-terms", data={"accept_terms": "on"})
    assert bad.status_code == 400
    ok = client.post("/accept-terms", data={"accept_terms": "on", "confirm_age": "on", "next": "/flashcards"})
    assert ok.status_code == 302 and ok.headers["Location"].endswith("/flashcards")
    assert ConsentRecord.query.filter_by(user_id=user.id, source="reacceptance").count() == 2
    assert client.get("/home").status_code == 200


def test_accept_terms_rejects_external_redirect(client):
    make_user(email="old3@example.com", consented=False)
    login(client, "old3@example.com", "Correct-Horse9!")
    resp = client.post("/accept-terms", data={
        "accept_terms": "on", "confirm_age": "on", "next": "//evil.example.com"})
    assert "evil.example.com" not in resp.headers["Location"]


def test_new_policy_version_requires_reacceptance(client, app):
    make_user(email="v@example.com")
    login(client, "v@example.com", "Correct-Horse9!")
    assert client.get("/home").status_code == 200
    app.config["TERMS_VERSION"] = "2099-01"
    assert client.get("/home", follow_redirects=False).status_code == 302


def test_legal_pages_are_public_and_logout_is_not_gated(client):
    for path in ("/privacy", "/terms", "/subprocessors"):
        assert client.get(path).status_code == 200
    make_user(email="out@example.com", consented=False)
    login(client, "out@example.com", "Correct-Horse9!")
    assert client.get("/logout", follow_redirects=False).status_code == 302


def test_export_contains_all_user_data_and_nothing_from_others(client):
    user = make_user(email="exp@example.com")
    other = make_user(email="other@example.com")
    make_quiz_session(user, QUESTION)
    make_quiz_session(other, QUESTION)
    db.session.add(FlashcardDeck(user_id=user.id, topic="Deck", cards_json=json.dumps([{"q": "1"}])))
    db.session.add(SuperNote(user_id=user.id, title="Note", digitized_text="text"))
    db.session.commit()
    login(client, "exp@example.com", "Correct-Horse9!")
    resp = client.post("/account/data/export")
    assert resp.status_code == 200
    assert "attachment" in resp.headers["Content-Disposition"]
    data = json.loads(resp.get_data(as_text=True))
    assert data["account"]["email"] == "exp@example.com"
    assert len(data["quizzes"]) == 1 and len(data["flashcard_decks"]) == 1 and len(data["notes"]) == 1
    assert len(data["consents"]) == 2
    assert "password_hash" not in json.dumps(data)
    assert "other@example.com" not in json.dumps(data)


def test_export_requires_login(client):
    assert client.post("/account/data/export").status_code in (302, 401)


def test_delete_account_requires_correct_password(client):
    user = make_user(email="del@example.com")
    login(client, "del@example.com", "Correct-Horse9!")
    client.post("/account/delete", data={"password": "wrong"})
    assert User.query.get(user.id) is not None


@patch("services.privacy_service._delete_supabase_identity", return_value=True)
def test_delete_account_removes_everything(mock_identity, client):
    user = make_user(email="gone@example.com")
    other = make_user(email="stay@example.com")
    uid, oid = user.id, other.id
    make_quiz_session(user, QUESTION)
    make_quiz_session(other, QUESTION)
    db.session.add(FlashcardDeck(user_id=uid, topic="D", cards_json="[]"))
    db.session.add(SuperNote(user_id=uid, title="N"))
    db.session.commit()
    login(client, "gone@example.com", "Correct-Horse9!")
    resp = client.post("/account/delete", data={"password": "Correct-Horse9!"})
    assert resp.status_code == 302
    assert User.query.get(uid) is None
    assert QuizSession.query.filter_by(user_id=uid).count() == 0
    assert QuizQuestion.query.count() == 1
    assert FlashcardDeck.query.filter_by(user_id=uid).count() == 0
    assert SuperNote.query.filter_by(user_id=uid).count() == 0
    assert ConsentRecord.query.filter_by(user_id=uid).count() == 0
    assert User.query.get(oid) is not None
    assert ConsentRecord.query.filter_by(user_id=oid).count() == 2
    mock_identity.assert_called_once()
    assert client.get("/home", follow_redirects=False).status_code == 302


@patch("services.privacy_service._delete_supabase_identity", return_value=True)
def test_oauth_user_deletes_by_typing_delete(mock_identity, client):
    user = make_user(email="oauth@example.com", auth_provider="google", supabase_uid="abc")
    uid = user.id
    login(client, "oauth@example.com", "Correct-Horse9!")
    client.post("/account/delete", data={"confirm_text": "nope"})
    assert User.query.get(uid) is not None
    client.post("/account/delete", data={"confirm_text": "DELETE"})
    assert User.query.get(uid) is None
    mock_identity.assert_called_once_with("abc")


def test_login_records_last_login(client):
    user = make_user(email="seen@example.com")
    assert user.last_login_at is None
    login(client, "seen@example.com", "Correct-Horse9!")
    assert User.query.get(user.id).last_login_at is not None


@patch("services.privacy_service._delete_supabase_identity", return_value=True)
def test_purge_removes_stale_unverified_and_inactive_accounts(mock_identity, app):
    now = datetime.utcnow()
    stale = make_user(email="stale@example.com", is_verified=False, consented=False)
    stale.created_at = now - timedelta(days=30)
    fresh = make_user(email="fresh@example.com", is_verified=False, consented=False)
    fresh.created_at = now - timedelta(days=1)
    dormant = make_user(email="dormant@example.com")
    dormant.last_login_at = now - timedelta(days=900)
    active = make_user(email="active@example.com")
    active.last_login_at = now - timedelta(days=10)
    never = make_user(email="never@example.com")
    db.session.commit()
    from services.privacy_service import purge_expired
    result = purge_expired(now)
    assert result["unverified_accounts_removed"] == 1
    assert result["inactive_accounts_removed"] == 1
    remaining = {u.email for u in User.query.all()}
    assert remaining == {"fresh@example.com", "active@example.com", "never@example.com"}


def test_purge_clears_expired_codes(app):
    user = make_user(email="otp@example.com")
    user.verification_otp = "123456"
    user.verification_otp_expires = datetime.utcnow() - timedelta(minutes=5)
    user.pending_email = "new@example.com"
    user.email_change_otp = "654321"
    user.email_change_otp_expires = datetime.utcnow() - timedelta(minutes=5)
    db.session.commit()
    from services.privacy_service import purge_expired
    purge_expired()
    fresh = User.query.get(user.id)
    assert fresh.verification_otp is None and fresh.email_change_otp is None and fresh.pending_email is None


def test_no_third_party_scripts_and_csp_has_no_cdn(client):
    for path in ("/", "/login", "/register"):
        resp = client.get(path)
        html = resp.get_data(as_text=True)
        assert "cdn.jsdelivr.net" not in html
        assert "google-analytics" not in html and "googletagmanager" not in html
        assert "cdn.jsdelivr.net" not in resp.headers["Content-Security-Policy"]


def test_consent_gate_script_activates_nothing_without_consent_markup(client):
    html = client.get("/login").get_data(as_text=True)
    assert 'id="consent-banner"' in html and "consent.js" in html
    assert 'type="text/plain" data-consent' not in html


def test_ai_notice_shown_on_free_tier_and_hidden_on_paid(client, app):
    make_user(email="ai@example.com")
    login(client, "ai@example.com", "Correct-Horse9!")
    assert "may use submitted content" in client.get("/study").get_data(as_text=True)
    app.config["GEMINI_PAID_TIER"] = True
    html = client.get("/study").get_data(as_text=True)
    assert "may use submitted content" not in html and "does not use your content to train" in html