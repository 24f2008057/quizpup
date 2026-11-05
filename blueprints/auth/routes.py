import logging
import secrets
from datetime import datetime, timedelta

from flask import render_template, request, redirect, url_for, flash, jsonify, session, make_response
from flask_login import login_user, logout_user, current_user, login_required
from werkzeug.security import generate_password_hash, check_password_hash
from flask_limiter.util import get_remote_address
from sqlalchemy import func

from extensions import db, limiter
from models import User
from services.email_service import send_verification_email, send_otp_email, OTP_VALID_MINUTES, OTP_MAX_ATTEMPTS
from services.security_utils import (
    generate_otp, is_password_strong, otp_matches, is_valid_email, normalize_email,
    PASSWORD_REQUIREMENTS_MESSAGE
)
from services.supabase_client import supabase_client

from . import auth_bp

logger = logging.getLogger("quizpup")


_DUMMY_PASSWORD_HASH = generate_password_hash(secrets.token_urlsafe(16), method='scrypt')


def _login_identity():
    return normalize_email(request.form.get('email')) or get_remote_address()


@auth_bp.route('/')
def index():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.home'))
    return render_template('landing.html')


@auth_bp.route('/register', methods=['GET', 'POST'])
@limiter.limit("10 per hour")
def register():
    if request.method == 'POST':
        username = (request.form.get('username') or '').strip()
        password = request.form.get('password')
        first_name = (request.form.get('first_name') or '').strip()
        last_name = (request.form.get('last_name') or '').strip()
        email = normalize_email(request.form.get('email'))
        phone = (request.form.get('phone') or '').strip()

        if not email:
            flash('Email address is required.', 'danger')
            return redirect(url_for('auth.register'))

        if not is_valid_email(email):
            flash('Please enter a valid email address.', 'danger')
            return redirect(url_for('auth.register'))

        if not username or not first_name or not last_name or not phone:
            flash('Please fill in all fields.', 'danger')
            return redirect(url_for('auth.register'))

        if len(username) > 150 or len(first_name) > 100 or len(last_name) > 100 or len(phone) > 20:
            flash('One of the fields you entered is too long.', 'danger')
            return redirect(url_for('auth.register'))

        if not is_password_strong(password):
            flash(PASSWORD_REQUIREMENTS_MESSAGE, 'danger')
            return redirect(url_for('auth.register'))

        if User.query.filter(func.lower(User.username) == username.lower()).first():
            flash('Username already exists.', 'danger')
            return redirect(url_for('auth.register'))
        if User.query.filter(func.lower(User.email) == email).first():
            flash('Email address already registered.', 'danger')
            return redirect(url_for('auth.register'))

        otp_code = generate_otp()
        new_user = User(
            username=username,
            password_hash=generate_password_hash(password, method='scrypt'),
            first_name=first_name,
            last_name=last_name,
            email=email,
            phone=phone,
            is_verified=False,
            verification_otp=otp_code,
            verification_otp_expires=datetime.utcnow() + timedelta(minutes=OTP_VALID_MINUTES),
            verification_attempts=0
        )
        db.session.add(new_user)
        try:
            db.session.commit()
        except Exception:
            db.session.rollback()
            flash('That username or email was just taken by someone else. Please try again.', 'danger')
            return redirect(url_for('auth.register'))

        session['pending_verification_user_id'] = new_user.id

        if send_verification_email(email, otp_code):
            flash('Account created! Check your email for a 6-digit verification code.', 'success')
        else:
            flash('Account created, but we could not send the verification email right now. Tap "Resend code" on the next screen to try again.', 'warning')

        return redirect(url_for('auth.verify_email'))

    return render_template('login.html', action="Register")


@auth_bp.route('/verify-email', methods=['GET', 'POST'])
@limiter.limit("15 per hour")
def verify_email():
    user_id = session.get('pending_verification_user_id')
    if not user_id:
        flash('No pending verification found. Please register or log in.', 'danger')
        return redirect(url_for('auth.login'))

    user = db.session.get(User, user_id)
    if not user:
        session.pop('pending_verification_user_id', None)
        flash('Account not found.', 'danger')
        return redirect(url_for('auth.register'))

    if user.is_verified:
        session.pop('pending_verification_user_id', None)
        flash('Your account is already verified. Please log in.', 'success')
        return redirect(url_for('auth.login'))

    if request.method == 'POST':
        expired = (not user.verification_otp_expires) or datetime.utcnow() > user.verification_otp_expires
        if expired:
            flash('Your verification code expired. Please request a new one.', 'danger')
        elif user.verification_attempts >= OTP_MAX_ATTEMPTS:
            flash('Too many incorrect attempts. Please request a new code.', 'danger')
        else:
            submitted_otp = request.form.get('otp')
            if otp_matches(submitted_otp, user.verification_otp):
                user.is_verified = True
                user.verification_otp = None
                user.verification_otp_expires = None
                user.verification_attempts = 0
                db.session.commit()
                session.pop('pending_verification_user_id', None)
                flash('Email verified! Please log in.', 'success')
                return redirect(url_for('auth.login'))
            else:
                user.verification_attempts += 1
                db.session.commit()
                logger.warning("Failed email verification code for user_id=%s (attempt %s)", user.id, user.verification_attempts)
                remaining = OTP_MAX_ATTEMPTS - user.verification_attempts
                flash(f'Invalid verification code. {remaining} attempt(s) remaining.', 'danger')

    return render_template('verify_email.html', email=user.email)


@auth_bp.route('/verify-email/resend', methods=['POST'])
@limiter.limit("5 per hour")
def resend_verification_email():
    user_id = session.get('pending_verification_user_id')
    if not user_id:
        flash('No pending verification found.', 'danger')
        return redirect(url_for('auth.login'))

    user = db.session.get(User, user_id)
    if not user or user.is_verified:
        return redirect(url_for('auth.login'))

    otp_code = generate_otp()
    user.verification_otp = otp_code
    user.verification_otp_expires = datetime.utcnow() + timedelta(minutes=OTP_VALID_MINUTES)
    user.verification_attempts = 0
    db.session.commit()

    if send_verification_email(user.email, otp_code):
        flash('A new verification code has been sent.', 'success')
    else:
        flash('Could not send the email right now. Please try again shortly.', 'danger')

    return redirect(url_for('auth.verify_email'))


@auth_bp.route('/login', methods=['GET', 'POST'])
@limiter.limit("25 per minute; 50 per hour")
@limiter.limit("10 per hour", key_func=_login_identity, methods=["POST"], deduct_when=lambda response: response.status_code == 200)
def login():
    if current_user.is_authenticated:
        return redirect(url_for('dashboard.home'))

    if request.method == 'POST':
        email = normalize_email(request.form.get('email'))
        password = request.form.get('password') or ''
        user = User.query.filter(func.lower(User.email) == email).first()

        if not user:
            check_password_hash(_DUMMY_PASSWORD_HASH, password)

        if user and check_password_hash(user.password_hash, password):
            if not user.is_verified:
                session['pending_verification_user_id'] = user.id
                flash('Please verify your email before logging in. Check your inbox for the code.', 'warning')
                return redirect(url_for('auth.verify_email'))

            session.permanent = True
            login_user(user, remember=True)
            return redirect(url_for('dashboard.home'))
        else:
            logger.warning("Failed login attempt for user_id=%s", user.id if user else None)
            flash('Invalid email or password.', 'danger')

    return render_template('login.html', action="Login")


@auth_bp.route('/logout')
@login_required
def logout():
    logout_user()
    session.clear()

    response = make_response(redirect(url_for('auth.login')))
    response.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
    response.headers['Pragma'] = 'no-cache'
    response.headers['Expires'] = '0'
    response.delete_cookie('session', path='/')
    response.delete_cookie('remember_token', path='/')

    return response


@auth_bp.route('/auth/callback', methods=['GET'])
def oauth_callback_page():
    return render_template('oauth_callback.html')


@auth_bp.route('/auth/callback', methods=['POST'])
@limiter.limit("20 per hour")
def oauth_callback_exchange():
    data = request.get_json() or {}
    access_token = data.get('access_token')
    if not access_token:
        return jsonify({"success": False, "error": "Missing session token."}), 400

    try:
        auth_response = supabase_client.auth.get_user(access_token)
        supa_user = auth_response.user
    except Exception:
        logger.exception("Supabase token verification failed")
        return jsonify({"success": False, "error": "Could not verify your session. Please try signing in again."}), 401

    if not supa_user or not supa_user.email:
        return jsonify({"success": False, "error": "Could not verify your session. Please try signing in again."}), 401

    try:
        provider = (supa_user.app_metadata or {}).get('provider', 'oauth')
        metadata = supa_user.user_metadata or {}
        supa_uid = supa_user.id
        email = supa_user.email

        email_confirmed = getattr(supa_user, 'email_confirmed_at', None) or getattr(supa_user, 'confirmed_at', None)
        if not email_confirmed:
            logger.warning("OAuth sign-in refused: provider email not confirmed (provider=%s)", provider)
            return jsonify({"success": False, "error": "Your email address has not been verified with the sign-in provider."}), 403

        email = normalize_email(email)
        existing = User.query.filter_by(supabase_uid=supa_uid).first()
        if not existing:
            existing = User.query.filter(func.lower(User.email) == email).first()
            if existing:
                if existing.supabase_uid and existing.supabase_uid != supa_uid:
                    logger.warning("OAuth sign-in refused: identity mismatch for user_id=%s", existing.id)
                    return jsonify({"success": False, "error": "This email is already linked to a different sign-in identity."}), 403
                existing.supabase_uid = supa_uid
                if not existing.is_verified:
                    existing.password_hash = generate_password_hash(secrets.token_urlsafe(32), method='scrypt')
                    existing.is_verified = True
                    existing.verification_otp = None
                    existing.verification_otp_expires = None
                    existing.verification_attempts = 0
                db.session.commit()

        if existing:
            login_user(existing, remember=True)
            return jsonify({"success": True, "redirect": url_for('dashboard.home')})

        full_name = metadata.get('full_name') or metadata.get('name') or ''
        name_parts = full_name.strip().split(' ', 1)
        session['pending_oauth'] = {
            "supabase_uid": supa_uid,
            "email": email,
            "provider": provider,
            "first_name": metadata.get('given_name') or (name_parts[0] if name_parts else ''),
            "last_name": metadata.get('family_name') or (name_parts[1] if len(name_parts) > 1 else ''),
            "suggested_username": metadata.get('user_name') or metadata.get('preferred_username') or ''
        }
        return jsonify({"success": True, "redirect": url_for('auth.complete_oauth_profile_page')})
    except Exception:
        logger.exception("OAuth callback failed while linking/creating the local user record")
        return jsonify({"success": False, "error": "Something went wrong finishing sign-in. Please try again."}), 500


@auth_bp.route('/auth/complete-profile', methods=['GET'])
def complete_oauth_profile_page():
    pending = session.get('pending_oauth')
    if not pending:
        flash('Please sign in again to continue.', 'error')
        return redirect(url_for('auth.login'))
    return render_template('complete_profile.html', pending=pending)


@auth_bp.route('/auth/complete-profile', methods=['POST'])
@limiter.limit("10 per hour")
def complete_oauth_profile_submit():
    pending = session.get('pending_oauth')
    if not pending:
        flash('Please sign in again to continue.', 'error')
        return redirect(url_for('auth.login'))

    username = (request.form.get('username') or '').strip()
    first_name = (request.form.get('first_name') or '').strip()[:100]
    last_name = (request.form.get('last_name') or '').strip()[:100]
    phone = (request.form.get('phone') or '').strip()[:20]

    if not username or not first_name or not last_name or not phone:
        flash('Please fill in all fields to finish setting up your account.', 'error')
        return render_template('complete_profile.html', pending=pending)

    if len(username) > 150:
        flash('That username is too long.', 'error')
        return render_template('complete_profile.html', pending=pending)

    if User.query.filter(func.lower(User.username) == username.lower()).first():
        flash('That username is already taken. Please choose another.', 'error')
        return render_template('complete_profile.html', pending=pending)

    if User.query.filter(func.lower(User.email) == normalize_email(pending['email'])).first():
        session.pop('pending_oauth', None)
        flash('An account with this email already exists. Please sign in again.', 'error')
        return redirect(url_for('auth.login'))

    new_user = User(
        username=username,
        password_hash=generate_password_hash(secrets.token_urlsafe(32)),
        first_name=first_name,
        last_name=last_name,
        email=normalize_email(pending['email']),
        phone=phone,
        is_verified=True,
        supabase_uid=pending['supabase_uid'],
        auth_provider=pending['provider']
    )
    db.session.add(new_user)
    db.session.commit()

    session.pop('pending_oauth', None)
    login_user(new_user, remember=True)
    return redirect(url_for('dashboard.home'))


@auth_bp.route('/ping', methods=['GET', 'HEAD'])
@limiter.exempt
def ping_server():
    return "OK", 200


@auth_bp.route('/forgot-password', methods=['GET', 'POST'])
@limiter.limit("5 per hour")
def forgot_password():
    if request.method == 'POST':
        email = normalize_email(request.form.get('email'))
        user = User.query.filter(func.lower(User.email) == email).first() if email else None

        if user:
            otp_code = generate_otp()
            user.verification_otp = otp_code
            user.verification_otp_expires = datetime.utcnow() + timedelta(minutes=OTP_VALID_MINUTES)
            user.password_reset_attempts = 0
            db.session.commit()
            send_otp_email(user.email, otp_code)

        session['reset_password_email'] = email
        flash('If an account exists for that email address, a verification code has been sent.', 'success')
        return redirect(url_for('auth.verify_otp'))

    return render_template('forgot_password.html')


@auth_bp.route('/verify-otp', methods=['GET', 'POST'])
@limiter.limit("15 per hour")
def verify_otp():
    email = session.get('reset_password_email')
    if not email:
        return redirect(url_for('auth.forgot_password'))

    user = User.query.filter(func.lower(User.email) == normalize_email(email)).first()

    if request.method == 'POST':
        submitted_otp = request.form.get('otp')
        new_password = request.form.get('new_password')

        if not submitted_otp or not new_password:
            flash('Both the verification code and chosen password fields are required.', 'danger')
            return render_template('verify_otp.html', email=email)

        if not is_password_strong(new_password):
            flash(PASSWORD_REQUIREMENTS_MESSAGE, 'danger')
            return render_template('verify_otp.html', email=email)

        if not user:
            flash('Invalid or expired recovery code.', 'danger')
            return render_template('verify_otp.html', email=email)

        expired = (not user.verification_otp_expires) or datetime.utcnow() > user.verification_otp_expires
        if expired:
            flash('Your verification code expired. Please request a new one.', 'danger')
        elif user.password_reset_attempts >= OTP_MAX_ATTEMPTS:
            flash('Too many incorrect attempts. Please request a new code.', 'danger')
        elif otp_matches(submitted_otp, user.verification_otp):
            user.password_hash = generate_password_hash(new_password, method='scrypt')
            user.verification_otp = None
            user.verification_otp_expires = None
            user.password_reset_attempts = 0
            db.session.commit()

            session.pop('reset_password_email', None)
            flash('Your account security password has been updated successfully. Please login.', 'success')
            return redirect(url_for('auth.login'))
        else:
            user.password_reset_attempts += 1
            db.session.commit()
            logger.warning("Failed password reset code for user_id=%s (attempt %s)", user.id, user.password_reset_attempts)
            flash('Invalid or expired recovery code.', 'danger')

    return render_template('verify_otp.html', email=email)