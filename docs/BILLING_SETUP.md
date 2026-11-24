# Billing setup (Razorpay)

Do all of this in Razorpay TEST mode first. No real money moves in test mode.

1. Create a Razorpay account. Switching to live mode later needs KYC and a bank account. Whether an unregistered individual can go live depends on Razorpay's current rules, so confirm with them before you plan a launch date.
2. In the dashboard (test mode) go to Subscriptions, Plans, and create two monthly plans: Pro for Rs 399 and Max for Rs 799. The amounts must match `PRO_PRICE_INR` and `MAX_PRICE_INR`, or checkout refuses to start.
3. Go to Settings, API Keys, generate test keys.
4. Go to Settings, Webhooks, add `https://YOUR-DOMAIN/billing/webhook`, choose a secret, and tick: subscription.authenticated, activated, charged, pending, halted, cancelled, completed, paused, resumed, updated; payment.failed; refund.created, refund.processed.
5. Set these on Render (never commit them): `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_WEBHOOK_SECRET`, `RAZORPAY_PLAN_PRO`, `RAZORPAY_PLAN_MAX`, and `BILLING_ENABLED=true`.
6. Run the SQL in `sql/ws7_billing.sql` in the Supabase SQL editor (it also turns on row level security for the new tables).
7. Run `flask billing-check`. It prints `mode: test` or `mode: LIVE`.
8. Test with Razorpay's test UPI id and test cards: a monthly plan, cancel, a refund from the dashboard, and a failed payment.
9. Schedule `flask send-renewal-reminders` once a day.

Refund from the command line: `flask refund-payment pay_XXXX --amount-inr 399`. Access ends when Razorpay confirms a full refund.

Testing on your own computer: Razorpay cannot reach localhost, so webhooks only work on your Render site (or through a tunnel). Use TEST keys on Render first, then replace them with LIVE keys only when you are ready to take real money. Never paste a key into chat, a ticket or a commit.

All plans renew automatically each month. A customer can stop the autopay from QuizPup (Cancel renewal) or in their bank app. There are no one-time passes.

Not built yet: switching from Pro to Max in the middle of a plan, invoices and tax receipts, and any school or institution plan. If the Razorpay checkout fails to open, open the browser console and look for a Content-Security-Policy message, then tell me the blocked address.