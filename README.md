# QuizPup

QuizPup is exam preparation for any exam. Students can generate quizzes and flashcards on any topic or from their own PDF, and can start from ready-made decks for JEE, NEET, and CAT. It combines AI-generated quizzes, preloaded flashcard decks, and an OCR-powered notes digitizer into a single Flask web app that also ships as a native Android app via Capacitor.

**Live app:** [quizpup.onrender.com](https://quizpup.onrender.com/)

## Features

### AI Quiz Engine
- Generates 10–200 question multiple-choice quizzes on any topic, or from an uploaded PDF (text is extracted and used as source material).
- Four difficulty levels — easy, medium, hard, and **god mode** (university-level rigor, higher generation temperature, escalated to a heavier model).
- Hints according to the difficulty level of the quiz: unlimited on easy, ~30% of question count on medium, none on hard/god mode.
- Large quizzes are split into batches and generated concurrently against the Gemini API, then merged.
- Every question ships with a hint and a post-attempt explanation; sessions and answers are persisted so a quiz can be reviewed after submission.

### Flashcards
- **Preloaded decks**: ~600+ hand-curated cards organized as Exam → Subject → Chapter (JEE: Physics/Chemistry/Maths, NEET: Biology/Chemistry/Physics, CAT: Quant/DILR/VARC).
- **AI-generated decks**: type any topic and get 15–25 revision flashcards, saved to your account for later review.

### SuperNotes (OCR + Summarization)
- Upload a photo or PDF of handwritten/printed notes.
- PDFs are text-extracted first; if the page text density is too low (i.e. scanned/handwritten), the app falls back to Gemini-based OCR.
- Long notes are split into segments and summarized concurrently, then reassembled into a structured HTML summary and a downloadable two-column PDF (built with fpdf2 and DejaVu fonts for full Unicode support).
- Processing runs as a background thread so the upload request returns immediately; the frontend polls a status endpoint.

### Accounts & Security
- Email/password auth (Flask-Login) with OTP-based email verification, password reset, and email-change flows — all backed by rate-limited attempt counters.
- Transactional email delivered via the Brevo HTTP API.
- CSRF protection (Flask-WTF), hardened session cookies (HttpOnly, SameSite=Lax, Secure in production), and signed, expiring tokens for note PDF downloads.
- Ownership checks on every quiz/flashcard/notes endpoint so users can only access their own data.

### Android App
- Packaged as a native Android app using Capacitor, pointing at the live Render deployment (`www` dir + `capacitor.config.json`).

## Tech Stack

| Layer | Technology |
|---|---|
| Backend | Flask, Flask-SQLAlchemy, Flask-Login, Flask-WTF |
| Database | PostgreSQL (via Supabase) in production, SQLite fallback for local dev |
| AI | Google Gemini API (`google-genai`) — tiered between a lite and a heavier model based on task difficulty/size |
| PDF handling | pypdf (extraction), fpdf2 (generation) |
| Email | Brevo HTTP API |
| Mobile | Capacitor (Android) |
| Deployment | Render, served with Gunicorn |

## Project Structure

```
quiz-app/
├── app.py              # Flask app: routes, auth, quiz/flashcard/notes logic, Gemini integration
├── models.py            # SQLAlchemy models: User, QuizSession, QuizQuestion, FlashcardDeck, SuperNote
├── flashcard_data.py     # Preloaded flashcard decks (JEE/NEET/CAT)
├── templates/            # Jinja2 templates (dashboard, quiz, auth flows)
├── static/               # Images, fonts (used in generated PDFs)
├── uploads/               # Temporary storage for uploaded PDFs/images (cleared after processing)
├── android/               # Capacitor Android project
├── www/                   # Capacitor web shell
└── capacitor.config.json  # Points the Android build at the live deployment
```

## Setup

1. Clone the repo and install dependencies:
   ```bash
   pip install -r requirements.txt
   npm install   # only needed for the Capacitor/Android build
   ```

2. Create a `.env` file with:
   ```
   SECRET_KEY=
   DATABASE_URL=            # Postgres URL; omit to fall back to local SQLite
   SUPABASE_URL=
   SUPABASE_KEY=
   GEMINI_API_KEY=
   BREVO_API_KEY=
   BREVO_SENDER_EMAIL=
   ```

3. Run the app:
   ```bash
   python app.py
   ```
   Tables are created and auto-migrated (missing columns are added) on startup.

## API Overview

| Area | Endpoints |
|---|---|
| Auth | `/register`, `/login`, `/logout`, `/verify-email`, `/forgot-username`, `/forgot-password`, `/verify-otp` |
| Profile | `/profile`, `/profile/edit`, `/profile/email/*` |
| Flashcards | `/api/flashcards/structure`, `/api/flashcards/preload`, `/api/flashcards/generate`, `/api/flashcards/my-decks` |
| SuperNotes | `/api/study/process-notes`, `/api/study/notes/history`, `/api/study/notes/<id>`, `/api/study/notes/<id>/pdf` |
| Quiz | `/generate-quiz`, `/quiz/<session_id>`, `/api/quiz/<session_id>/questions`, `/api/quiz/<session_id>/submit` |

## Future Plans

### Upcoming features
- **Generating flashcards from notes:** Students will be able to generate flahcards by uploading images or pdfs of the notes that they want to generate flashcards of.
- **AI tutor:** Helping students in their study with visual learning and interactive elements.
- **Student Helpline:** Helping students in their difficulties both academically and mentally. We as a company feel that no student should suffer or feel burdened due to education. We will be connecting students to specialists who can help guide them in the right direstion and suggest them alternatives by analyzing their info. 

### App Launch
We are actively working towards delivering the final fully running version one **(V1.0)** of the app to the public by **August 15, 2026**. 

## License

Personal project of Creator: **Sanjoy Ostad**