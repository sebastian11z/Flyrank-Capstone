# FlyRank capstone: usage metering and billing

A backend for two tenant organizations: meter simulated generation, enforce plan
quotas before work, calculate exact USD costs, and synchronize Stripe **test-mode**
subscriptions. No model call, paid infrastructure, or real payment is required.

**Submission status:** local implementation and synthetic Stripe tests are present.
Real Stripe Checkout/CLI evidence and Sebastian's explanation/submission remain
pending. Read [EVIDENCE.md](EVIDENCE.md); a mock is not a real Checkout proof.

## Run on a clean machine

Install Python **3.12** and Git. Clone this dedicated public repository and enter
its directory. Create an isolated environment (commands below use PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
Copy-Item .env.example .env
python -m app.seed
python -m app
```

If PowerShell blocks activation, use `.\.venv\Scripts\python.exe` in place of
`python`; no policy change is needed. On macOS/Linux, activate with
`source .venv/bin/activate` and copy using `cp .env.example .env`.
Do not overwrite an existing `.env`. Metering starts with Stripe variables blank.
The seed generates distinct high-entropy API keys in the git-ignored `.env` and
stores only their SHA-256 hashes in SQLite. It is safe to repeat: usage and plans
are preserved, and changed keys are rejected rather than silently rotating access.

The **one run command** is `python -m app`; the **seed command** is
`python -m app.seed`. Startup applies versioned migrations. Database files live
under `data/`. Keep the terminal open. Stop with Ctrl+C. The API and its single
background worker run together on `http://127.0.0.1:8000`; interactive API docs are
at `/docs`. Restart after editing `.env`. Do not run multiple server processes.

In a second activated terminal:

```powershell
python -m pytest -q
python scripts/probe.py core
python scripts/probe.py usage
```

`core` makes two real HTTP requests with one newly generated idempotency key and
asserts one new event and exactly 6,600 micro-USD of incremental cost. Repeating
the script is a new billable action. Keys and hosted Checkout URLs are not printed.
Tests use temporary databases and never call Stripe or change your demo data.

## Architecture and schema

```text
Client (X-API-Key)
  -> FastAPI HTTP: validation, authentication, error/status mapping
      -> Metering logic: retry lookup -> plan/quota check -> event + saved response
          -> SQLite transaction, unique tenant/key, integer costs
      -> GET /usage: tenant/month rollup of accepted events
      -> Checkout logic -> official Stripe SDK -> hosted test Checkout

Stripe CLI -> POST /webhooks/stripe -> SDK raw-body signature verification
  -> durable webhook_jobs inbox (unique event ID) -> quick acknowledgment
      -> background worker -> retrieve current subscription from Stripe
          -> validate customer/price/mode -> subscription update + mark done atomically
          -> retry with backoff -> CRITICAL failure alert + degraded health on exhaustion
```

Data access is in `app/db.py` and `migrations/`. Business rules are in
`app/metering.py`, `app/payments.py`, and `app/jobs.py`. HTTP is in `app/main.py`.
`tenants` owns one `subscriptions` row; it references a shared `plans` row.
`usage_events` references its tenant. `checkout_attempts` persists one active
Checkout intent per tenant. `webhook_jobs` records verified event IDs and minimal
customer/subscription IDs, not raw Stripe payloads or card/customer details.
Foreign keys, unique keys, and tenant/month and queue indexes are in migrations.

## API and tenant isolation

| Route | Purpose | Authentication |
|---|---|---|
| `GET /health` | DB health and count of exhausted jobs; 503 when degraded | None; no tenant data |
| `POST /generate` | One simulated action; one usage event containing calls + token counts | `X-API-Key`, `Idempotency-Key` |
| `GET /usage` | Current UTC month's usage, limits, cost, plan/status | `X-API-Key` |
| `POST /billing/checkout` | Create/reuse hosted Pro Checkout | `X-API-Key` |
| `GET /billing/status` | Current tenant's latest 20 background jobs | `X-API-Key` |
| `POST /webhooks/stripe` | Queue verified supported events | `Stripe-Signature` |

`tenant_a` and `tenant_b` start on Free. A key determines its tenant; request
bodies cannot choose another tenant. The same idempotency key can be used by two
tenants independently. There is no public plan-change or tenant-admin endpoint.
OpenAPI describes the headers and token fields. A generation body can be `{}`
(API-call usage only) or:

```json
{"input_tokens":1000,"cached_input_tokens":400,"output_tokens":500,"reasoning_tokens":200}
```

Only nonnegative integers are accepted, up to 1 billion per field; booleans,
floats, strings, invalid subsets, and extra fields receive 422. A key is 1-128
printable non-space ASCII characters. Missing/invalid tenant authentication is 401.

## Quotas and retry contract

| Plan | API calls / UTC calendar month | AI tokens / UTC calendar month |
|---|---:|---:|
| Free | 1,000 | 100,000 |
| Pro | 10,000 | 1,000,000 |

Each accepted generation consumes **one call** and `input_tokens + output_tokens`.
Both `used + requested <= limit` checks must pass. At exactly the boundary the
request succeeds; the next request that needs more of that meter gets **429**,
with `meter`, `used`, `requested`, `limit`, `resets_at`, and `Retry-After` seconds.
At the token limit, a zero-token API-only action can still use remaining calls.
Denied requests consume neither meter and reserve no idempotency key.

An existing unpaid/delinquent paid subscription (`past_due`, `unpaid`, `incomplete`,
or `paused`) yields **402**, with a payment explanation. Active/trialing Pro is
allowed. Canceled or incomplete-expired subscriptions return to Free. A downgrade
does not erase existing usage; if it already exceeds Free limits, wait or upgrade.

`BEGIN IMMEDIATE` serializes retry lookup, quota check, and insert. The unique
`(tenant_id, idempotency_key)` constraint backs up the transaction. Same key + same
normalized body returns the original saved response, including its event ID and
month, even after a month/plan/status change. Same key + different body is **409**.
Keys are retained with events indefinitely in this capstone. There is no quota
reset job: UTC month filtering naturally starts a new monthly window.

## Pinned pricing and money arithmetic

Prices in `app/config.py` are illustrative **versioned constants**, not a current
AI provider's advertised prices. All stored costs are integer micro-USD
(`1 USD = 1,000,000 micro-USD`); USD response values are exact decimal strings.
No binary floating point is used for money.

| Unit | micro-USD | Equivalent |
|---|---:|---|
| API call | 1,000 | USD 0.001 / call |
| Fresh input token | 2 | USD 2 / million |
| Cached input token | 1 | USD 1 / million |
| Output token, including reasoning | 8 | USD 8 / million |

`input_tokens` **includes** its cached subset. `output_tokens` **includes** its
reasoning subset. Do not add subsets again. For the body above:

```text
API calls       1 * 1000          = 1000 micro-USD
Fresh input     (1000 - 400) * 2  = 1200
Cached input    400 * 1           =  400
Output          500 * 8           = 4000  (already includes 200 reasoning tokens)
Total                             = 6600 = USD 0.006600
Quota tokens                      = 1500, not 2100
```

Each event stores its calculated charge and pricing version. `/usage` sums those
stored charges; changing future pricing cannot silently reprice old events.
The **USD 19/month Pro subscription fee is separate** from this informational
usage-cost figure. We do not send usage charges or overages to Stripe.

## Complete the real Stripe test-mode check

Use Stripe's test mode/sandbox throughout. No real card and no live API key. The
app rejects a configured key without `sk_test_` and signed live events. Do not
paste secrets into chat, screenshots, Git, command lines, or evidence.

1. In your Stripe test Dashboard, create a product named **FlyRank Pro** with a
   recurring **USD 19.00 monthly** price (one unit, no trial/discount/tax setup).
   Copy its `price_...` ID. The app verifies that price's mode, currency, amount,
   recurring interval, and licensed usage type before creating Checkout.
2. In the ignored `.env`, set `STRIPE_SECRET_KEY` to your test secret and
   `STRIPE_PRO_PRICE_ID` to that price ID. Keep both demo keys.
3. Install the [official Stripe CLI](https://docs.stripe.com/stripe-cli/install),
   then sign in and forward the three required snapshot event types:

   ```powershell
   stripe login
   stripe listen --events checkout.session.completed,customer.subscription.updated,customer.subscription.deleted --forward-to http://127.0.0.1:8000/webhooks/stripe
   ```

   On Sebastian's current workstation a checksum-verified CLI is also available
   at `tmp/stripe/bin/stripe.exe`; use that path in place of `stripe`.
4. Copy the listener's `whsec_...` value into `.env` as `STRIPE_WEBHOOK_SECRET`.
   The CLI secret and a Dashboard endpoint's secret are different. Keep the
   listener running and restart `python -m app` to read the new environment.
5. Run `python scripts/probe.py usage`; record Free limits. Run
   `python scripts/probe.py checkout`; it opens hosted Checkout locally. Use test
   card **4242 4242 4242 4242**, any future expiry and test CVC; no real card.
6. After completion run `python scripts/probe.py status` and
   `python scripts/probe.py usage`. Wait for a `done` job and Pro limits of 10,000
   calls and 1,000,000 tokens. A success-page visit alone never upgrades a tenant.
7. Capture a forged-signature rejection with the command below. A local fixture
   test already proves this, but real HTTP evidence should also show no changes:

   ```powershell
   curl.exe -i -X POST http://127.0.0.1:8000/webhooks/stripe -H "Content-Type: application/json" -H "Stripe-Signature: invalid" -d "{}"
   ```

8. Replay a **real event belonging to this app's customer** twice locally after
   its initial CLI delivery. `stripe trigger` creates unrelated fixture customers by default, so
   it alone is not proof of this tenant's upgrade. Use
   `python scripts/replay_event.py evt_YOUR_REAL_EVENT_ID` (documented below).
   Confirm `duplicate` acknowledgments and the same job's `attempts: 1`.
9. Change/cancel the subscription in the test Dashboard and verify its signed
   updated/deleted event synchronizes status/limits. Paste sanitized actual
   outputs into the pending slots in EVIDENCE.md.

`scripts/replay_event.py` retrieves an existing real test event with your local
test key and posts it twice, signed with the configured local listener secret.
This exercises replay of a real Stripe payload, rather than inventing a checkout
event. The initial checkout delivery must still be observed through Stripe CLI.
It prints only acknowledgment and tenant-scoped job state, never raw payloads,
signatures, keys, card details, or hosted Checkout URLs.

Stripe integration follows the official [Checkout Session API](https://docs.stripe.com/api/checkout/sessions/create),
[webhook guidance](https://docs.stripe.com/webhooks), and
[Python SDK resource handling](https://github.com/stripe/stripe-python#working-with-api-resources).

## Worker retries and failure alert

Webhook verification writes a minimal durable job **before** returning success.
The worker retrieves current subscription truth from Stripe, verifies ownership
and exact Pro price/quantity, then atomically commits plan/status and job completion.
Event IDs deduplicate delivery. Old snapshots cannot resurrect canceled plans.
A late deletion of an old subscription cannot undo a newer active subscription.
An unrelated customer is ignored and cannot select a tenant through metadata.

Three attempts, delays of 2 then 4 seconds, and a 120-second recoverable lease
cover transient failures and process restarts. A fencing token prevents an old
worker's late result from overwriting a reclaimed job. Exhaustion writes durable
`failed` state, emits a **CRITICAL `BACKGROUND_JOB_FAILED` alert** to stderr, and
makes `/health` return 503. This is a local operational alert, not an email/page.
Exception messages and raw payment payloads are never logged. Inspect your own
jobs with `python scripts/probe.py status`. Correct the cause, restart if `.env`
changed, then run:

```powershell
python -m app.jobs evt_FAILED_EVENT_ID
```

Only failed jobs can be reset. Completed work is never reset. An interrupted
Checkout attempt retains its Stripe idempotency key. An uncertain attempt older
than 23 hours requires operator reconciliation before another session can be
created, avoiding Stripe's eventual idempotency-record expiry.

## Limitations and submission

- Single local process/worker, SQLite write serialization, no throughput claims.
  Queued Stripe synchronization is eventually consistent; inspect status before
  relying on new limits. Back up SQLite and `.env` together. Never delete the DB
  while Stripe subscriptions exist without first reconciling customer mappings.
- API keys are a capstone authentication mechanism, with no user signup, account
  recovery, key-rotation UI, or production deployment/TLS configuration. The API
  binds loopback. Do not expose it publicly as a production payment service.
- Only USD, UTC calendar months, one Pro subscription per tenant, one licensed
  Pro price. No invoicing, overage billing, proration, discounts, tax, reconciliation
  scheduler, model calls, or collection of the informational usage cost.
- `customer.subscription.created` is intentionally outside the required event
  set: create subscriptions through this app's Checkout. Unrelated CLI fixtures
  are ignored. Exhausted jobs require an operator to retry after fixing the cause.
- `BUILDLOG.md` records AI involvement and its mistakes. Sebastian must understand
  the selected code and add his own review/changes honestly before submitting.
- Submit the **public GitHub repository URL** through the internship portal, not
  an archive. Do not mark the external evidence complete until actually run.

See [DESIGN.md](DESIGN.md), [REQUIREMENTS.md](REQUIREMENTS.md),
[EVIDENCE.md](EVIDENCE.md), and [TUTORIAL.md](TUTORIAL.md) for design, proof status,
and a detailed explanation of the engineering choices.
