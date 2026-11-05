import os
import tempfile

# --- Test environment must be set BEFORE anything imports config.py/app.py ---
# config.py resolves SECRET_KEY / DATABASE_URL etc. as class attributes at
# *import* time, so once `import app` (or anything that imports config) has
# happened once in the process, changing os.environ afterward has no effect.
_TEST_DB_FD, _TEST_DB_PATH = tempfile.mkstemp(suffix=".db")
os.environ["SECRET_KEY"] = "pytest-only-secret-key-do-not-use-elsewhere"
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH}"
os.environ["GEMINI_API_KEY"] = "test-dummy-gemini-key"
os.environ["SUPABASE_URL"] = "https://example.supabase.co"
os.environ["SUPABASE_KEY"] = "test-dummy-supabase-key"
os.environ["SUPABASE_ANON_KEY"] = "test-dummy-supabase-anon-key"
os.environ["RATELIMIT_STORAGE_URI"] = "memory://"
os.environ.pop("FLASK_ENV", None)
os.environ.pop("RENDER", None)  # make sure IS_PRODUCTION resolves False for tests

import pytest
from werkzeug.security import generate_password_hash

from app import create_app
from extensions import db as _db
from models import User, QuizSession, QuizQuestion


@pytest.fixture(scope="session")
def app():
    flask_app = create_app()
    flask_app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,   # form posts in tests don't carry a CSRF token
    )
    # RATELIMIT_ENABLED in config is read once, inside create_app()'s
    # limiter.init_app(app) call — setting it here afterward has no effect.
    # Flip the live Limiter instance instead so tests never trip real limits.
    from extensions import limiter as _limiter
    _limiter.enabled = False

    yield flask_app
    with flask_app.app_context():
        _db.session.remove()
        _db.engine.dispose()
    os.close(_TEST_DB_FD)
    try:
        os.unlink(_TEST_DB_PATH)
    except PermissionError:
        pass


@pytest.fixture(autouse=True)
def db_session(app):
    """Fresh schema for every test function, sharing the one temp-file engine
    (a real file, not sqlite:///:memory:, so it behaves the same across the
    multiple connections Flask-SQLAlchemy may open per request)."""
    with app.app_context():
        _db.create_all()
        yield _db
        _db.session.remove()
        _db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()


def make_user(email="student@example.com", username=None, password="Correct-Horse9!",
              is_verified=True, **overrides):
    if username is None:
        username = email.split("@")[0].lower().replace(".", "_")
    user = User(
        username=username,
        password_hash=generate_password_hash(password, method="scrypt"),
        first_name=overrides.pop("first_name", "Ada"),
        last_name=overrides.pop("last_name", "Lovelace"),
        email=email,
        phone=overrides.pop("phone", "9999999999"),
        is_verified=is_verified,
        **overrides,
    )
    _db.session.add(user)
    _db.session.commit()
    return user


def login(client, email, password):
    return client.post("/login", data={"email": email, "password": password}, follow_redirects=False)


def make_quiz_session(user, questions, **overrides):
    session_instance = QuizSession(
        user_id=user.id,
        topic=overrides.pop("topic", "Thermodynamics"),
        difficulty=overrides.pop("difficulty", "medium"),
        total_questions=len(questions),
        duration_minutes=overrides.pop("duration_minutes", 10),
        seconds_remaining=overrides.pop("seconds_remaining", 600),
        hints_remaining=overrides.pop("hints_remaining", 1),
        **overrides,
    )
    _db.session.add(session_instance)
    _db.session.commit()

    for q in questions:
        db_q = QuizQuestion(
            session_id=session_instance.id,
            question_text=q["question_text"],
            option_1=q["option_1"], option_2=q["option_2"],
            option_3=q["option_3"], option_4=q["option_4"],
            correct_option=q["correct_option"],
            hint=q.get("hint", "Think carefully."),
            explanation=q.get("explanation", "Because physics."),
        )
        _db.session.add(db_q)
    _db.session.commit()
    return session_instance