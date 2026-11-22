from extensions import db
from models import QuizQuestion
from tests.conftest import login, make_quiz_session, make_user

QUESTIONS = [
    {"question_text": f"Q{i}", "option_1": "a", "option_2": "b", "option_3": "c", "option_4": "d", "correct_option": 1}
    for i in range(1, 6)
]


def _setup(client, email="pal@example.com"):
    user = make_user(email=email)
    quiz = make_quiz_session(user, QUESTIONS)
    login(client, email, "Correct-Horse9!")
    ids = [q.id for q in QuizQuestion.query.filter_by(session_id=quiz.id).order_by(QuizQuestion.id)]
    return quiz, ids


def _save(client, quiz, payload):
    return client.post(f"/api/quiz/{quiz.id}/save-progress", json=payload)


def test_questions_endpoint_starts_with_nothing_visited_or_marked(client):
    quiz, ids = _setup(client)
    data = client.get(f"/api/quiz/{quiz.id}/questions").get_json()
    assert data["visited_ids"] == [] and data["marked_ids"] == []


def test_visited_and_marked_are_saved_and_returned(client):
    quiz, ids = _setup(client)
    resp = _save(client, quiz, {"answers": {}, "visited": ids[:3], "marked": [ids[1]]})
    assert resp.status_code == 200
    data = client.get(f"/api/quiz/{quiz.id}/questions").get_json()
    assert data["visited_ids"] == ids[:3]
    assert data["marked_ids"] == [ids[1]]


def test_unmarking_removes_the_mark_but_visited_never_goes_back(client):
    quiz, ids = _setup(client)
    _save(client, quiz, {"visited": ids[:2], "marked": [ids[0], ids[1]]})
    _save(client, quiz, {"visited": [ids[0]], "marked": [ids[1]]})
    data = client.get(f"/api/quiz/{quiz.id}/questions").get_json()
    assert data["marked_ids"] == [ids[1]]
    assert data["visited_ids"] == ids[:2]


def test_payload_without_marked_key_keeps_existing_marks(client):
    quiz, ids = _setup(client)
    _save(client, quiz, {"visited": [ids[0]], "marked": [ids[0]]})
    _save(client, quiz, {"visited": [ids[1]], "answers": {}})
    data = client.get(f"/api/quiz/{quiz.id}/questions").get_json()
    assert data["marked_ids"] == [ids[0]]


def test_ids_from_other_quizzes_and_garbage_are_ignored(client):
    quiz, ids = _setup(client)
    other_user = make_user(email="someone@example.com")
    other = make_quiz_session(other_user, QUESTIONS)
    other_ids = [q.id for q in QuizQuestion.query.filter_by(session_id=other.id)]
    _save(client, quiz, {"visited": other_ids + ["x", None, {"a": 1}], "marked": other_ids})
    assert not any(q.visited or q.marked for q in QuizQuestion.query.filter(QuizQuestion.id.in_(other_ids)))
    assert client.post(f"/api/quiz/{quiz.id}/save-progress", json={"visited": "nope", "marked": 5}).status_code == 200


def test_other_users_cannot_write_marks(client):
    quiz, ids = _setup(client)
    client.get("/logout")
    make_user(email="intruder@example.com")
    login(client, "intruder@example.com", "Correct-Horse9!")
    assert _save(client, quiz, {"marked": ids}).status_code == 403
    assert not any(q.marked for q in QuizQuestion.query.filter_by(session_id=quiz.id))


def test_quiz_page_has_palette_sheet_and_mark_button(client):
    quiz, ids = _setup(client)
    html = client.get(f"/quiz/{quiz.id}").get_data(as_text=True)
    assert "data-palette-mount" in html
    assert 'id="markBtn"' in html and 'aria-pressed="false"' in html
    assert 'id="palette-sheet"' in html and "<dialog" in html


def test_sidebar_logout_is_a_compact_button_not_a_full_height_nav_item(client):
    make_user(email="nav@example.com")
    login(client, "nav@example.com", "Correct-Horse9!")
    html = client.get("/home").get_data(as_text=True)
    assert 'class="rail-logout"' in html
    assert 'class="nav-item mt-auto"' not in html