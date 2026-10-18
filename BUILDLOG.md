# Build log (AI assistance)

## 2026-10-18 - Phase 1: brief and design

- Codex read the ten-page capstone PDF and visually checked its requirements,
  submission table, and evaluation pages. Sebastian requested implementation
  against all criteria and detailed teaching support.
- AI selected the permitted Python/FastAPI + SQLite stack and drafted DESIGN.md.
  The often-missed shared requirement is a durable background job with retries and
  a failure alert; Stripe synchronization will be that job.
- The workspace initially contained no brief. A subsequent check found it. The
  initial missing-file report is no longer a blocker.
- No claim that Sebastian independently authored or understands this code is made.
  He must review it and record his own changes and explanations before submission.
- Stripe and GitHub are available to Sebastian; repository URL is pending.

## 2026-10-18 - Phase 2: core billing

- AI implemented migrations, hash-based tenant authentication, integer pricing,
  atomic metering, immutable retry responses, 429/402 semantics, and safe seeding.
- Verified `python -m pytest tests/test_metering.py -q`: **16 passed in 13.82s**.
  The tests include 24 concurrent retries, a real 1,000-call quota boundary,
  concurrent competition for the final token, and UTC month rollover.
- Token input/output counts are inclusive; cached/reasoning counts are subsets.
  A worked example costs exactly 6,600 micro-USD, including the API call.
- Inspected Sebastian's supplied GitHub repo: public and empty. No existing work
  needs merging. The design commit precedes implementation.
- No real Stripe Checkout has been tested at this point.

## 2026-10-18 - Phase 3: Stripe adapter and durable synchronization

- AI implemented hosted test Checkout, durable Checkout attempt IDs, signature
  verification through the official SDK, and a persistent webhook inbox. Current
  Stripe subscription retrieval avoids blindly applying out-of-order snapshots.
- The worker uses recoverable leases, fencing tokens, three exponential-backoff
  attempts, CRITICAL failure alerts, and tenant-scoped job visibility.
- The first payment test run found real AI mistakes: Stripe SDK v16 resources
  are not dictionaries and use `.to_dict()`, not `.to_dict_recursive()`. The
  official SDK transport test caught the same problem in price validation.
- A byte-for-byte HTTP replay test also found JSON field order changed on retry.
  The stored response now preserves the original field order.
- After these fixes, `python -m pytest tests/test_http_payments.py tests/test_jobs.py
  -q` reported **31 passed in 2.99s**, with one upstream TestClient deprecation
  warning. Those are synthetic events and fake Stripe transport, not live proof.
- Actual Stripe Checkout/CLI evidence remains pending local account configuration.

## 2026-10-18 - Phase 4: evidence, teaching, and final local checks

- AI added the exact run/seed/test commands, evaluator manifest, README diagram,
  detailed TUTORIAL.md, and requirement-by-requirement EVIDENCE.md. Actual Stripe
  and human steps are explicitly unchecked instead of being represented by mocks.
- A final boundary review caught an off-by-one Retry-After error at whole seconds.
  It now uses ceiling rather than unconditional +1, with a Dec 31 last-second test.
- Actual full run: `python -m pytest -v --tb=short`: **59 passed, 1 warning in
  15.19s**. The warning is upstream TestClient/httpx deprecation, not a failed test.
- Actual `python -m app` boot, `/health` 200, `/openapi.json` 200, and live HTTP
  `scripts/probe.py core` passed: two byte-identical responses, one new event,
  exactly +6,600 micro-USD. Event ID is recorded in EVIDENCE.md.
- `pip check` reported no broken requirements. Repeated seed preserved data.
  Tracked-file scan found no local `.env` credential values. Local DB, credentials,
  source brief and downloaded tools remain outside Git.
- Official Stripe CLI v1.54.0 was downloaded into ignored `tmp/stripe/bin`; its
  archive SHA-256 matched the GitHub release asset digest before extraction.
- The local `.env` still has no Stripe key, price or webhook secret at this check.
  Sebastian was asked to configure them locally using the README; no secret was
  requested in chat. Live Checkout is still unverified.
- GitHub Actions is configured for clean Ubuntu and Windows Python 3.12 checks.
  Run 39130480338 succeeded on both OSes for revision 77519f9, with independent
  installation of all pinned dependencies and the full acceptance suite.
- A fresh public clone also completed seed, migrations, documented server boot,
  and the live duplicate/pricing HTTP probe. It reused the isolated dependency
  environment; clean dependency installation is independently proven by CI.

## 2026-10-18 - Guided real Stripe setup

- Sebastian requested help configuring Stripe. His local test secret authenticated
  successfully; the configured price value was not a Stripe Price ID. AI created
  or reused the FlyRank Pro test product and exact USD 19/month licensed price,
  then saved the valid ID in ignored `.env`.
- The CLI obtained its test webhook signing secret using the local API key through
  the child process environment. The secret was captured and written directly to
  `.env`, without displaying it. The listener is connected using API version
  2026-09-30.endive; its startup secret is redacted from tool output.
- After restarting the API, a real forged-signature request returned 400; tenant A
  remained Free with zero webhook jobs.
- Real Checkout creation exposed another AI integration mistake: the current
  Stripe API rejects `payment_method_types`. The adapter now uses the documented
  `allowed_payment_method_types` card filter. The SDK contract test checks that
  field; all 29 HTTP/payment tests passed in 3.57s.
- Stripe retained the prior failed request under its idempotency key. Before
  replacing the failed local intent, AI queried its customer and proved zero
  Checkout sessions and zero subscriptions. Only that uncompleted intent was
  reset; usage and subscription state were preserved.
- The corrected live API request created hosted test Checkout and opened it in
  Sebastian's browser. Actual browser payment and signed upgrade proof are pending
  his completion; a created session alone is not presented as a successful payment.

## 2026-10-18 - Real Stripe acceptance completed

- Sebastian completed hosted test Checkout and confirmed it in chat. Stripe
  reported session complete / payment paid; CLI delivered the real signed
  checkout.session.completed event. The worker processed it once and GET /usage
  showed Pro limits of 10,000 calls and 1,000,000 tokens.
- The real event was retrieved from Stripe and replayed twice with the local
  listener signature. Both returned duplicate; its done job stayed at attempts=1.
  A forged request while Pro returned 400 and left the complete billing state
  unchanged. Exact event IDs and sanitized outputs are in EVIDENCE.md.
- AI scheduled cancellation on this owned test subscription to generate a real
  subscription.updated event, then canceled it without proration/invoice-now to
  generate subscription.deleted. Both arrived through CLI and processed once.
  The local state returned to Free with its original one usage event and 6,600
  micro-USD cost intact. No local plan was manually changed.
- CI run 39131512651 verified revision 5f5b0a1 on Ubuntu (59 passed in 4.38s)
  and Windows (59 passed in 12.58s); both emitted only the existing upstream
  TestClient deprecation warning.
- All technical Section 6 checkboxes and the four phase gates now have actual
  evidence. Sebastian still needs to review/explain the code honestly and submit
  the repository through the portal; AI cannot assert those human steps happened.

## 2026-10-18 - Beginner codebase guide

- Sebastian requested a Markdown explanation of the whole codebase and the three
  ownership questions. AI read the current source, migrations, scripts, tests and
  project documentation and wrote CODEBASE_GUIDE.md, linked from the README.
- The guide explains each maintained file, Python/HTTP/SQL vocabulary, complete
  request flows, concurrency/idempotency, inclusive token arithmetic, and verified
  webhook access changes. It includes practice questions and reasoned answers.
- This is documentation work; creating the guide does not establish that Sebastian
  has independently reviewed or understood the implementation. His ownership and
  submission checkboxes remain unchanged.

