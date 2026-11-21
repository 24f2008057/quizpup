from unittest.mock import patch

from models import User

BASE = {
    "username": "kidstudent", "password": "Correct-Horse9!",
    "first_name": "Ravi", "last_name": "Kumar", "email": "ravi@example.com",
    "accept_terms": "on",
}


@patch("blueprints.auth.routes.send_verification_email", return_value=True)
def test_minor_with_parent_email_is_stored(mock_send, client):
    resp = client.post("/register", data={
        **BASE, "age_group": "minor", "parent_email": "Parent@Example.com",
    })
    assert resp.status_code == 302
    user = User.query.filter_by(email="ravi@example.com").first()
    assert user.age_group == "minor"
    assert user.parent_email == "parent@example.com"


@patch("blueprints.auth.routes.send_verification_email", return_value=True)
def test_adult_stores_no_parent_email(mock_send, client):
    client.post("/register", data={
        **BASE, "age_group": "adult", "parent_email": "ignored@example.com",
    })
    user = User.query.filter_by(email="ravi@example.com").first()
    assert user.age_group == "adult"
    assert user.parent_email is None


def test_minor_without_valid_parent_email_is_rejected(client):
    for parent in ("", "not-an-email", "ravi@example.com"):
        client.post("/register", data={**BASE, "age_group": "minor", "parent_email": parent})
        assert User.query.filter_by(email="ravi@example.com").first() is None


def test_missing_or_invalid_age_group_is_rejected(client):
    for extra in ({}, {"age_group": ""}, {"age_group": "teen"}):
        client.post("/register", data={**BASE, **extra})
        assert User.query.filter_by(email="ravi@example.com").first() is None


def test_register_form_asks_age_not_a_single_adult_checkbox(client):
    html = client.get("/register").get_data(as_text=True)
    assert 'name="age_group"' in html and 'name="parent_email"' in html
    assert 'name="confirm_age"' not in html