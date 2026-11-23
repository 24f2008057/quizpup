from flask import Blueprint

billing_bp = Blueprint('billing', __name__)

from . import routes  # noqa: E402,F401
from .routes import register_billing_cli  # noqa: E402,F401