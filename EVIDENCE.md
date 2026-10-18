# Acceptance evidence

Run date: **2026-10-18 (Asia/Manila)**. Accounting periods are UTC.
Status: **local implementation and real Stripe test-mode acceptance verified**.
Sebastian's code explanation and portal submission are separate, unfinished human
steps. No live-mode payment was made and no pending step is presented as completed.

## Actual local test run

Command: `python -m pytest -v --tb=short`

```text
======================= 59 passed, 1 warning in 15.19s ========================
```

The warning is Starlette's deprecation of its `httpx` TestClient compatibility
path. The pinned versions still run successfully. Tests use temporary SQLite
databases; fake Stripe data/transport does not contact Stripe or spend money.

## Section 6: one proof per checkbox

### Metering

- [x] Accepted billable action creates exactly one event under retries.

  ```text
  tests/test_metering.py::test_simultaneous_retries_create_one_event_and_original_response PASSED
  tests/test_http_payments.py::test_http_idempotency_response_identity_and_isolation PASSED
  ```

  The first test sends 24 calls through 12 concurrent threads, asserting one event
  and all responses equal. The HTTP test also compares response bytes and checks
  a conflicting body under the same key returns 409. SQL uniquely indexes
  `(tenant_id,idempotency_key)` and the check/insert share `BEGIN IMMEDIATE`.

- [x] Pasted double-counting proof.

  Actual live server, started with `python -m app`; command
  `python scripts/probe.py core`:

  ```text
  POST /generate twice: 200, byte-identical responses, one new event, +6600 micro-USD
  event_id: 6687ab8b-7088-4845-a14c-d10d4f65d7ce
  tenant_id: tenant_a
  period: 2026-10
  GET /usage used.events: 1
  GET /usage used.api_calls: 1
  GET /usage used.ai_tokens: 1500
  GET /usage cost_micro_usd: 6600
  GET /usage cost_usd: 0.006600
  ```

  This protects accepted database effects scoped to tenant/key, not arbitrary
  external side effects. No actual AI provider call occurs.

### Quotas

- [x] Requests over plan limits are rejected before recording usage.

  ```text
  tests/test_metering.py::test_exact_api_call_boundary_then_429 PASSED
  tests/test_metering.py::test_token_boundary_and_concurrent_last_slot PASSED
  ```

  The API boundary test records all 1,000 allowed calls, then asserts rejection
  and unchanged event count. The token test reaches 99,999 and races ten requests
  for the last token: exactly one 200, nine 429, final total 100,000.

- [x] Correct 429/402 responses and explanations.

  ```text
  tests/test_http_payments.py::test_http_quota_and_payment_status_codes PASSED
  tests/test_metering.py::test_402_and_retry_still_returns_original PASSED
  tests/test_metering.py::test_retry_after_exact_last_second_and_year_rollover PASSED
  ```

  HTTP assertions check message, used/limit and Retry-After on 429, and message
  on past_due 402. At 2026-12-31 23:59:59 UTC the reset is 2027-01-01 00:00:00
  and Retry-After is exactly 1, not 2. A successful old key still replays under 402.

### Cost calculation

- [x] Monthly tenant usage rolls into a cost figure.

  ```text
  tests/test_metering.py::test_exact_pricing_and_monthly_rollup PASSED
  tests/test_metering.py::test_utc_month_rollover_retains_old_idempotency PASSED
  ```

  The live `/usage` output above independently reports the exact same charge.
  January usage is excluded from February, but January retry history remains.

- [x] Cached input, reasoning and output pricing are correct.

  ```text
  tests/test_metering.py::test_exact_pricing_and_monthly_rollup PASSED
  tests/test_metering.py::test_pricing_subset_properties_and_immutable_historical_cost PASSED
  ```

  Input=1,000 includes cached=400; output=500 includes reasoning=200.
  Unique tokens=1,500. Cost=1,000 + (1,000-400)*2 + 400*1 + 500*8 = **6,600
  micro-USD = USD 0.006600**. More reasoning within unchanged output costs no
  extra; replacing 100 fresh tokens with cached saves 100 micro-USD.

- [x] Pricing pinned in config with exact-total proof.

  `app/config.py`: version `illustrative-usd-v1`, API=1,000, fresh=2, cached=1,
  output=8 micro-USD. The preceding worked total and test output are the proof.
  Events retain costs/version; the immutable-history test changes today's input
  constant and asserts the already-recorded rollup charge remains unchanged.

### Stripe integration

- [x] **Real test-mode subscription Checkout works end to end.**

  Local implementation evidence, not a replacement for actual Checkout:

  ```text
  tests/test_http_payments.py::test_checkout_and_signed_webhook_upgrade_then_replay PASSED
  tests/test_http_payments.py::test_official_sdk_encodes_checkout_and_customer_contract PASSED
  tests/test_http_payments.py::test_checkout_unknown_result_reuses_durable_attempt_after_network_failure PASSED
  ```

  These prove Free -> Pro on a valid locally signed event, 10,000/1,000,000 new
  limits, official SDK request encoding and stable provider idempotency keys.
  They do not prove Stripe account/product/CLI/browser configuration.

  **Actual Stripe/browser/CLI proof, 2026-10-18:** Sebastian completed the hosted
  test Checkout and confirmed completion in chat. Stripe returned session status
  `complete`, payment status `paid`, and test mode. Initial tenant state was
  `free`, status `free`, limits 1,000 calls / 100,000 tokens. Actual delivery:

  ```text
  21:26:21 --> checkout.session.completed [evt_1URu7582vP3MRlfw0LkKBvQa]
  21:26:21 <-- [200] POST http://127.0.0.1:8000/webhooks/stripe [evt_1URu7582vP3MRlfw0LkKBvQa]
  GET /billing/status: plan=pro, subscription_status=active
  job: event_id=evt_1URu7582vP3MRlfw0LkKBvQa, status=done, attempts=1, last_error_type=null
  GET /usage: plan=pro, limit={"api_calls":10000,"ai_tokens":1000000}
  GET /usage: used.events=1, used.ai_tokens=1500, cost_micro_usd=6600
  ```

  The product's active USD 19/month licensed test price was independently
  retrieved and validated. No secrets, card details, raw customer payloads or
  hosted Checkout URLs are included in this evidence.

- [x] **Real signed webhook delivery/replay and plan/status synchronization.**

  Local verification is complete:

  ```text
  tests/test_http_payments.py::test_forged_stale_or_changed_signature_changes_nothing PASSED
  tests/test_http_payments.py::test_checkout_and_signed_webhook_upgrade_then_replay PASSED
  tests/test_jobs.py::test_background_updates_payment_and_cancellation_from_current_truth PASSED
  tests/test_jobs.py::test_out_of_order_snapshot_cannot_resurrect_canceled_subscription PASSED
  tests/test_jobs.py::test_late_old_subscription_deletion_cannot_undo_new_active_one PASSED
  ```

  Local forged/modified/stale signatures return 400 and leave zero jobs; a valid
  duplicate event returns `duplicate` with one `done` job and attempts=1.
  All three required event types have exercised paths. Canonical active,
  past_due, unpaid and canceled state is tested. Old snapshots cannot undo current
  Stripe truth. Wrong customer/mode/price/quantity cannot grant Pro.

  **Actual account proof:** Stripe CLI connected using API version
  2026-09-30.endive. Raw-body verification used the locally captured signing
  secret. A forged request was checked both before Checkout and while Pro:

  ```text
  GET /health: 200 {"status":"ok","failed_background_jobs":0}
  POST /webhooks/stripe with Stripe-Signature: invalid
  400 {"error":"invalid_webhook","message":"Invalid payload or Stripe signature"}
  Initial GET /billing/status: {"tenant_id":"tenant_a","plan":"free","subscription_status":"free","jobs":[]}
  After Checkout: forged webhook returned 400; Pro plan and all processing state unchanged
  ```

  The real Checkout event was retrieved from Stripe and replayed twice with
  `python scripts/replay_event.py evt_1URu7582vP3MRlfw0LkKBvQa`:

  ```text
  POST /webhooks/stripe evt_1URu7582vP3MRlfw0LkKBvQa: 200 duplicate
  POST /webhooks/stripe evt_1URu7582vP3MRlfw0LkKBvQa: 200 duplicate
  {"event_id":"evt_1URu7582vP3MRlfw0LkKBvQa","event_type":"checkout.session.completed","status":"done","attempts":1,"last_error_type":null}
  Real test event replayed twice; original processing state unchanged.
  ```

  The initial authentic delivery was through Stripe CLI. The replay helper signs
  that retrieved real event locally with the listener secret; it does not claim
  the CLI performed the two replay POSTs.

  The test subscription was then scheduled for end-of-period cancellation through
  Stripe's API to generate an update, and finally canceled immediately to exercise
  deletion. Before each mutation, test mode and customer ownership were checked.
  Neither mutation wrote the local plan directly. Actual signed deliveries:

  ```text
  21:27:31 --> customer.subscription.updated [evt_1URu8D82vP3MRlfw6EJl3QOR]
  21:27:31 <-- [200] POST http://127.0.0.1:8000/webhooks/stripe [evt_1URu8D82vP3MRlfw6EJl3QOR]
  job: status=done, attempts=1, last_error_type=null
  GET /billing/status: plan=pro, subscription_status=active

  21:27:46 --> customer.subscription.deleted [evt_1URu8S82vP3MRlfwSpwwVR8E]
  21:27:46 <-- [200] POST http://127.0.0.1:8000/webhooks/stripe [evt_1URu8S82vP3MRlfwSpwwVR8E]
  job: status=done, attempts=1, last_error_type=null
  GET /billing/status: plan=free, subscription_status=free
  GET /usage: limit={"api_calls":1000,"ai_tokens":100000}, used.events=1, cost_micro_usd=6600
  ```

  The final Free state is intentional: this acceptance run canceled its test
  subscription. Usage and cost history remained intact. Creating Checkout again
  is available for a later demo; no manual Pro grant was added.

### Data model, tests and documentation

- [x] Required tables exist and customer data is tenant-isolated.

  ```text
  tests/test_metering.py::test_tenant_isolation_persistence_migrations_and_safe_reseed PASSED
  tests/test_http_payments.py::test_http_idempotency_response_identity_and_isolation PASSED
  tests/test_jobs.py::test_unknown_customer_is_ignored_and_deduplicated PASSED
  ```

  Migrations define tenants/plans/subscriptions/usage_events and payment workflow
  tables. Reopening connections preserves events, repeating migrations and seed
  does not reset data, and B's usage/job listing cannot see A's records.

- [x] README, architecture diagram, setup instructions and required pack files.

  Actual commands run successfully:

  ```text
  python -m app.seed
  Seeded tenant_a and tenant_b without resetting usage or subscriptions. Keys are in .env; never commit it.
  python -m app
  INFO: Application startup complete.
  INFO: Uvicorn running on http://127.0.0.1:8000
  GET /health: 200 {"status":"ok","failed_background_jobs":0}
  GET /openapi.json: 200
  python -m pip check
  No broken requirements found.
  ```

  README includes exact commands, ASCII architecture, quotas, pricing and honest
  limitations. `capstone.yaml`, `EVIDENCE.md`, `BUILDLOG.md`, `.env.example` are
  present. `TUTORIAL.md` provides an engineering walkthrough and exercises.
  GitHub Actions checks the same tests on clean Ubuntu and Windows runners;
  CI succeeded on both OSes for revision `77519f9`:
  [Acceptance run 39130480338](https://github.com/sebastian11z/Flyrank-Capstone/actions/runs/39130480338).
  Both runners installed the pinned dependencies and ran the full test suite.

  A fresh public clone was also seeded and booted with `python -m app`, then
  exercised over HTTP with `scripts/probe.py core`. Actual output:

  ```text
  Fresh public clone: all 5 submission files present; migrations and seed succeeded
  POST /generate twice: 200, byte-identical responses, one new event, +6600 micro-USD
  event_id: 1939c86a-b6a1-49fb-86aa-1c3495aed032
  GET /usage used.events: 1; cost_micro_usd: 6600
  ```

  This clone used the existing isolated dependency environment; independent
  clean dependency installation is covered by the two GitHub CI runners.

## Shared requirements (Section 12)

| Requirement | Actual proof/status |
|---|---|
| Layered architecture | `app/db.py`/migrations; logic in metering/payments/jobs; HTTP in main; diagram in README |
| Boundary validation, clean 4xx | `test_bad_input_is_4xx_not_500` and `test_auth_headers_and_validation_do_not_echo_credentials` passed; signed-invalid payload cases passed |
| Background job, retries, failure alert | `test_worker_runs_off_http_path_and_health_exposes_exhausted_jobs`, `test_durable_job_retries_then_failure_alert_without_secrets`, `test_three_crashed_attempts_alert_and_operator_retry` passed |
| Persistence, migrations, indexes, tenant isolation | migration/reseed/reopen and cross-tenant tests above; SQL schema inspected |
| Idempotency | 24 concurrent retries; official SDK Checkout keys; deduplicated webhook inbox |
| Secrets clean | `.env`, DB and local CLI ignored; tracked-file scan found no actual local credential values; DB stores only API-key digests; log-redaction test passed |
| AI cost/budget if AI used | No real AI model is used; simulated token costs attributed per event/tenant, token quota guard tested |

Retry worker proofs also include persisted lease recovery, stale-worker fencing,
2/4-second backoff and CRITICAL logging after three failures. `/health` exposes
exhausted jobs as 503. Failure alerts are local stderr/health signals, not external
email/paging. Exception text is deliberately omitted to avoid leaking credentials.

## Repository, phase gates, and remaining human steps

- [x] Dedicated public repository inspected before writing remote contents:
  `https://github.com/sebastian11z/Flyrank-Capstone`; initially public and empty.
- [x] Design commit precedes implementation: `7a0fd24`.
- [x] Phase 2 core metering/quota gate: commit `c833ea6`, tests above.
- [x] Phase 3 **real** Checkout upgrade gate: actual hosted Checkout, signed CLI
  event, Pro limits, replay and cancellation evidence above. Live API compatibility
  fix is in `5f5b0a1`; its complete CI suite passed on both OSes.
- [x] Phase 4 cost gate locally: live `/usage` is exactly USD 0.006600 for the
  worked event; proof above. Five required files are present in the fresh clone,
  and secret/exclusion checks passed before pushing.
- [ ] Sebastian reviews selected code, can explain 2-3 arbitrary lines, and adds
  his own honest changes/understanding to BUILDLOG.md.
- [ ] Sebastian submits the public repository URL through the internship portal.

Optional stretch goals are intentionally excluded. Live Stripe test-mode
verification is complete; intern understanding and portal submission are not
claimed complete by this document.
