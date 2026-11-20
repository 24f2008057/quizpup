import re
from pathlib import Path

from tests.conftest import login, make_quiz_session, make_user

TEMPLATES = Path(__file__).resolve().parent.parent / "templates"


def _titles(client, paths):
    out = {}
    for path in paths:
        html = client.get(path).get_data(as_text=True)
        match = re.search(r"<title>(.*?)</title>", html, re.S)
        assert match, path
        out[path] = match.group(1).strip()
    return out


def test_every_img_tag_has_an_alt_attribute():
    for tpl in TEMPLATES.glob("*.html"):
        for tag in re.findall(r"<img\b[^>]*>", tpl.read_text(encoding="utf-8")):
            assert re.search(r"\balt=", tag), f"{tpl.name}: {tag[:80]}"


def test_file_inputs_are_not_hidden_from_the_keyboard():
    for tpl in TEMPLATES.glob("*.html"):
        for tag in re.findall(r"<input\b[^>]*type=\"file\"[^>]*>", tpl.read_text(encoding="utf-8")):
            assert 'class="hidden"' not in tag, f"{tpl.name}: {tag[:80]}"


def test_no_low_contrast_opacity_on_text_in_templates():
    for name in ("dashboard.html", "quiz.html"):
        text = (TEMPLATES / name).read_text(encoding="utf-8")
        assert "opacity-70" not in text and "opacity-60" not in text, name


def test_public_pages_have_distinct_titles(client):
    titles = _titles(client, ["/login", "/register", "/forgot-password"])
    assert len(set(titles.values())) == len(titles)
    assert all(t.endswith("QuizPup") for t in titles.values())


def test_flashcard_card_is_keyboard_operable(client):
    user = make_user(email="fc@example.com")
    login(client, "fc@example.com", "Correct-Horse9!")
    html = client.get("/flashcards").get_data(as_text=True)
    assert 'id="fcCardStage"' in html
    assert 'role="button"' in html and 'tabindex="0"' in html


def test_quiz_page_exposes_timer_radiogroup_and_live_region(client):
    user = make_user(email="qz@example.com")
    quiz = make_quiz_session(
        user,
        [{"question_text": "Q", "option_1": "a", "option_2": "b", "option_3": "c", "option_4": "d", "correct_option": 1}],
    )
    login(client, "qz@example.com", "Correct-Horse9!")
    html = client.get(f"/quiz/{quiz.id}").get_data(as_text=True)
    assert 'role="timer"' in html
    assert 'role="radiogroup"' in html
    assert 'aria-live="polite"' in html
    assert html.count("<h1") == 2