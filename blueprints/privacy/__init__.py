from flask import Blueprint

privacy_bp = Blueprint('privacy', __name__)

from . import routes