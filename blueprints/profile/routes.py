from datetime import datetime, timedelta

from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func

from extensions import db, limiter
from models import User
from services.email_service import send_email, OTP_VALID_MINUTES
from services.security_utils import (
    generate_otp, otp_matches, clear_email_change_state, is_valid_email, normalize_email
)

from . import profile_bp


@profile_bp.route('/profile')
@login_required
def profile():
    return render_template('dashboard.html', view="profile")


@profile_bp.route('/profile/edit', methods=['GET'])
@login_required
def edit_profile_page():
    return render_template('dashboard.html', view="edit_profile")


@profile_bp.route('/profile/edit', methods=['POST'])
@login_required
def edit_profile():
    first_name = (request.form.get('first_name') or '').strip()[:100]
    last_name = (request.form.get('last_name') or '').strip()[:100]
    phone = (request.form.get('phone') or '').strip()[:20]

    if not first_name or not last_name:
        flash('First and last name are required.', 'danger')
        return redirect(url_for('profile.edit_profile_page'))

    current_user.first_name = first_name
    current_user.last_name = last_name
    current_user.phone = phone
    db.session.commit()
    flash('Your details have been updated.', 'success')
    return redirect(url_for('profile.profile'))


@profile_bp.route('/profile/email/request-change', methods=['POST'])
@login_required
@limiter.limit("5 per hour")
def request_email_change():
    new_email = normalize_email(request.form.get('new_email'))

    if not is_valid_email(new_email):
        flash('Please enter a valid email address.', 'danger')
        return redirect(url_for('profile.edit_profile_page'))

    if new_email == (current_user.email or '').lower():
        flash('That is already your current email address.', 'danger')
        return redirect(url_for('profile.edit_profile_page'))

    if User.query.filter(func.lower(User.email) == new_email).first():
        flash('That email address is already in use by another account.', 'danger')
        return redirect(url_for('profile.edit_profile_page'))

    otp_code = generate_otp()
    current_user.pending_email = new_email
    current_user.email_change_otp = otp_code
    current_user.email_change_otp_expires = datetime.utcnow() + timedelta(minutes=OTP_VALID_MINUTES)
    db.session.commit()

    send_email(
        new_email,
        "Verify your new QuizPup email address",
        f"<p>Your verification code to confirm this as your new QuizPup email "
        f"address is: <strong>{otp_code}</strong></p>"
    )
    flash(f'A verification code has been sent to your new email ({new_email}).', 'success')
    return redirect(url_for('profile.edit_profile_page'))


@profile_bp.route('/profile/email/verify', methods=['POST'])
@login_required
@limiter.limit("15 per hour")
def verify_email_change():
    submitted_otp = request.form.get('otp')

    if not current_user.pending_email:
        flash('No pending email change found.', 'danger')
        return redirect(url_for('profile.edit_profile_page'))

    expired = (not current_user.email_change_otp_expires) or datetime.utcnow() > current_user.email_change_otp_expires
    if expired:
        flash('That code has expired. Please start the email change again.', 'danger')
        clear_email_change_state(current_user)
        db.session.commit()
        return redirect(url_for('profile.edit_profile_page'))

    if not otp_matches(submitted_otp, current_user.email_change_otp):
        flash('Incorrect code. Please try again.', 'danger')
        return redirect(url_for('profile.edit_profile_page'))

    if User.query.filter(func.lower(User.email) == current_user.pending_email.lower(), User.id != current_user.id).first():
        flash('That email address was just claimed by another account. Please try a different address.', 'danger')
        clear_email_change_state(current_user)
        db.session.commit()
        return redirect(url_for('profile.edit_profile_page'))

    current_user.email = current_user.pending_email
    clear_email_change_state(current_user)
    db.session.commit()
    flash('Your email address has been updated successfully.', 'success')
    return redirect(url_for('profile.profile'))


@profile_bp.route('/profile/email/cancel', methods=['POST'])
@login_required
def cancel_email_change():
    clear_email_change_state(current_user)
    db.session.commit()
    flash('Email change cancelled.', 'success')
    return redirect(url_for('profile.edit_profile_page'))