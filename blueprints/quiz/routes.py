import os
import re
import json
import math
import uuid
import logging
import concurrent.futures

from flask import request, jsonify, render_template, redirect, url_for, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from pypdf import PdfReader
from google.genai import types

from extensions import db, limiter
from models import QuizSession, QuizQuestion
from services.gemini_service import call_gemini, quiz_task, GeminiCooldown, MAX_PARALLEL
from services.security_utils import is_allowed_upload_content

from . import quiz_bp

logger = logging.getLogger("quizpup")

QUIZ_SOURCE_MAX_PDF_PAGES = 200
QUIZ_SOURCE_MAX_CHARS = 40000


@quiz_bp.route('/generate-quiz', methods=['POST'])
@login_required
@limiter.limit("20 per hour")
def generate_quiz():
    topic = (request.form.get('topic') or '').strip()
    difficulty = request.form.get('difficulty', 'easy')
    try:
        total_q = int(request.form.get('total_questions', 10))
        duration = int(request.form.get('duration', 10))
    except (TypeError, ValueError):
        return jsonify({"error": "Questions and duration must be numbers."}), 400

    if total_q < 10 or total_q > 200:
        return jsonify({"error": "Questions must stay between 10 and 200 items."}), 400
    if duration < 1 or duration > 300:
        return jsonify({"error": "Duration must be between 1 and 300 minutes."}), 400
    if len(topic) > 300:
        return jsonify({"error": "Topic is too long. Please keep it under 300 characters."}), 400
    has_pdf_upload = 'pdf_file' in request.files and request.files['pdf_file'].filename != ''
    if not topic and not has_pdf_upload:
        return jsonify({"error": "Please provide a topic or upload a source PDF."}), 400

    source_text = ""
    if 'pdf_file' in request.files and request.files['pdf_file'].filename != '':
        file = request.files['pdf_file']
        if not file.filename.lower().endswith('.pdf'):
            return jsonify({"error": "Please upload a PDF file."}), 400
        safe_name = secure_filename(file.filename) or "upload.pdf"
        filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], f"{uuid.uuid4().hex}_{safe_name}")
        file.save(filepath)
        if not is_allowed_upload_content(filepath, expect_pdf=True):
            os.remove(filepath)
            return jsonify({"error": "That file's contents don't match a valid PDF."}), 400
        try:
            reader = PdfReader(filepath)
            if len(reader.pages) > QUIZ_SOURCE_MAX_PDF_PAGES:
                os.remove(filepath)
                return jsonify({"error": f"That PDF has {len(reader.pages)} pages. Please upload {QUIZ_SOURCE_MAX_PDF_PAGES} pages or fewer."}), 400
            for page in reader.pages:
                source_text += page.extract_text() or ""
                if len(source_text) >= QUIZ_SOURCE_MAX_CHARS:
                    break
            os.remove(filepath)
        except Exception:
            logger.exception("Failed to parse uploaded quiz-source PDF")
            if os.path.exists(filepath):
                os.remove(filepath)
            return jsonify({"error": "Could not read that PDF. Please try a different file."}), 400

    if difficulty == 'easy':
        hints_allowed = -1
    elif difficulty == 'medium':
        hints_allowed = math.floor(total_q * 0.3)
    else:
        hints_allowed = 0

    task = quiz_task(difficulty, len(source_text))
    context_source = f"Text Source Material Context: {source_text[:40000]}" if source_text else f"Topic: {topic}"

    batch_max = 60
    max_topup_rounds = 2

    quiz_schema = types.Schema(
        type=types.Type.OBJECT,
        properties={
            "questions": types.Schema(
                type=types.Type.ARRAY,
                items=types.Schema(
                    type=types.Type.OBJECT,
                    properties={
                        "question": types.Schema(type=types.Type.STRING),
                        "opt1": types.Schema(type=types.Type.STRING),
                        "opt2": types.Schema(type=types.Type.STRING),
                        "opt3": types.Schema(type=types.Type.STRING),
                        "opt4": types.Schema(type=types.Type.STRING),
                        "correct": types.Schema(type=types.Type.INTEGER, description="Correct option number: 1, 2, 3, or 4"),
                        "hint": types.Schema(type=types.Type.STRING),
                        "explanation": types.Schema(type=types.Type.STRING)
                    },
                    required=["question", "opt1", "opt2", "opt3", "opt4", "correct", "hint", "explanation"]
                )
            )
        },
        required=["questions"]
    )

    difficulty_guide = {
        "easy": "Test direct recall and basic understanding. Questions should have one clearly correct answer and three obviously wrong distractors. Suitable for beginners reviewing fundamentals.",
        "medium": "Test application and analysis. Require applying concepts to scenarios or distinguishing between similar ideas. Distractors should be plausible but distinguishable with solid understanding.",
        "hard": "Test synthesis and evaluation. Use multi-step reasoning, edge cases, and subtle distinctions. All four options should seem plausible — only deep understanding separates the correct answer.",
        "god mode": "Test at competitive exam / graduate-entrance level. Combine multiple concepts per question, use tricky edge cases, require precise technical knowledge. Distractors must be expertly crafted near-misses that would fool anyone who doesn't know the material cold."
    }

    def split_targets(n):
        n_batches = math.ceil(n / batch_max)
        base, extra = divmod(n, n_batches)
        return [base + (1 if i < extra else 0) for i in range(n_batches)]

    def normalize_stem(text):
        return re.sub(r'\W+', ' ', str(text).lower()).strip()

    def is_valid_question(q):
        try:
            if not isinstance(q, dict):
                return False
            for key in ('question', 'opt1', 'opt2', 'opt3', 'opt4', 'hint', 'explanation'):
                if not str(q.get(key, '')).strip():
                    return False
            return int(q.get('correct')) in (1, 2, 3, 4)
        except (TypeError, ValueError):
            return False

    def generate_batch(batch_index, batches_total, count, avoid_stems, call_task):
        avoid_block = ""
        if avoid_stems:
            listed = "\n".join(f"- {stem[:120]}" for stem in avoid_stems[-150:])
            avoid_block = f"\nThese questions already exist. Do NOT repeat or rephrase any of them:\n{listed}\n"

        prompt = f"""You are an expert exam question writer creating a multiple-choice quiz.
{context_source}

Generate exactly {count} unique questions. Batch {batch_index+1} of {batches_total}.
Other batches are written in parallel, so concentrate on the {batch_index+1}/{batches_total} slice of this subject's subtopics to avoid overlap.
{avoid_block}
Difficulty: {difficulty}
{difficulty_guide.get(difficulty, difficulty_guide['medium'])}

Question design rules:
1. Each question stem must be a complete, self-contained question — never "Which of the following…" without context.
2. All four options must be similar in length and grammatical structure.
3. Place the correct answer at a RANDOM position (1-4) — do NOT default to any position.
4. Distractors must be plausible wrong answers from the same domain, not absurd fillers.
5. No two questions should test the exact same fact. Spread coverage across all subtopics.
6. "hint": A short nudge that points TOWARD the right reasoning without revealing the answer (e.g. "Think about what happens when pressure increases" not "The answer is Boyle's Law").
7. "explanation": After the student answers, explain WHY the correct answer is right AND why the most tempting wrong answer is wrong. Max 2 sentences.
"""
        response = call_gemini(
            call_task,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                response_schema=quiz_schema,
                max_output_tokens=32768
            )
        )
        batch_data = json.loads(response.text)
        return batch_data.get('questions', [])

    cooldowns = []

    def run_batches(targets, avoid_stems, call_task):
        results = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(targets), MAX_PARALLEL)) as executor:
            future_to_index = {
                executor.submit(generate_batch, i, len(targets), targets[i], avoid_stems, call_task): i
                for i in range(len(targets))
            }
            for future in concurrent.futures.as_completed(future_to_index):
                idx = future_to_index[future]
                try:
                    results[idx] = future.result()
                except GeminiCooldown as cd:
                    cooldowns.append(cd)
                    results[idx] = []
                except Exception:
                    logger.exception("Quiz batch %s failed for user %s", idx, current_user.id)
                    results[idx] = []
        ordered = []
        for i in range(len(targets)):
            ordered.extend(results.get(i, []))
        return ordered

    try:
        collected = []
        seen_stems = set()

        def absorb(batch_questions):
            for q in batch_questions:
                if not is_valid_question(q):
                    continue
                key = normalize_stem(q['question'])
                if key in seen_stems:
                    continue
                seen_stems.add(key)
                collected.append(q)

        first_pass_total = total_q + max(2, math.ceil(total_q * 0.1))
        topup_task = quiz_task("hard") if task == "quiz_god" else task

        absorb(run_batches(split_targets(first_pass_total), [], task))

        for _ in range(max_topup_rounds):
            shortfall = total_q - len(collected)
            if shortfall <= 0:
                break
            if cooldowns:
                raise max(cooldowns, key=lambda cd: cd.retry_after)
            # Request exactly the shortfall + a tight 10% safety buffer (min 2)
            # so we don't waste RPM/RPD on questions we'll immediately discard.
            ask = shortfall + max(2, math.ceil(shortfall * 0.1))
            absorb(run_batches(split_targets(ask), [q['question'] for q in collected], topup_task))

        if len(collected) < total_q and cooldowns:
            raise max(cooldowns, key=lambda cd: cd.retry_after)

        if not collected:
            raise RuntimeError("No valid questions were generated")

        if len(collected) < total_q:
            logger.warning("Quiz short after top-ups: wanted %s, got %s", total_q, len(collected))

        all_generated_questions = collected[:total_q]

        quiz_session_instance = QuizSession(
            user_id=current_user.id,
            topic=topic if topic else "Uploaded Document Reference Workspace",
            difficulty=difficulty,
            total_questions=len(all_generated_questions),
            duration_minutes=duration,
            seconds_remaining=duration * 60,
            hints_remaining=hints_allowed
        )
        db.session.add(quiz_session_instance)
        db.session.commit()

        for q in all_generated_questions:
            question_entry = QuizQuestion(
                session_id=quiz_session_instance.id,
                question_text=q['question'],
                option_1=q['opt1'],
                option_2=q['opt2'],
                option_3=q['opt3'],
                option_4=q['opt4'],
                correct_option=int(q['correct']),
                hint=q['hint'],
                explanation=q['explanation']
            )
            db.session.add(question_entry)
        db.session.commit()

        return jsonify({"success": True, "session_id": quiz_session_instance.id})
    except GeminiCooldown as cd:
        response = jsonify({"error": cd.message, "retry_after": cd.retry_after})
        response.status_code = 429
        response.headers["Retry-After"] = str(cd.retry_after)
        return response
    except Exception:
        logger.exception("Quiz generation failed for user %s", current_user.id)
        return jsonify({"error": "Could not generate the quiz right now. Please try again shortly."}), 500


@quiz_bp.route('/quiz/<int:session_id>')
@login_required
def run_quiz(session_id):
    session_instance = QuizSession.query.get_or_404(session_id)
    if session_instance.user_id != current_user.id:
        return redirect(url_for('dashboard.home'))
    return render_template('quiz.html', session=session_instance, view='quiz', hide_bottom_nav=True)


@quiz_bp.route('/api/quiz/<int:session_id>/questions')
@login_required
def get_quiz_questions(session_id):
    session_instance = QuizSession.query.get_or_404(session_id)
    if session_instance.user_id != current_user.id:
        return jsonify({"error": "Not authorized"}), 403

    questions = [{
        "id": q.id,
        "question": q.question_text,
        "1": q.option_1,
        "2": q.option_2,
        "3": q.option_3,
        "4": q.option_4
    } for q in session_instance.questions]

    explanations_map = {}
    saved_answers_map = {q.id: q.user_answer for q in session_instance.questions if q.user_answer is not None}
    if session_instance.is_completed:
        for q in session_instance.questions:
            explanations_map[q.id] = {"correct": q.correct_option, "explanation": q.explanation}

    return jsonify({
        "duration": session_instance.duration_minutes,
        "seconds_remaining": session_instance.seconds_remaining if session_instance.seconds_remaining is not None else session_instance.duration_minutes * 60,
        "difficulty": session_instance.difficulty,
        "hints_remaining": session_instance.hints_remaining,
        "is_completed": session_instance.is_completed,
        "score": session_instance.score,
        "total": session_instance.total_questions,
        "recommendation": session_instance.recommendation,
        "questions": questions,
        "saved_answers": saved_answers_map,
        "explanations": explanations_map
    })


@quiz_bp.route('/api/quiz/<int:session_id>/submit', methods=['POST'])
@login_required
def submit_quiz(session_id):
    session_instance = QuizSession.query.get_or_404(session_id)
    if session_instance.user_id != current_user.id:
        return jsonify({"error": "Not authorized"}), 403

    if session_instance.is_completed:
        return jsonify({"score": session_instance.score, "total": session_instance.total_questions, "recommendation": session_instance.recommendation})

    data = request.json.get('answers', {})
    score = 0
    for q in session_instance.questions:
        user_ans = data.get(str(q.id))
        try:
            user_ans_int = int(user_ans) if user_ans is not None else None
        except (TypeError, ValueError):
            user_ans_int = None
        q.user_answer = user_ans_int
        if user_ans_int == q.correct_option:
            score += 1

    session_instance.score = score
    session_instance.is_completed = True
    session_instance.recommendation = "Review completed targets to fortify foundational metrics."
    db.session.commit()

    return jsonify({"score": score, "total": session_instance.total_questions, "recommendation": session_instance.recommendation})


@quiz_bp.route('/api/quiz/<int:session_id>/save-progress', methods=['POST'])
@login_required
def save_quiz_progress(session_id):
    session_instance = QuizSession.query.get_or_404(session_id)
    if session_instance.user_id != current_user.id:
        return jsonify({"error": "Not authorized"}), 403

    if session_instance.is_completed:
        return jsonify({"success": True, "message": "Quiz already completed"})

    data = request.get_json(silent=True)
    if data is None:
        try:
            data = json.loads(request.form.get('payload', '{}'))
        except (TypeError, ValueError):
            data = {}
    if not isinstance(data, dict):
        data = {}
    answers = data.get('answers', {})
    if not isinstance(answers, dict):
        answers = {}
    seconds_left = data.get('seconds_remaining')

    if seconds_left is not None:
        try:
            session_instance.seconds_remaining = int(seconds_left)
        except (TypeError, ValueError):
            pass

    for q in session_instance.questions:
        if str(q.id) in answers:
            ans_val = answers[str(q.id)]
            try:
                q.user_answer = int(ans_val) if ans_val is not None else None
            except (TypeError, ValueError):
                q.user_answer = None

    db.session.commit()
    return jsonify({"success": True})


@quiz_bp.route('/api/quiz/<int:session_id>/hint/<int:question_id>', methods=['POST'])
@login_required
def get_quiz_hint(session_id, question_id):
    session_instance = QuizSession.query.get_or_404(session_id)
    if session_instance.user_id != current_user.id:
        return jsonify({"error": "Not authorized"}), 403

    if session_instance.difficulty not in ('easy', 'medium'):
        return jsonify({"error": f"Hints are disabled for {session_instance.difficulty} mode."}), 400

    question = QuizQuestion.query.filter_by(id=question_id, session_id=session_id).first_or_404()

    if not getattr(question, 'hint_requested', False):
        if session_instance.difficulty == 'medium':
            if session_instance.hints_remaining <= 0:
                return jsonify({"error": "No hints remaining!"}), 400
            session_instance.hints_remaining -= 1
        question.hint_requested = True
        db.session.commit()

    return jsonify({
        "hint": question.hint or "No hint available for this question.",
        "hints_remaining": session_instance.hints_remaining
    })


@quiz_bp.route('/api/quiz/<int:session_id>', methods=['DELETE'])
@login_required
def delete_quiz_session(session_id):
    try:
        session_instance = QuizSession.query.get_or_404(session_id)
        if session_instance.user_id != current_user.id:
            return jsonify({"error": "Not authorized"}), 403

        QuizQuestion.query.filter_by(session_id=session_id).delete(synchronize_session=False)
        db.session.delete(session_instance)
        db.session.commit()

        return jsonify({"success": True, "message": "Quiz deleted successfully"})
    except Exception:
        logger.exception("Failed to delete quiz session %s", session_id)
        db.session.rollback()
        return jsonify({"error": "Could not delete quiz session."}), 500