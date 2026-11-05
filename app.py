import os
import logging

from flask import Flask
from werkzeug.middleware.proxy_fix import ProxyFix
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
logger = logging.getLogger("quizpup")

from config import Config, IS_PRODUCTION
from extensions import db, login_manager, csrf, limiter
from security_headers import register_security_hooks
from error_handlers import register_error_handlers
from blueprints.auth import auth_bp
from blueprints.dashboard import dashboard_bp
from blueprints.profile import profile_bp
from blueprints.flashcards import flashcards_bp
from blueprints.notes import notes_bp
from blueprints.quiz import quiz_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(Config)
    app.config['TEMPLATES_AUTO_RELOAD'] = not IS_PRODUCTION

    if IS_PRODUCTION:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)

    if app.config['RATELIMIT_STORAGE_URI'] == 'memory://' and IS_PRODUCTION:
        print("--- WARNING: Rate limiting is using in-process memory storage in "
              "production. If Gunicorn runs more than one worker, set "
              "RATELIMIT_STORAGE_URI to a shared Redis instance or these limits "
              "are effectively multiplied by the worker count. ---")

    os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

    db.init_app(app)
    login_manager.init_app(app)
    csrf.init_app(app)
    limiter.init_app(app)

    register_security_hooks(app)
    register_error_handlers(app)

    app.register_blueprint(auth_bp)
    app.register_blueprint(dashboard_bp)
    app.register_blueprint(profile_bp)
    app.register_blueprint(flashcards_bp)
    app.register_blueprint(notes_bp)
    app.register_blueprint(quiz_bp)

    with app.app_context():
        db.create_all()

    return app


app = create_app()

if __name__ == '__main__':
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    if debug_mode and IS_PRODUCTION:
        raise RuntimeError("FLASK_DEBUG=true is not allowed when running in production.")
    app.run(debug=debug_mode)