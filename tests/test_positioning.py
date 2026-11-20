def test_landing_page_states_the_any_exam_positioning(client):
    html = client.get("/").get_data(as_text=True)
    assert "<title>QuizPup — Exam preparation for any exam</title>" in html
    assert "Exam preparation for any exam" in html
    assert 'name="description"' in html
    assert "JEE, NEET and CAT" not in html


def test_login_page_has_its_own_descriptive_title(client):
    html = client.get("/login").get_data(as_text=True)
    assert "<title>Log in — QuizPup</title>" in html