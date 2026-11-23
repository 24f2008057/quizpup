import io
import os
import math
import json
import logging
import concurrent.futures

from pypdf import PdfReader, PdfWriter
from google.genai import types

from extensions import db
from models import SuperNote
from services import credits
from services.gemini_service import call_gemini, GeminiCooldown, BACKGROUND_MAX_WAIT, MAX_PARALLEL
from services.pdf_service import render_summary_html, generate_pdf_bytes

logger = logging.getLogger("quizpup")

SUPERNOTE_CHUNK_CHAR_LIMIT = 8000
SUPERNOTE_MAX_PDF_PAGES = 60

SUMMARY_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "topics": types.Schema(
            type=types.Type.ARRAY,
            items=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "heading": types.Schema(type=types.Type.STRING),
                    "bullets": types.Schema(
                        type=types.Type.ARRAY,
                        items=types.Schema(type=types.Type.STRING)
                    )
                },
                required=["heading", "bullets"]
            )
        )
    },
    required=["topics"]
)


def split_into_segments(text, max_chars=SUPERNOTE_CHUNK_CHAR_LIMIT):
    if len(text) <= max_chars:
        return [(None, text)]
    n_parts = math.ceil(len(text) / max_chars)
    part_size = math.ceil(len(text) / n_parts)
    return [(f"Part {i + 1}", text[i * part_size: (i + 1) * part_size]) for i in range(n_parts)]


def summarize_segment(label, content, retry_tokens=None):
    source_words = len(content.split())
    target_words = max(35, round(source_words * 0.175))
    prompt = f"""
    Create concise study notes from the text segment below. Identify only the
    MOST IMPORTANT topics covered — this is a compressed study aid, not a full
    rewrite, so skip minor or repetitive details entirely if needed.

    Text segment:
    {content}

    Strict rules:
    1. The combined bullets across all topics must total approximately
       {target_words} words — roughly 15-20% of the source length. Do not pad
       or significantly exceed this.
    2. Give each distinct topic you keep a short heading, with a tight list of
       bullets, each bullet under 15 words.
    3. Include only the highest-yield concepts, definitions, formulas, and facts.
       Do not restate the source verbatim, include examples/filler, or repeat points.
    """
    token_budget = retry_tokens or max(8000, target_words * 8)

    response = call_gemini(
        "summary",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=SUMMARY_SCHEMA,
            max_output_tokens=token_budget
        ),
        max_wait=BACKGROUND_MAX_WAIT
    )

    finish_reason = None
    if response.candidates:
        finish_reason = str(response.candidates[0].finish_reason)

    if "MAX_TOKENS" in (finish_reason or "") and retry_tokens is None:
        return summarize_segment(label, content, retry_tokens=token_budget * 2)

    parsed = json.loads(response.text)
    return {"chapter": label, "topics": parsed.get("topics", [])}


def batch_ocr_pdf(filepath, page_count, batch_size=15):
    reader = PdfReader(filepath)
    batch_ranges = [(start, min(start + batch_size, page_count)) for start in range(0, page_count, batch_size)]

    batch_byte_chunks = []
    for start, end in batch_ranges:
        writer = PdfWriter()
        for i in range(start, end):
            writer.add_page(reader.pages[i])
        buffer = io.BytesIO()
        writer.write(buffer)
        batch_byte_chunks.append(buffer.getvalue())

    def ocr_one_batch(idx, start, end):
        ocr_prompt = f"Perform accurate OCR transcription on these scanned handwritten note pages (pages {start+1}-{end})."
        response = call_gemini(
            "ocr",
            contents=[
                types.Part.from_bytes(data=batch_byte_chunks[idx], mime_type="application/pdf"),
                ocr_prompt
            ],
            max_wait=BACKGROUND_MAX_WAIT
        )
        return response.text

    results_by_index = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(batch_ranges), MAX_PARALLEL)) as executor:
        future_to_index = {
            executor.submit(ocr_one_batch, idx, start, end): idx
            for idx, (start, end) in enumerate(batch_ranges)
        }
        for future in concurrent.futures.as_completed(future_to_index):
            idx = future_to_index[future]
            results_by_index[idx] = future.result()

    return "\n\n".join(results_by_index[i] for i in range(len(batch_ranges)))


def run_supernotes_job(app_instance, note_id, filepath, filename, is_pdf, file_mime_type):
    with app_instance.app_context():
        note = SuperNote.query.get(note_id)
        try:
            extracted_text = ""

            if is_pdf:
                reader = PdfReader(filepath)
                for page in reader.pages:
                    extracted_text += page.extract_text() or ""

                page_count = len(reader.pages)
                avg_chars_per_page = len(extracted_text.strip()) / max(page_count, 1)
                looks_scanned = avg_chars_per_page < 40

                if looks_scanned:
                    extracted_text = batch_ocr_pdf(filepath, page_count)
            else:
                with open(filepath, 'rb') as f:
                    img_bytes = f.read()

                ocr_prompt = "Perform full digital text transcription from this photographic note document."
                response = call_gemini(
                    "ocr",
                    contents=[
                        types.Part.from_bytes(data=img_bytes, mime_type=file_mime_type or "image/jpeg"),
                        ocr_prompt
                    ],
                    max_wait=BACKGROUND_MAX_WAIT
                )
                extracted_text = response.text

            if os.path.exists(filepath):
                os.remove(filepath)

            segments = split_into_segments(extracted_text)
            summary_results = {}
            with concurrent.futures.ThreadPoolExecutor(max_workers=min(len(segments), MAX_PARALLEL)) as executor:
                future_to_index = {
                    executor.submit(summarize_segment, label, content): idx
                    for idx, (label, content) in enumerate(segments)
                }
                for future in concurrent.futures.as_completed(future_to_index):
                    idx = future_to_index[future]
                    summary_results[idx] = future.result()
            structured_summary = [summary_results[i] for i in range(len(segments))]

            full_summary = render_summary_html(structured_summary)
            pdf_bytes = generate_pdf_bytes(note.title, structured_summary)

            note.digitized_text = extracted_text.strip()
            note.summary_html = full_summary
            note.pdf_data = pdf_bytes
            note.status = "complete"
            db.session.commit()
        except GeminiCooldown as cd:
            logger.warning("SuperNotes job %s hit AI cooldown: %s", note_id, cd.message)
            if os.path.exists(filepath):
                os.remove(filepath)
            note.status = "failed"
            note.error_message = cd.message
            db.session.commit()
            credits.refund_charge(note.user_id, f"note-{note_id}")
        except Exception:
            logger.exception("SuperNotes job failed for note %s", note_id)
            if os.path.exists(filepath):
                os.remove(filepath)
            note.status = "failed"
            note.error_message = "We couldn't process this file. Please try a clearer scan or a different file."
            db.session.commit()
            credits.refund_charge(note.user_id, f"note-{note_id}")