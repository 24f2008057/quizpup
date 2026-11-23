# Legal pages: what you still need to fill in or decide

Not legal advice. A qualified lawyer must review every page before launch. Then set `LEGAL_REVIEWED_BY_LAWYER=true` to remove the draft banner.

## Environment variables on Render (shown on /contact, /terms, /privacy)

| Variable | What to put |
|---|---|
| LEGAL_ENTITY_NAME | Legal name of whoever operates QuizPup. No business is registered yet; decide this with a lawyer or accountant |
| LEGAL_ADDRESS | Postal address you are willing to publish |
| SUPPORT_EMAIL | Support address |
| PRIVACY_CONTACT_EMAIL | Address for data requests |
| GRIEVANCE_OFFICER_NAME, GRIEVANCE_OFFICER_EMAIL | Named person for complaints |
| LEGAL_REPLY_TIME | A reply time you can keep |
| LEGAL_GOVERNING_LAW, LEGAL_COURT_PLACE | For example India and a city |
| LEGAL_EFFECTIVE_DATE | Date you publish |
| LEGAL_REVIEWED_BY_LAWYER | `true` only after review |

Run `flask legal-check` to list what is still empty. It exits with an error until all are set.

## Decisions only you can make

1. Refund window and renewal refund rule (Refund Policy). Decide with pricing in Workstream 7.
2. Plan names, prices and renewal terms (Terms, "Plans and payments").
3. Uptime figure (Terms, "Availability"). The page promises none today. Set one only after load testing in Workstream 11, and decide whether it is a goal or a promise.
4. Whether under-18 users are allowed at all (Google's Gemini terms, DPDP parental consent).
5. Regions of Supabase, Render, Google and Brevo, for the transfers section and the sub-processors page.
6. Log and backup retention periods of your providers.
7. Whether stored phone numbers are deleted (`update public."user" set phone = null;`).

## For the lawyer

Legal basis per purpose (DPDP and GDPR if you serve the EU or UK), rights and response times, breach wording, liability limit and disclaimer, governing law and dispute route, the licence to user content, ownership of AI output, which business contact details Indian rules require you to publish, and recurring-payment rules before Workstream 7.
