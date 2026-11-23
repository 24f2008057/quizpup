from flask import render_template
from flask_login import login_required, current_user

from models import QuizSession

from . import dashboard_bp


@dashboard_bp.route('/home')
@login_required
def home():
    past_quizzes = QuizSession.query.filter_by(user_id=current_user.id).order_by(QuizSession.created_at.desc()).all()
    return render_template('dashboard.html', past_quizzes=past_quizzes, view="home")


@dashboard_bp.route('/flashcards')
@login_required
def flashcards_view():
    return render_template('dashboard.html', view="flashcards")


@dashboard_bp.route('/study')
@login_required
def study_view():
    return render_template('dashboard.html', view="study")


@dashboard_bp.route('/account')
@login_required
def account():
    from blueprints.billing.routes import page_context
    return render_template('dashboard.html', view="account", **page_context(current_user))