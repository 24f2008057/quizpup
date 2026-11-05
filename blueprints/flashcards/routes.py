import json
import logging

from flask import request, jsonify
from flask_login import login_required, current_user
from google.genai import types

from extensions import db, limiter
from models import FlashcardDeck
from flashcard_data import PRELOADED_DECKS
from services.gemini_service import call_gemini, GeminiCooldown

from . import flashcards_bp

logger = logging.getLogger("quizpup")


@flashcards_bp.route('/api/flashcards/structure', methods=['GET'])
@login_required
def flashcards_structure():
    exam_target = request.args.get('exam', 'JEE').upper()
    exam_data = PRELOADED_DECKS.get(exam_target, {})

    structure = {}
    for subject, chapters in exam_data.items():
        structure[subject] = [
            {"chapter": chapter_name, "count": len(cards)}
            for chapter_name, cards in chapters.items()
        ]

    return jsonify({"success": True, "exam": exam_target, "subjects": structure})


@flashcards_bp.route('/api/flashcards/preload', methods=['GET'])
@login_required
def preload_flashcards():
    exam_target = request.args.get('exam', 'JEE').upper()
    subject_target = request.args.get('subject', '')
    chapter_target = request.args.get('chapter', '')

    exam_data = PRELOADED_DECKS.get(exam_target, {})
    subject_data = exam_data.get(subject_target, {})
    chapter_deck = subject_data.get(chapter_target, [])

    return jsonify({"success": True, "deck": chapter_deck})


@flashcards_bp.route('/api/flashcards/generate', methods=['POST'])
@login_required
@limiter.limit("20 per hour")
def generate_ai_flashcards():
    data = request.get_json() or {}
    topic = (data.get('topic') or '').strip()
    if not topic:
        return jsonify({"success": False, "error": "Topic argument is completely missing."}), 400
    if len(topic) > 300:
        return jsonify({"success": False, "error": "Topic is too long. Please keep it under 300 characters."}), 400

    fc_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "cards": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "question": types.Schema(type=types.Type.STRING),
                        "answer": types.Schema(type=types.Type.STRING)
                    },
                    required=["question", "answer"]
                )
            )
        },
        required=["cards"]
    )

    prompt = f"""You are building a study deck for a student actively preparing for exams on: '{topic}'.

Generate 18-25 flashcards that cover the highest-value testable concepts.

Card design rules:
- QUESTION side: ask ONE specific thing — a definition, formula, distinction,
  cause/effect, or "why" question. Never ask vague questions like "Tell me about X".
  Phrase as direct recall prompts: "What is…", "State the formula for…",
  "Distinguish X from Y", "Why does… happen?"
- ANSWER side: 1-3 concise sentences. Include the key fact, formula, or
  reasoning. If a formula applies, state it. No filler, no preamble.
- Prioritize concepts students commonly confuse, formulas they must memorize,
  and distinctions between similar ideas.
- Spread coverage across ALL major subtopics — do not cluster on one area.
- Order cards from foundational concepts to advanced ones.
"""
    try:
        response = call_gemini(
            "flashcards",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=fc_schema,
                max_output_tokens=16384
            )
        )
        res_data = json.loads(response.text)
        cards = res_data.get('cards', [])

        saved_deck = FlashcardDeck(
            user_id=current_user.id,
            topic=topic,
            cards_json=json.dumps(cards)
        )
        db.session.add(saved_deck)
        db.session.commit()

        return jsonify({"success": True, "deck": cards, "deck_id": saved_deck.id})
    except GeminiCooldown as cd:
        response = jsonify({"success": False, "error": cd.message, "retry_after": cd.retry_after})
        response.status_code = 429
        response.headers["Retry-After"] = str(cd.retry_after)
        return response
    except Exception:
        logger.exception("AI flashcard generation failed for user %s", current_user.id)
        return jsonify({"success": False, "error": "Could not generate flashcards right now. Please try again shortly."}), 500


@flashcards_bp.route('/api/flashcards/my-decks', methods=['GET'])
@login_required
def my_flashcard_decks():
    decks = FlashcardDeck.query.filter_by(user_id=current_user.id).order_by(FlashcardDeck.created_at.desc()).all()
    return jsonify({
        "success": True,
        "decks": [
            {
                "id": d.id,
                "topic": d.topic,
                "count": len(json.loads(d.cards_json)),
                "created_at": d.created_at.strftime('%b %d, %Y') if d.created_at else ""
            }
            for d in decks
        ]
    })


@flashcards_bp.route('/api/flashcards/my-decks/<int:deck_id>', methods=['GET'])
@login_required
def view_flashcard_deck(deck_id):
    deck = FlashcardDeck.query.filter_by(id=deck_id, user_id=current_user.id).first()
    if not deck:
        return jsonify({"success": False, "error": "Flashcard deck not found."}), 404
    return jsonify({"success": True, "topic": deck.topic, "deck": json.loads(deck.cards_json)})


@flashcards_bp.route('/api/flashcards/my-decks/<int:deck_id>', methods=['DELETE'])
@login_required
def delete_flashcard_deck(deck_id):
    deck = FlashcardDeck.query.filter_by(id=deck_id, user_id=current_user.id).first()
    if not deck:
        return jsonify({"success": False, "error": "Flashcard deck not found."}), 404

    try:
        db.session.delete(deck)
        db.session.commit()
        return jsonify({"success": True, "message": "Flashcard deck deleted successfully"})
    except Exception:
        db.session.rollback()
        logger.exception("Failed to delete flashcard deck %s", deck_id)
        return jsonify({"success": False, "error": "Could not delete flashcard deck."}), 500