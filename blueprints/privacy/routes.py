import json
import logging
from datetime import datetime

import click
from flask import (
    Response, current_app, flash, jsonify, redirect, render_template, request, session, url_for
)
from flask_login import current_user, login_required, logout_user
from flask_login import user_logged_in
from werkzeug.security import check_password_hash

from extensions import db, limiter
from services.privacy_service import (
    delete_user_account, export_user_data, has_current_consent, has_parent_consent,
    purge_expired, record_consents, record_parental_consent
)

from services.age_service import parse_age_form, read_parent_token, send_parent_request

from . import privacy_bp

logger = logging.getLogger("quizpup")

GATE_EXEMPT_ENDPOINTS = {
    'static', 'auth.logout', 'auth.ping_server', 'privacy.accept_terms',
    'privacy.privacy_policy', 'privacy.terms', 'privacy.subprocessors',
    'privacy.parent_confirm', 'privacy.parent_pending', 'privacy.parent_resend',
    'privacy.parent_change',
}


def _safe_next(target):
    if target and target.startswith('/') and not target.startswith('//') and '\\' not in target:
        return target
    return url_for('dashboard.home')


def _wants_json():
    return (
        request.path.startswith('/api/') or
        request.path == '/generate-quiz' or
        request.headers.get('X-Requested-With') == 'XMLHttpRequest' or
        request.is_json
    )


@privacy_bp.before_app_request
def require_current_consent():
    if request.endpoint is None or request.endpoint in GATE_EXEMPT_ENDPOINTS:
        return None
    if not current_user.is_authenticated:
        return None
    if has_current_consent(current_user):
        if current_user.age_group == 'minor' and not has_parent_consent(current_user):
            if _wants_json():
                return jsonify({
                    "success": False,
                    "code": "parental_consent_required",
                    "error": "A parent or guardian must confirm your account before you can continue.",
                }), 403
            return redirect(url_for('privacy.parent_pending'))
        return None
    if _wants_json():
        return jsonify({
            "success": False,
            "code": "consent_required",
            "error": "Please accept the latest Terms and Privacy Policy to continue.",
        }), 403
    return redirect(url_for('privacy.accept_terms', next=request.full_path.rstrip('?')))


@privacy_bp.app_context_processor
def inject_privacy_context():
    cfg = current_app.config
    return {
        'ai_free_tier': not cfg['GEMINI_PAID_TIER'],
        'privacy_contact_email': cfg['PRIVACY_CONTACT_EMAIL'],
        'terms_version': cfg['TERMS_VERSION'],
        'privacy_version': cfg['PRIVACY_VERSION'],
    }


@privacy_bp.route('/privacy')
def privacy_policy():
    return render_template('legal_page.html', page='privacy')


@privacy_bp.route('/terms')
def terms():
    return render_template('legal_page.html', page='terms')


@privacy_bp.route('/subprocessors')
def subprocessors():
    return render_template('subprocessors.html')


@privacy_bp.route('/accept-terms', methods=['GET', 'POST'])
@login_required
def accept_terms():
    target = _safe_next(request.values.get('next'))
    if request.method == 'POST':
        ask_age = current_user.age_group is None
        if request.form.get('accept_terms') != 'on':
            flash('Please accept the Terms and Privacy Policy to continue.', 'danger')
            return render_template('accept_terms.html', next_url=target, ask_age=ask_age), 400
        if ask_age:
            age_group, parent_email, age_error = parse_age_form(request.form, current_user.email)
            if age_error:
                flash(age_error, 'danger')
                return render_template('accept_terms.html', next_url=target, ask_age=True), 400
            current_user.age_group = age_group
            current_user.parent_email = parent_email
        record_consents(current_user.id, source='reacceptance')
        db.session.commit()
        if ask_age and current_user.age_group == 'minor':
            send_parent_request(current_user)
        return redirect(target)
    if has_current_consent(current_user):
        return redirect(target)
    return render_template('accept_terms.html', next_url=target, ask_age=current_user.age_group is None)


@privacy_bp.route('/account/data/export', methods=['POST'])
@login_required
@limiter.limit("5 per hour")
def export_data():
    payload = export_user_data(current_user)
    stamp = datetime.utcnow().strftime('%Y%m%d')
    response = Response(
        json.dumps(payload, ensure_ascii=False, indent=2),
        mimetype='application/json',
    )
    response.headers['Content-Disposition'] = f'attachment; filename="quizpup-data-{stamp}.json"'
    response.headers['Cache-Control'] = 'no-store'
    return response


@privacy_bp.route('/account/delete', methods=['POST'])
@login_required
@limiter.limit("5 per hour")
def delete_account():
    uses_password = (current_user.auth_provider or 'password') == 'password'
    if uses_password:
        supplied = request.form.get('password') or ''
        confirmed = bool(supplied) and check_password_hash(current_user.password_hash, supplied)
    else:
        confirmed = (request.form.get('confirm_text') or '').strip() == 'DELETE'

    if not confirmed:
        flash('We could not confirm it is you. Nothing was deleted.', 'danger')
        return redirect(url_for('profile.profile'))

    user = current_user._get_current_object()
    ok, identity_removed = delete_user_account(user)
    if not ok:
        flash('Something went wrong and your account was not deleted. Please try again.', 'danger')
        return redirect(url_for('profile.profile'))

    logout_user()
    session.clear()
    if identity_removed:
        flash('Your account and data have been deleted.', 'success')
    else:
        flash('Your QuizPup data has been deleted. Removal of your sign-in record is still pending; contact us if it persists.', 'warning')
    return redirect(url_for('auth.index'))


@user_logged_in.connect
def stamp_last_login(sender, user):
    try:
        user.last_login_at = datetime.utcnow()
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Could not record last login time")


def register_privacy_cli(app):
    @app.cli.command('purge-data')
    def purge_data_command():
        result = purge_expired()
        click.echo(json.dumps(result))


@privacy_bp.route('/parent/confirm/<token>', methods=['GET', 'POST'])
@limiter.limit("30 per hour")
def parent_confirm(token):
    user = read_parent_token(token)
    if user is None:
        return render_template('parent_confirm.html', state='invalid'), 400
    if has_parent_consent(user):
        return render_template('parent_confirm.html', state='done', child_name=user.first_name)
    if request.method == 'POST':
        if request.form.get('is_parent') != 'on' or request.form.get('accept_terms') != 'on':
            flash('Please tick both boxes to confirm.', 'danger')
            return render_template('parent_confirm.html', state='form', child_name=user.first_name), 400
        record_parental_consent(user)
        db.session.commit()
        return render_template('parent_confirm.html', state='done', child_name=user.first_name)
    return render_template('parent_confirm.html', state='form', child_name=user.first_name)


@privacy_bp.route('/parent-pending')
@login_required
def parent_pending():
    if current_user.age_group != 'minor' or has_parent_consent(current_user):
        return redirect(url_for('dashboard.home'))
    return render_template('parent_pending.html', parent_email=current_user.parent_email)


@privacy_bp.route('/parent-pending/resend', methods=['POST'])
@login_required
@limiter.limit("5 per hour")
def parent_resend():
    if current_user.age_group == 'minor' and not has_parent_consent(current_user):
        if send_parent_request(current_user):
            flash('We sent the email again.', 'success')
        else:
            flash('We could not send the email right now. Please try again later.', 'danger')
    return redirect(url_for('privacy.parent_pending'))


@privacy_bp.route('/parent-pending/change', methods=['POST'])
@login_required
@limiter.limit("5 per hour")
def parent_change():
    if current_user.age_group != 'minor' or has_parent_consent(current_user):
        return redirect(url_for('dashboard.home'))
    _, parent_email, error = parse_age_form(
        {'age_group': 'minor', 'parent_email': request.form.get('parent_email')},
        current_user.email,
    )
    if error:
        flash(error, 'danger')
        return redirect(url_for('privacy.parent_pending'))
    current_user.parent_email = parent_email
    db.session.commit()
    if send_parent_request(current_user):
        flash('We sent the email to the new address.', 'success')
    else:
        flash('Saved, but we could not send the email right now. Please try again later.', 'danger')
    return redirect(url_for('privacy.parent_pending'))