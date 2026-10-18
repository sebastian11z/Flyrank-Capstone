# Usage metering and billing: phase 1 design

Build a tenant-isolated API that meters simulated generation, enforces monthly
allowances atomically, calculates exact costs, and mirrors Stripe test subscriptions.
Non-goal: collecting usage charges (no invoicing, overages, or proration).

## Data and layers

SQLite with versioned SQL migrations, foreign keys, WAL, and integer USD micro-units
(1 USD = 1,000,000 micro-USD). `tenants` stores a SHA-256 digest of a random API key
and an optional unique Stripe customer ID. `plans` defines Free (1,000 calls,
100,000 tokens) and Pro (10,000 calls, 1,000,000 tokens). `subscriptions` holds one
current plan/status per tenant and its Stripe subscription ID. `usage_events` holds
one accepted generation: one API call plus its token breakdown, immutable charge,
pricing version, request fingerprint, idempotency key, and original response.
`webhook_jobs` is a durable, deduplicated Stripe inbox with retry/lease state.

```text
HTTP (app/main.py: validation, auth, status codes)
  -> logic (app/metering.py, app/payments.py, app/jobs.py)
    -> data (app/db.py + migrations/*.sql)
Stripe -> signature verification -> durable inbox -> worker -> Stripe current state
                                                    -> subscription update + done
```

## API and correctness contract

- `POST /generate`: `X-API-Key`, `Idempotency-Key`, integer token counts. In one
  `BEGIN IMMEDIATE` transaction: find prior result, compare canonical request,
  check subscription, sum this UTC calendar month's events, check both quotas,
  insert one event and its response. A unique `(tenant_id, idempotency_key)` index
  is the database backstop. Same key/different body is 409. A rejected action
  creates no event. The exact boundary is allowed; crossing either quota is 429
  with a reset timestamp and Retry-After; a delinquent paid subscription is 402.
- `GET /usage`: current UTC calendar month, current plan limits, category totals,
  immutable cost sum, and subscription state. Authentication determines the tenant.
- `POST /billing/checkout`: creates/reuses a hosted Stripe test subscription
  Checkout; customer mapping is persisted before sending the user to Stripe.
- `POST /webhooks/stripe`: raw-body signature verification, reject live events,
  validate supported event shapes, enqueue once by Stripe event ID, acknowledge.
- `GET /billing/status`: tenant-scoped background job progress. `GET /health`:
  database availability and aggregate failed-job count; degraded on dead jobs.

Token convention: `input_tokens` includes `cached_input_tokens`; `output_tokens`
includes `reasoning_tokens`. Validate each subset. Count tokens as input + output;
price `(input-cached)*2 + cached*1 + output*8` micro-USD, plus 1,000 per API call.
These are pinned illustrative prices, not a claimed current provider quote.
Pro Checkout is USD 19/month; usage costs are a separate informational figure.

## Background and payment truth

The single worker runs off the HTTP path, claims jobs with a recoverable lease,
retries three times with exponential backoff, and emits a sanitized CRITICAL
failure alert plus durable failed status. Each job fetches the current Stripe
subscription rather than trusting an old event snapshot. A database-fenced lease
prevents stale workers committing over newer work. Subscription update and job
completion commit together. Unknown customers are ignored; customer/price/mode
must match. Only verified jobs can change paid plan/status. Canceled subscriptions
return to Free; past_due/unpaid/incomplete/paused produce 402.

## Verification and operational boundary

Acceptance tests cover simultaneous retries and final-slot competition, 429/402,
month rollover, exact arithmetic, tenant isolation, signed/replayed/forged events,
out-of-order snapshots, failed jobs, lease recovery, and Stripe request contracts.
Real Stripe Checkout, CLI delivery/replay, public repository history, and portal
submission require external evidence and must not be claimed from local mocks.
One process/worker and SQLite are intentional capstone limits; no real AI call,
payment credentials in source, or production deployment is needed.

