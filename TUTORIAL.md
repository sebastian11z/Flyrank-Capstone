# Understanding and defending this capstone

Read the README first and run the core probe. Then follow the request through
the layers while reading this guide. Treat tests as experiments you can change,
not a certificate that every future change will be correct.

## 1. Define the unit of work

The core unit is one **accepted simulated generation request**. It consumes one
API call and a supplied number of AI tokens. One event stores both quantities.
It does not call a model: all hard behavior concerns trustworthy accounting.

Why one event rather than one row per token category? The evaluator asks whether
one retried action creates exactly one event. One immutable event with an explicit
breakdown makes that promise easy to inspect and its charge reproducible.
`Generation` validates the trust boundary before business logic: integers only,
no negatives, no unknown fields, cached <= input, reasoning <= output.

Exercise: send `{"input_tokens": true}`. Python normally treats `bool` as a
subclass of `int`; Pydantic's strict integer constraint is why this receives 422.
Then send `{"input_tokens": 100, "cached_input_tokens": 101}` and explain why
rejecting a contradictory breakdown is better than silently clamping it.

## 2. Authenticate a tenant rather than trusting an ID

The HTTP dependency hashes `X-API-Key` and looks up its tenant. Business logic
receives that ID; the request body cannot choose one. The seed generates random
keys in `.env`. The DB stores a SHA-256 digest, not the original key. Fast hashing
is appropriate for high-entropy random API tokens; it would be inappropriate for
human-chosen low-entropy passwords, which need a password-hashing scheme.

Follow `authenticated_tenant -> tenant_for_key -> record/usage`. SQL binds values
using `?` parameters, so values are data rather than executable SQL. Every usage
query includes the authenticated tenant. Job-status queries do too. The webhook
has a different trust boundary: a valid Stripe signature plus a known customer
mapping, never an arbitrary tenant ID supplied in metadata.

Exercise: use tenant B's key after generating for tenant A. B's `/usage` must stay
zero. Reuse A's idempotency-key string for B; it must work independently. Explain
why uniqueness on just `idempotency_key` would incorrectly couple customers.

## 3. Why check-then-insert must be one transaction

Imagine 999 calls used and two workers receiving a new call simultaneously:

```text
Without a transaction: A reads 999; B reads 999; both allow; both write -> 1001.
With BEGIN IMMEDIATE: A takes writer reservation, checks, inserts, commits.
                      B waits, then reads 1000 and rejects.
```

Inside `record`, the sequence is:

1. Find a saved event by tenant and idempotency key.
2. If found, compare its canonical request hash; replay or reject with 409.
3. Check subscription payment state, then sum accepted events for this UTC month.
4. Reject if either `used + requested > limit`.
5. Calculate integer cost, insert the event and saved result, commit.

The lock is acquired before reading quotas. A unique database constraint is a
second guard against duplicate keys. A Python dictionary or lock alone would
not survive a restart or protect another process sharing the database.

This is a precise **exactly-once database effect for accepted requests with a
stable tenant/key**, not an unconditional claim of exactly-once distributed
execution. If a future implementation calls a real model before the transaction
commits, a crash could cause an external call to run twice. That extension needs
a persisted workflow and provider-side idempotency, not just this SQLite insert.

Exercise: inspect `test_simultaneous_retries_create_one_event_and_original_response`
and `test_token_boundary_and_concurrent_last_slot`. Predict their results before
running them. Temporarily moving the quota read outside the transaction is an
experiment in an isolated branch, not a safe implementation improvement.

## 4. Retries must replay history

The first response is saved with the event. A retry returns it **before** checking
today's subscription or month. Otherwise an already-successful January action
could become a February charge or return a new quota error after a downgrade.

Canonical JSON sorts request fields and fills defaults before hashing, so `{}`
and an explicit set of zero token counts represent the same normalized action.
The saved response preserves field order too: the HTTP test compares raw bytes.
A different normalized body under an already-used key is 409, not a second event.
Rejected requests reserve no key, so a caller can retry after resolving payment
or quota. Successful keys are never recycled in this implementation.

Exercise: generate once, change a test subscription to past_due, then send the
same key/body. Explain why replay returns success while a new key returns 402.

## 5. Quotas are two independent counters

Free permits 1,000 calls and 100,000 tokens. A request is allowed at equality;
only `>` rejects. For API calls at 999, the next action succeeds and reaches
1,000. At 1,000, a new action needs one more call and gets 429. At 100,000 tokens,
an API-only action with zero new tokens can still fit the call allowance.

429 means usage exceeded the current allowance: the response explains which
meter, its used/requested/limit, and when a UTC calendar month resets. 402 means a
known paid subscription has a payment problem. Subscription cancellation falls
back to Free rather than preserving paid access forever. Neither rejection
creates a usage event. The service does not secretly reset or erase usage on an
upgrade or downgrade.

UTC months are a chosen accounting rule, independent of Sebastian's Manila time
and Stripe's renewal date. This is acceptable for the brief, but a product using
subscription-anniversary quotas would need a different period model.

## 6. Integer money and inclusive token categories

An API response may contain input=1000, cached=400, output=500, reasoning=200.
The inclusive convention says cached is already inside input and reasoning is
already inside output. Unique tokens are 1500. Adding all four numbers would
overcount usage by 600 tokens and overcharge reasoning output.

With the pinned illustrative rates, fresh input costs `(1000-400)*2 = 1200`
micro-USD; cached input costs `400*1 = 400`; output (including reasoning) costs
`500*8 = 4000`. Add the API fee of 1000: **6600 micro-USD = USD 0.006600**.
All multiplication, addition, storage, and aggregation use integers. Display uses
integer division and a six-digit remainder, not conversion through `float`.

An event stores its cost and pricing version. Monthly rollup sums stored charges.
It does not recalculate history using whatever constants happen to be installed
today. Free still has an informational usage cost; that does not mean the service
collects money from Free customers. Pro's USD 19 fee is a separate subscription.

Exercise: change only reasoning from 200 to 300 while output stays 500. Neither
token total nor cost should change. Change cached from 400 to 500; cost should
fall by exactly 100 micro-USD. These are useful properties beyond one example.

## 7. Separate accepting a webhook from doing its slow work

HTTP reads the exact raw bytes and calls Stripe's signature verifier with the
listener secret and signature header. Re-encoding JSON before verification would
change the bytes and invalidate the signature. The SDK also enforces timestamp
tolerance. Missing/bad/stale signatures receive 400 without changing the DB.
The signed event must still be test mode and have the required shape.

The handler stores a minimal job under a unique Stripe event ID, then responds.
Returning 200 before a durable insert risks losing the update forever; doing
network synchronization inline risks webhook timeouts. The worker performs the
Stripe API call off the request path, so signature validation/deduplication stays
short and auditable.

A repeated event ID does not create another job. Unknown customers are recorded
as ignored and cannot name a tenant. The job stores IDs, type, status, and retry
metadata; it does not persist whole Checkout/customer payloads or card details.

## 8. Handle event ordering and worker crashes

An old `updated` event may arrive after a `deleted` event. Copying its old active
snapshot could give Pro back to a canceled customer. The worker retrieves the
current Stripe subscription, validates its customer, test mode, price and quantity,
then updates local state. A late cancellation for a prior subscription cannot
overwrite a different current active subscription.

Claims are database transactions. The job moves from pending to processing,
increments attempts, and receives a 120-second lease plus a random fencing token.
Only one unexpired job is processing at a time. A crashed worker's expired lease
can be reclaimed on restart. A worker completing after another claim cannot
commit because its token no longer matches. Expired work is reclaimed before
later queued work, preserving the single-in-flight assumption during recovery.

The plan/status update and `done` marker commit in **the same transaction**. If
the process dies before commit, neither change persists and the job can retry.
If it dies after commit, the job is done and a duplicate delivery is ignored.
After three failures the job becomes failed, a CRITICAL log alerts the local
operator, and health degrades. The operator fixes the cause before requeueing.

Exercise: read the stale-worker test. Point to the SQL condition that blocks an
old worker's write. Explain why a lease timeout without a fencing check is weaker.

## 9. Checkout is also an external side effect

Checkout attempts are persisted before calling Stripe. A stable attempt ID is
sent as Stripe's idempotency key. If Stripe creates a session but the connection
drops before its response, a retry uses the same key instead of purchasing again.
The customer mapping is saved before the hosted Checkout is returned, so arriving
webhooks can identify the tenant. Returning from the browser does not grant Pro;
only the verified background sync can do that.

Provider idempotency has a retention limit. An ambiguous unsaved session older
than 23 hours is blocked for reconciliation instead of silently using an expired
key. We avoid pretending local transactions can atomically commit with Stripe.
This app supports one subscription per tenant; already-paid tenants cannot use
Checkout to buy another subscription.

## 10. What you need to prove yourself

Run the five published acceptance probes, then inspect their evidence. Local
synthetic signatures exercise real SDK verification, but they do not prove that
your Stripe account, product, CLI listener, and browser Checkout are configured
correctly. Complete that flow and paste sanitized actual results in EVIDENCE.md.

Before submitting, explain these selected pieces without reading a prepared answer:

- Why the duplicate lookup happens before quota/payment checks.
- Why token categories are not all added together.
- What `BEGIN IMMEDIATE`, the unique tenant/key constraint, and a fencing token
  each protect, and what they cannot protect outside this service.
- Why signing-secret configuration differs between CLI and Dashboard endpoints.
- Where the 429/402 decision is made and how a canceled subscription differs.
- What a queue job records, how it survives restart, and how failure is visible.

Record AI assistance and your own corrections honestly in BUILDLOG.md. A
defensible claim is narrower and stronger than an exaggerated one: this service
proves tenant/key-scoped atomic metering and tested payment-sync behavior; it
does not prove production scale, universal exactly-once external execution, or
completed live Stripe integration until actual evidence exists.
