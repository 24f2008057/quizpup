import re

import pytest

from tests.conftest import login, make_quiz_session, make_user

BACK_LINK = re.compile(r"<a[^>]*\bdata-back\b[^>]*>")

PUBLIC_PAGES_WITH_BACK = [
    ("/login", "/"),
    ("/register", "/"),
    ("/forgot-password", "/login"),
]

APP_PAGES_WITH_BACK = [
    ("/flashcards", "/home"),
    ("/study", "/home"),
    ("/account", "/home"),
    ("/profile", "/home"),
    ("/profile/edit", "/profile"),
]


def _href(html):
    match = BACK_LINK.search(html)
    assert match, "no back link rendered"
    href = re.search(r'href="([^"]*)"', match.group(0))
    return href.group(1)


@pytest.mark.parametrize("path,target", PUBLIC_PAGES_WITH_BACK)
def test_public_pages_have_a_back_link(client, path, target):
    html = client.get(path).get_data(as_text=True)
    assert _href(html) == target


def test_landing_page_has_no_back_link(client):
    html = client.get("/").get_data(as_text=True)
    assert not BACK_LINK.search(html)


def test_verify_email_page_has_a_back_link(client):
    user = make_user(email="verify@example.com", is_verified=False)
    with client.session_transaction() as sess:
        sess["pending_verification_user_id"] = user.id
    response = client.get("/verify-email")
    assert response.status_code == 200
    assert _href(response.get_data(as_text=True)) == "/login"


def test_verify_otp_page_has_a_back_link(client):
    make_user(email="reset@example.com")
    with client.session_transaction() as sess:
        sess["reset_password_email"] = "reset@example.com"
    response = client.get("/verify-otp")
    assert response.status_code == 200
    assert _href(response.get_data(as_text=True)) == "/forgot-password"


@pytest.mark.parametrize("path,target", APP_PAGES_WITH_BACK)
def test_signed_in_pages_have_a_back_link(client, path, target):
    make_user(email="nav@example.com")
    login(client, "nav@example.com", "Correct-Horse9!")
    html = client.get(path).get_data(as_text=True)
    assert _href(html) == target


def test_home_is_the_app_root_and_has_no_back_link(client):
    make_user(email="home@example.com")
    login(client, "home@example.com", "Correct-Horse9!")
    html = client.get("/home").get_data(as_text=True)
    assert not BACK_LINK.search(html)


def test_quiz_page_has_a_back_link_to_home(client):
    user = make_user(email="quiz@example.com")
    quiz = make_quiz_session(
        user,
        [
            {
                "question_text": "Q",
                "option_1": "a",
                "option_2": "b",
                "option_3": "c",
                "option_4": "d",
                "correct_option": 1,
            }
        ],
    )
    login(client, "quiz@example.com", "Correct-Horse9!")
    html = client.get(f"/quiz/{quiz.id}").get_data(as_text=True)
    assert _href(html) == "/home"


def test_logout_returns_to_the_landing_page(client):
    make_user(email="out@example.com")
    login(client, "out@example.com", "Correct-Horse9!")
    response = client.get("/logout", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["Location"].rstrip("/") == ""
    landing = client.get(response.headers["Location"])
    assert landing.status_code == 200
    assert "Exam preparation for any exam" in landing.get_data(as_text=True)


def test_after_logout_app_pages_are_closed(client):
    make_user(email="closed@example.com")
    login(client, "closed@example.com", "Correct-Horse9!")
    client.get("/logout")
    response = client.get("/home", follow_redirects=False)
    assert response.status_code == 302
    assert "/login" in response.headers["Location"]


def test_every_page_loads_the_shared_design_tokens(client):
    make_user(email="tokens@example.com")
    for path in ("/", "/login", "/register", "/forgot-password"):
        html = client.get(path).get_data(as_text=True)
        assert "css/app.css" in html
    login(client, "tokens@example.com", "Correct-Horse9!")
    for path in ("/home", "/flashcards", "/study", "/account", "/profile", "/profile/edit"):
        html = client.get(path).get_data(as_text=True)
        assert "css/app.css" in html
        assert "cdn.jsdelivr.net/npm/@tailwindcss" not in html