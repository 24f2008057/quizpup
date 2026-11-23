# QuizPup Data Inventory and Privacy Review

Status: working document, not legal advice. A lawyer must review it before it is relied on. Last reviewed during Workstream 5 (November 2026).

## 1. Personal data QuizPup holds

| Data | Where stored | Why | Source | Retention | Shared with |
|---|---|---|---|---|---|
| Username, first name, last name | PostgreSQL (Supabase), table `user` | Account and greeting | You, or Google/GitHub sign-in | Until account deletion or 24 months without sign-in | Supabase |
| Email address | `user` | Sign-in, verification codes, password reset | You, or Google/GitHub | Same | Supabase, Brevo (sending codes) |
| Password hash (scrypt) | `user` | Sign-in | You | Same | Supabase |
| Phone number | `user.phone` (column kept, no longer collected) | Was collected at sign-up; no use found | You | Remove existing values with the optional SQL in section 6 | Supabase |
| One-time codes and attempt counters | `user` | Email verification, password reset, email change | Generated | Cleared when expired by `flask purge-data` | Supabase |
| Sign-in provider and Supabase user id | `user` | Link Google/GitHub identity | Supabase Auth | Until deletion | Supabase |
| Account created time, last sign-in time | `user` | Retention enforcement | Generated | Until deletion | Supabase |
| Consent records (type, granted, policy version, time, source) | `consent_record` | Proof of acceptance | Generated at sign-up or re-acceptance | Deleted with the account | Supabase |
| Quiz sessions, questions, your answers, scores, recommendations | `quiz_session`, `quiz_question` | The product | You and Gemini output | Until you delete them or the account | Supabase, Google (topic and source text go to Gemini) |
| Flashcard decks | `flashcard_deck` | The product | You and Gemini output | Same | Supabase, Google |
| Notes: extracted text, summary, generated PDF | `super_note` | The product | Your uploads and Gemini output | Same | Supabase, Google |
| Uploaded files (images, PDFs) | Render disk, folder `uploads/`, temporary | Processing | You | Deleted after processing, including on errors. A crash mid-processing could leave a file until Render's next restart or deploy | Render; content sent to Google |
| Session and remember-me cookies, CSRF token | Browser | Keep you signed in, protect forms | Generated | Up to 30 days | None |
| Cookie-choice record | Browser localStorage, key `quizpup_consent` | Remember banner choice | You | Until you clear it | None |
| Server logs (user id, event type, error text) | Render logs | Operations and security | Generated | Render's log retention (check dashboard) | Render |

Not collected: phone numbers (from now on), precise location, device identifiers, advertising identifiers, payment details (no payments yet), analytics events.

## 2. What is sent to third parties

| Recipient | What | When |
|---|---|---|
| Google Gemini API | Topic text, text from uploaded PDFs, uploaded images. No name, email or user id (checked in `services/gemini_service.py` and `supernotes_service.py`) | Every quiz, flashcard or notes generation |
| Brevo | Recipient email address and the code | Verification, password reset, email change |
| Supabase Auth | Email and profile from Google or GitHub, on OAuth sign-in | OAuth sign-in only |
| Render | All traffic and stored uploads, as host | Always |

## 3. Third-party SDK, script and tracker audit (item 26)

Browser scripts. Before this workstream the app loaded three scripts from cdn.jsdelivr.net on every page or on sign-in pages, which sent each visitor's IP address and browser details to a third party. All three are now served from `static/vendor/` and the Content Security Policy no longer allows any external script, style or font host.

| Script | Version | Where it was loaded | Now |
|---|---|---|---|
| lottie-web | 5.12.2 | Every page (loading animation) | Self-hosted |
| @supabase/supabase-js | 2.118.0 | Login and OAuth callback | Self-hosted |

Fonts are self-hosted. No analytics, advertising, session-replay, A/B-testing, social or chat scripts exist in the templates (checked by an automated test).

Android wrapper (Capacitor): plugins are Browser and Splash Screen only. Neither collects analytics. The wrapper loads the live website, so web cookies and consent apply. Re-run this audit when the native app is built in Workstream 12.

Python packages in `requirements.txt` were reviewed by name: none is an analytics or telemetry client. The `supabase` and `google-genai` clients contact only their own services.

Supabase JS stores its sign-in session in browser localStorage on sign-in pages. It is needed for sign-in and is treated as strictly necessary.

## 4. Cookies and consent (items 4 and 28)

Only strictly necessary cookies are set. The banner states this, records "Essential only" or "Allow optional" in the browser, and can be reopened from the footer. `static/js/consent.js` holds back any script marked `<script type="text/plain" data-consent="analytics" src="...">` until the visitor allows that category, so a future analytics tool can be added without firing early. Razorpay's checkout script, when added in Workstream 7, should load only on the payment step.

## 5. Legal basis, retention and rights

Retention rules (item 7), configurable by environment variable and enforced by running `flask purge-data`:
- Accounts never verified: removed after 7 days (`UNVERIFIED_ACCOUNT_DAYS`).
- Accounts with no sign-in for 730 days: removed (`INACTIVE_ACCOUNT_DAYS`). Revisit this before paid plans exist.
- Expired verification and email-change codes: cleared.
- Uploads: deleted immediately after processing.
The command must be scheduled (Render Cron Job, or an external scheduler such as cron-job.org calling a protected route that you add later). It is not scheduled by this change.

Access, export, correction and deletion (item 13): Profile page, "Download my data" (JSON) and "Delete my account". Deletion removes the local records, consent records, and the Supabase sign-in identity. Backups held by Supabase and Render are outside this app's control; ask each provider for their backup retention and state it in the policy.

Consent records (item 19): `consent_record` stores type, granted, policy version, time and source. Existing users are asked to accept again on their next visit. Bumping `TERMS_VERSION` or `PRIVACY_VERSION` asks everyone again.

Form consent audit (item 36):
| Form | Consent needed | Status |
|---|---|---|
| Register | Terms, Privacy, 18+ (separate, unticked, server-checked) | Done |
| OAuth finish-setup | Same | Done |
| Accept-terms page (existing users) | Same | Done |
| Login, forgot password, profile edit | None | n/a |
| Marketing email | Separate opt-in | Does not exist yet, Workstream 8 |

## 6. Optional clean-up SQL

To remove phone numbers already stored, after confirming you do not need them:
`update public."user" set phone = null;`

## 7. AI training (item 24)

- QuizPup does not train models on user content, and no code does.
- Gemini free tier: Google's Gemini API terms say content submitted to unpaid services may be used to improve Google's products and may be read by human reviewers, and say not to submit personal information. This cannot be switched off while the app uses unpaid quota. The app now shows a notice at both upload points and the privacy page says so. Paid-tier terms say Google does not use prompts or responses to improve its products. Set `GEMINI_PAID_TIER=true` on Render once billing is on, and the notices switch to the paid wording.
- The same Gemini terms say apps must not be directed at, or likely to be accessed by, people under 18. QuizPup is exam preparation and students under 18 are a likely audience. Sign-up now asks for an 18+ confirmation, but this is a business and legal decision that needs a lawyer's advice before launch.

## 8. India's DPDP Act, 2023

The Act's main duties (notice and consent, children's data and verifiable parental consent, retention and erasure, breach notification) start on 12 May 2027 under the 2025 Rules; consent-manager rules start 12 November 2026. QuizPup's notice, consent records, erasure and export are built toward that date. Two gaps remain: a breach-response procedure (Workstream 1 follow-up) and handling of users under 18, which needs a decision. A grievance contact must be published; set `PRIVACY_CONTACT_EMAIL`.

## Billing data (added in Workstream 7)

| Table | What it holds | Notes |
|---|---|---|
| subscription | One row per user: plan, monthly or pass, status, period dates, Razorpay subscription id | Deleted with the account |
| payment | Amount, plan, Razorpay payment, order and subscription ids, status, refunded amount | On account deletion the user link is removed and the row is kept anonymously. Retention period to be set by an accountant |
| payment_event | Razorpay webhook event ids | Used only to ignore repeated deliveries |
| credit_ledger | Credit grants, charges and refunds per user | Deleted with the account |

Card, UPI and bank details are entered in Razorpay's window and never reach QuizPup. Razorpay is a sub-processor.