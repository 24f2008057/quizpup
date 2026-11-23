import pytest

from tests.conftest import login, make_user

PAGES = ("/privacy", "/terms", "/cookies", "/refunds", "/contact")


@pytest.mark.parametrize("path", PAGES)
def test_each_legal_page_is_public_titled_and_marked_as_draft(client, path):
    resp = client.get(path)
    assert resp.status_code == 200
    html = resp.get_data(as_text=True)
    assert "<h1" in html
    assert "has not been reviewed by a lawyer" in html
    assert 'aria-label="Other legal pages"' in html


@pytest.mark.parametrize("path", PAGES)
def test_unset_business_details_show_as_placeholders_not_invented_values(client, path):
    html = client.get(path).get_data(as_text=True)
    assert "[" in html and "bg-warning-bg" in html


def test_business_details_appear_when_configured(app, client, monkeypatch):
    values = dict(
        LEGAL_ENTITY_NAME="Example Labs",
        LEGAL_ADDRESS="1 Test Road",
        SUPPORT_EMAIL="help@example.com",
        GRIEVANCE_OFFICER_NAME="A. Officer",
        GRIEVANCE_OFFICER_EMAIL="grievance@example.com",
        LEGAL_REPLY_TIME="5 working days",
        LEGAL_REVIEWED_BY_LAWYER=True,
        LEGAL_EFFECTIVE_DATE="1 January 2027",
    )
    for key, value in values.items():
        monkeypatch.setitem(app.config, key, value)
    html = client.get("/contact").get_data(as_text=True)
    for value in ("Example Labs", "1 Test Road", "help@example.com", "A. Officer", "grievance@example.com", "5 working days"):
        assert value in html
    assert "has not been reviewed by a lawyer" not in html
    assert "Effective 1 January 2027" in html


def test_cookie_policy_lists_every_cookie_the_app_sets(client):
    html = client.get("/cookies").get_data(as_text=True)
    for name in ("session (cookie)", "remember_token", "quizpup_consent", "isLoggedIn"):
        assert name in html


def test_uptime_is_not_promised_without_measurement(client):
    html = client.get("/terms").get_data(as_text=True)
    assert "have not measured or committed to an uptime percentage" in html
    assert "99." not in html


def test_privacy_page_ai_wording_follows_gemini_tier(app, client, monkeypatch):
    monkeypatch.setitem(app.config, "GEMINI_PAID_TIER", False)
    assert "free tier" in client.get("/privacy").get_data(as_text=True)
    monkeypatch.setitem(app.config, "GEMINI_PAID_TIER", True)
    assert "paid tier" in client.get("/privacy").get_data(as_text=True)


def test_signup_page_links_terms_and_privacy_without_a_footer_strip(client):
    html = client.get("/register").get_data(as_text=True)
    assert 'href="/terms"' in html and 'href="/privacy"' in html
    assert 'aria-label="Legal"' not in html


def test_loading_and_login_pages_have_no_legal_links(client):
    for path in ("/auth/callback", "/login"):
        html = client.get(path).get_data(as_text=True)
        assert 'aria-label="Legal"' not in html
        assert "Cookie settings" not in html


def test_landing_page_is_clean_and_links_about_only(client):
    html = client.get("/").get_data(as_text=True)
    assert 'href="/about"' in html
    assert "Sound off" not in html and "Pause video" not in html
    assert 'aria-label="Pause background video"' in html
    for path in ("/privacy", "/terms", "/cookies", "/refunds"):
        assert f'href="{path}"' not in html


@pytest.mark.parametrize("path", PAGES + ("/about", "/subprocessors"))
def test_every_legal_page_has_a_back_link_and_a_single_link_strip(client, path):
    html = client.get(path).get_data(as_text=True)
    assert 'class="back-link"' in html and "data-back-any" in html
    assert html.count('aria-label="Legal"') == 0
    assert html.count('aria-label="Other legal pages"') == 1 or path == "/subprocessors"


def test_signed_in_account_menu_links_about_and_privacy_terms(client):
    make_user(email="legal2@example.com")
    login(client, "legal2@example.com", "Correct-Horse9!")
    html = client.get("/home").get_data(as_text=True)
    assert 'href="/about"' in html and 'href="/privacy"' in html and 'href="/terms"' in html
    assert client.get("/about").status_code == 200


def test_legal_check_command_fails_until_everything_is_set(app):
    result = app.test_cli_runner().invoke(args=["legal-check"])
    assert result.exit_code == 1
    assert "missing: LEGAL_ENTITY_NAME" in result.output