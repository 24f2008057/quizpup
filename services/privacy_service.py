import json
import logging
from datetime import datetime, timedelta

from flask import current_app

from extensions import db
from models import User, ConsentRecord, QuizSession, QuizQuestion, FlashcardDeck, SuperNote

logger = logging.getLogger("quizpup")

REQUIRED_CONSENTS = ("terms", "privacy")


def current_versions():
    return {
        "terms": current_app.config["TERMS_VERSION"],
        "privacy": current_app.config["PRIVACY_VERSION"],
    }


def record_consents(user_id, source, types=REQUIRED_CONSENTS, granted=True):
    versions = current_versions()
    for consent_type in types:
        db.session.add(ConsentRecord(
            user_id=user_id,
            consent_type=consent_type,
            granted=granted,
            policy_version=versions[consent_type],
            source=source,
        ))


def has_current_consent(user):
    versions = current_versions()
    for consent_type in REQUIRED_CONSENTS:
        latest = (
            ConsentRecord.query
            .filter_by(user_id=user.id, consent_type=consent_type)
            .order_by(ConsentRecord.id.desc())
            .first()
        )
        if not latest or not latest.granted or latest.policy_version != versions[consent_type]:
            return False
    return True


def has_parent_consent(user):
    if user.age_group != "minor":
        return True
    latest = (
        ConsentRecord.query
        .filter_by(user_id=user.id, consent_type="parental")
        .order_by(ConsentRecord.id.desc())
        .first()
    )
    return bool(
        latest
        and latest.granted
        and latest.parent_email == user.parent_email
        and latest.policy_version == current_versions()["terms"]
    )


def record_parental_consent(user, source="parent_link"):
    db.session.add(ConsentRecord(
        user_id=user.id,
        consent_type="parental",
        granted=True,
        policy_version=current_versions()["terms"],
        source=source,
        parent_email=user.parent_email,
    ))


def _iso(value):
    return value.isoformat() + "Z" if value else None


def export_user_data(user):
    sessions = QuizSession.query.filter_by(user_id=user.id).order_by(QuizSession.id).all()
    quiz_data = []
    for quiz in sessions:
        questions = QuizQuestion.query.filter_by(session_id=quiz.id).order_by(QuizQuestion.id).all()
        quiz_data.append({
            "topic": quiz.topic,
            "difficulty": quiz.difficulty,
            "total_questions": quiz.total_questions,
            "duration_minutes": quiz.duration_minutes,
            "score": quiz.score,
            "completed": bool(quiz.is_completed),
            "recommendation": quiz.recommendation,
            "created_at": _iso(quiz.created_at),
            "questions": [
                {
                    "question": q.question_text,
                    "options": [q.option_1, q.option_2, q.option_3, q.option_4],
                    "correct_option": q.correct_option,
                    "your_answer": q.user_answer,
                    "hint_used": bool(q.hint_requested),
                    "explanation": q.explanation,
                }
                for q in questions
            ],
        })

    decks = []
    for deck in FlashcardDeck.query.filter_by(user_id=user.id).order_by(FlashcardDeck.id).all():
        try:
            cards = json.loads(deck.cards_json)
        except (TypeError, ValueError):
            cards = deck.cards_json
        decks.append({"topic": deck.topic, "created_at": _iso(deck.created_at), "cards": cards})

    notes = [
        {
            "title": note.title,
            "status": note.status,
            "extracted_text": note.digitized_text,
            "summary_html": note.summary_html,
            "has_generated_pdf": note.pdf_data is not None,
            "created_at": _iso(note.created_at),
        }
        for note in SuperNote.query.filter_by(user_id=user.id).order_by(SuperNote.id).all()
    ]

    consents = [
        {
            "type": c.consent_type,
            "granted": c.granted,
            "policy_version": c.policy_version,
            "source": c.source,
            "parent_email": c.parent_email,
            "recorded_at": _iso(c.created_at),
        }
        for c in ConsentRecord.query.filter_by(user_id=user.id).order_by(ConsentRecord.id).all()
    ]

    return {
        "exported_at": _iso(datetime.utcnow()),
        "account": {
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "email": user.email,
            "phone": user.phone,
            "sign_in_method": user.auth_provider,
            "age_group": user.age_group,
            "parent_or_guardian_email": user.parent_email,
            "plan": user.subscription_status,
            "email_verified": bool(user.is_verified),
            "created_at": _iso(user.created_at),
            "last_login_at": _iso(user.last_login_at),
        },
        "consents": consents,
        "quizzes": quiz_data,
        "flashcard_decks": decks,
        "notes": notes,
    }


def _delete_supabase_identity(supabase_uid):
    if not supabase_uid:
        return True
    try:
        from services.supabase_client import supabase_client
        supabase_client.auth.admin.delete_user(supabase_uid)
        return True
    except Exception:
        logger.exception("Could not delete the Supabase auth identity during account deletion")
        return False


def delete_user_account(user):
    user_id = user.id
    supabase_uid = user.supabase_uid
    try:
        for quiz in QuizSession.query.filter_by(user_id=user_id).all():
            db.session.delete(quiz)
        FlashcardDeck.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        SuperNote.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        ConsentRecord.query.filter_by(user_id=user_id).delete(synchronize_session=False)
        db.session.delete(user)
        db.session.commit()
    except Exception:
        db.session.rollback()
        logger.exception("Account deletion failed for user_id=%s", user_id)
        return False, False
    identity_removed = _delete_supabase_identity(supabase_uid)
    logger.info("Account deleted for user_id=%s", user_id)
    return True, identity_removed


def purge_expired(now=None):
    now = now or datetime.utcnow()
    cfg = current_app.config
    unverified_cutoff = now - timedelta(days=cfg["UNVERIFIED_ACCOUNT_DAYS"])
    inactive_cutoff = now - timedelta(days=cfg["INACTIVE_ACCOUNT_DAYS"])

    stale_unverified = User.query.filter(
        User.is_verified.is_(False),
        User.created_at.isnot(None),
        User.created_at < unverified_cutoff,
    ).all()

    inactive = User.query.filter(
        User.is_verified.is_(True),
        User.last_login_at.isnot(None),
        User.last_login_at < inactive_cutoff,
    ).all()

    removed_unverified = 0
    for user in stale_unverified:
        ok, _ = delete_user_account(user)
        removed_unverified += 1 if ok else 0

    removed_inactive = 0
    for user in inactive:
        ok, _ = delete_user_account(user)
        removed_inactive += 1 if ok else 0

    expired_otps = User.query.filter(
        User.verification_otp_expires.isnot(None),
        User.verification_otp_expires < now,
    ).all()
    for user in expired_otps:
        user.verification_otp = None
        user.verification_otp_expires = None
    email_otps = User.query.filter(
        User.email_change_otp_expires.isnot(None),
        User.email_change_otp_expires < now,
    ).all()
    for user in email_otps:
        user.pending_email = None
        user.email_change_otp = None
        user.email_change_otp_expires = None
    db.session.commit()

    return {
        "unverified_accounts_removed": removed_unverified,
        "inactive_accounts_removed": removed_inactive,
        "expired_codes_cleared": len(expired_otps) + len(email_otps),
    }