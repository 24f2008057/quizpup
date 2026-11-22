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
| Age group (adult or minor) | `user.age_group` | Decide whether parental confirmation is needed | You | Until account deletion | Supabase |
| Parent or guardian email (under-18 users only) | `user.parent_email` | Ask the parent to confirm, and to let them withdraw consent | The student | Until account deletion; accounts never confirmed by a parent are removed after 14 days (`PARENT_PENDING_ACCOUNT_DAYS`) | Supabase, Brevo (sending the confirmation email) |
| Consent records (type, granted, policy version, time, source, parent email for parental records) | `consent_record` | Proof of acceptance, parental confirmation and withdrawal | Generated at sign-up, re-acceptance, or when the parent confirms or withdraws | Deleted with the account | Supabase |
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
| Brevo | Recipient email address and the code or link; the student's first name in parent emails | Verification, password reset, email change, parent confirmation |
| Supabase Auth | Email and profile from Google or GitHub, on OAuth sign-in | OAuth sign-in only |
| Render | All traffic and stored uploads, as host | Always |

## 3. Third-party SDK, script and tracker audit (item 26)

Browser scripts. Before this workstream the app loaded three scripts from cdn.jsdelivr.net on every page or on sign-in pages, which sent each visitor's IP address and browser details to a third party. All three are now served from `static/vendor/` and the Content Security Policy no longer allows any external script, style or font host.

| Script | Version | Where it was loaded | Now |
|---|---|---|---|
| lottie-web | 5.12.2 | Every page (loading animation) | Self-hosted |
| @lottiefiles/lottie-player | 2.0.4 | Every page | Self-hosted |
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
| Register | Terms, Privacy (unticked, server-checked); age question, and parent email if under 18 | Done |
| OAuth finish-setup | Same | Done |
| Accept-terms page (existing users) | Same; age question only if not yet answered | Done |
| Parent confirmation page | Adult-guardian declaration and Terms and Privacy for the child (both unticked, server-checked) | Done |
| Parent manage page | Withdraw consent; delete account (needs a ticked box) | Done |
| Login, forgot password, profile edit | None | n/a |
| Marketing email | Separate opt-in | Does not exist yet, Workstream 8 |

## 6. Optional clean-up SQL

To remove phone numbers already stored, after confirming you do not need them:
`update public."user" set phone = null;`

## 7. AI training (item 24)

- QuizPup does not train models on user content, and no code does.
- Gemini free tier: Google's Gemini API terms say content submitted to unpaid services may be used to improve Google's products and may be read by human reviewers, and say not to submit personal information. This cannot be switched off while the app uses unpaid quota. The app now shows a notice at both upload points and the privacy page says so. Paid-tier terms say Google does not use prompts or responses to improve its products. Set `GEMINI_PAID_TIER=true` on Render once billing is on, and the notices switch to the paid wording.
- The same Gemini terms say apps must not be directed at, or likely to be accessed by, people under 18. QuizPup is exam preparation and students under 18 are a likely audience. Sign-up now asks whether the user is under 18 and requires parental confirmation for minors, but this does not change Google's own terms. Whether under-18 students may use the free tier, or any tier, is a business and legal decision that needs a lawyer's advice before launch. Check the paid-tier terms for the same restriction.

## 8. India's DPDP Act, 2023

The Act's main duties (notice and consent, children's data and verifiable parental consent, retention and erasure, breach notification) start on 12 May 2027 under the 2025 Rules; consent-manager rules start 12 November 2026. QuizPup's notice, consent records, erasure and export are built toward that date. Handling of users under 18 is built (section 9) and needs a lawyer to confirm it meets the "verifiable" parental consent standard. One gap remains: a breach-response procedure (Workstream 1 follow-up). A grievance contact must be published; set `PRIVACY_CONTACT_EMAIL`.

## 9. Children (under 18)

Flow:
1. Sign-up, Google sign-up and the accept-terms page ask whether the user is 18 or older. Under 18 requires a parent or guardian email that differs from the student's.
2. The parent receives a signed link, valid 7 days and tied to that email. On the confirmation page they declare they are an adult parent or guardian and accept the Terms and Privacy Policy for the child. This is stored as a `parental` consent record with time, terms version and parent email.
3. Until then the student sees only a "waiting for your parent" page, and API calls return 403 `parental_consent_required`.
4. After confirming, the parent receives an email with a management link valid one year. From it they can withdraw consent (the student is locked again) or delete the account and all data.
5. Changing the parent email, or bumping `TERMS_VERSION`, requires a new confirmation.

Restrictions for under-18 accounts:
- No optional analytics. The page is marked `data-minor`, `consent.js` refuses every optional category, and the banner has no "Allow optional" button. No advertising or analytics exists in the app today.
- Accounts that a parent never confirmed are removed by `flask purge-data` after `PARENT_PENDING_ACCOUNT_DAYS` (default 14). Accounts whose parent withdrew consent are kept so the parent can confirm again, or deleted by the parent.

Open points for a lawyer:
- Whether email-link confirmation plus a declaration is enough for verifiable parental consent under the 2025 Rules. A payment by the parent is a stronger signal and is planned for Workstream 7.
- Whether the quiz scores and recommendations stored for students count as monitoring of a child's behaviour.
- Gemini terms for under-18 users (section 7).
- Parent-email sending depends on `BREVO_API_KEY` and `BREVO_SENDER_EMAIL` being set; it has not been tested against Brevo.