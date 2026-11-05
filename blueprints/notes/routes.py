import io
import os
import uuid
import logging
import threading

from flask import request, jsonify, send_file, current_app
from flask_login import login_required, current_user
from werkzeug.utils import secure_filename
from pypdf import PdfReader

from extensions import db, limiter
from models import SuperNote
from services.security_utils import is_allowed_upload_content, detect_upload_mime, ALLOWED_NOTE_EXTENSIONS
from services.supernotes_service import run_supernotes_job, SUPERNOTE_MAX_PDF_PAGES

from . import notes_bp

logger = logging.getLogger("quizpup")


@notes_bp.route('/api/study/process-notes', methods=['POST'])
@login_required
@limiter.limit("15 per hour")
def process_study_notes():
    if 'note_file' not in request.files:
        return jsonify({"success": False, "error": "Payload key mismatch."}), 400

    file = request.files['note_file']
    if file.filename == '':
        return jsonify({"success": False, "error": "Empty filename."}), 400

    filename = secure_filename(file.filename)
    if not filename.lower().endswith(tuple(ALLOWED_NOTE_EXTENSIONS)):
        return jsonify({"success": False, "error": "Unsupported file type. Please upload a PDF, JPG, PNG, or WEBP file."}), 400

    filepath = os.path.join(current_app.config['UPLOAD_FOLDER'], f"{uuid.uuid4().hex}_{filename}")
    is_pdf = filename.lower().endswith('.pdf')

    try:
        file.save(filepath)
    except Exception:
        logger.exception("Failed to save uploaded note file")
        return jsonify({"success": False, "error": "Could not save the uploaded file. Please try again."}), 500

    if not is_allowed_upload_content(filepath, is_pdf):
        os.remove(filepath)
        return jsonify({"success": False, "error": "That file's contents don't match a supported PDF or image format."}), 400

    file_mime_type = detect_upload_mime(filepath)

    if is_pdf:
        try:
            page_count = len(PdfReader(filepath).pages)
        except Exception:
            os.remove(filepath)
            return jsonify({"success": False, "error": "Could not read that PDF. Please try a different file."}), 400
        if page_count > SUPERNOTE_MAX_PDF_PAGES:
            os.remove(filepath)
            return jsonify({"success": False, "error": f"That PDF has {page_count} pages. Please upload {SUPERNOTE_MAX_PDF_PAGES} pages or fewer at a time."}), 400

    note_title = os.path.splitext(filename)[0].replace('_', ' ').replace('-', ' ').strip() or "Untitled Note"
    note = SuperNote(user_id=current_user.id, title=note_title, status="processing")
    db.session.add(note)
    db.session.commit()
    note_id = note.id

    threading.Thread(
        target=run_supernotes_job,
        args=(current_app._get_current_object(), note_id, filepath, filename, is_pdf, file_mime_type),
        daemon=True
    ).start()

    return jsonify({
        "success": True,
        "note_id": note_id,
        "title": note_title,
        "status": "processing"
    })


@notes_bp.route('/api/study/notes/history', methods=['GET'])
@login_required
def supernotes_history():
    notes = SuperNote.query.filter_by(user_id=current_user.id).order_by(SuperNote.created_at.desc()).all()
    return jsonify({
        "success": True,
        "notes": [
            {"id": n.id, "title": n.title, "status": n.status, "created_at": n.created_at.isoformat()}
            for n in notes
        ]
    })


@notes_bp.route('/api/study/notes/<int:note_id>/status', methods=['GET'])
@login_required
def supernotes_status(note_id):
    note = SuperNote.query.filter_by(id=note_id, user_id=current_user.id).first()
    if not note:
        return jsonify({"success": False, "error": "Note not found."}), 404
    return jsonify({"success": True, "status": note.status, "error": note.error_message})


@notes_bp.route('/api/study/notes/<int:note_id>', methods=['GET'])
@login_required
def supernotes_view(note_id):
    note = SuperNote.query.filter_by(id=note_id, user_id=current_user.id).first()
    if not note:
        return jsonify({"success": False, "error": "Note not found."}), 404
    return jsonify({
        "success": True,
        "title": note.title,
        "status": note.status,
        "error": note.error_message,
        "digitized_text": note.digitized_text,
        "summary": note.summary_html,
        "created_at": note.created_at.isoformat()
    })


@notes_bp.route('/api/study/notes/<int:note_id>', methods=['DELETE'])
@login_required
def delete_supernote(note_id):
    note = SuperNote.query.filter_by(id=note_id, user_id=current_user.id).first()
    if not note:
        return jsonify({"success": False, "error": "Note not found."}), 404

    try:
        db.session.delete(note)
        db.session.commit()
        return jsonify({"success": True, "message": "Note deleted successfully"})
    except Exception:
        db.session.rollback()
        logger.exception("Failed to delete supernote %s", note_id)
        return jsonify({"success": False, "error": "Could not delete note."}), 500


@notes_bp.route('/api/study/notes/<int:note_id>/pdf', methods=['GET'])
@login_required
def supernotes_download(note_id):
    note = SuperNote.query.filter_by(id=note_id, user_id=current_user.id).first()
    if not note or note.status != "complete":
        return jsonify({"success": False, "error": "Note not found or not ready yet."}), 404
    safe_name = secure_filename(note.title) or "supernote"
    return send_file(
        io.BytesIO(note.pdf_data),
        mimetype='application/pdf',
        as_attachment=True,
        download_name=f"{safe_name}.pdf"
    )