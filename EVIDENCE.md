# Acceptance evidence

Run date: **2026-10-18 (Asia/Manila)**. Accounting periods are UTC.
Status: **local implementation verified; real Stripe flow and intern submission
not yet proven**. No pending external step is presented as completed.

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

- [ ] **Real test-mode subscription Checkout works end to end.**

  Local implementation evidence, not a replacement for actual Checkout:

  ```text
  tests/test_http_payments.py::test_checkout_and_signed_webhook_upgrade_then_replay PASSED
  tests/test_http_payments.py::test_official_sdk_encodes_checkout_and_customer_contract PASSED
  tests/test_http_payments.py::test_checkout_unknown_result_reuses_durable_attempt_after_network_failure PASSED
  ```

  These prove Free -> Pro on a valid locally signed event, 10,000/1,000,000 new
  limits, official SDK request encoding and stable provider idempotency keys.
  They do not prove Stripe account/product/CLI/browser configuration.

  **Pending actual proof:** initial Free `/usage`, Stripe CLI delivery/event ID
  for this tenant's successful Checkout, `done` job, Pro `/usage`. Do not paste
  secrets, signatures, raw customer payloads, card details, or Checkout URLs.

- [ ] **Real signed webhook delivery/replay and plan/status synchronization.**

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

  **Pending actual proof:** bad-signature live HTTP 400 and unchanged plan,
  initial CLI delivery, two replays of an actual test event with unchanged job,
  and a Dashboard update/cancel followed by the corresponding signed sync.

  **Guided setup progress (actual account, 2026-10-18):** the test API key
  authenticated, the active USD 19/month licensed Pro price passed validation,
  and the Stripe CLI listener connected using API version 2026-09-30.endive.
  The app was restarted to load the local signing secret. Actual HTTP output:

  ```text
  GET /health: 200 {"status":"ok","failed_background_jobs":0}
  POST /webhooks/stripe with Stripe-Signature: invalid
  400 {"error":"invalid_webhook","message":"Invalid payload or Stripe signature"}
  GET /billing/status: {"tenant_id":"tenant_a","plan":"free","subscription_status":"free","jobs":[]}
  ```

  The forged-webhook portion is now proven against the real running configuration.
  A real test Checkout session was also created and opened in the browser after
  correcting the API's card-filter parameter to `allowed_payment_method_types`.
  The updated SDK contract and all 29 HTTP/payment tests passed. Payment completion,
  upgrade, real event replay and cancellation are still pending; this checkbox
  remains unchecked.

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
- [ ] Phase 3 **real** Checkout upgrade gate; implementation commit `8405602`
  is not proof of the live account flow.
- [x] Phase 4 cost gate locally: live `/usage` is exactly USD 0.006600 for the
  worked event; proof above. Five required files are present in the fresh clone,
  and secret/exclusion checks passed before pushing.
- [ ] Sebastian reviews selected code, can explain 2-3 arbitrary lines, and adds
  his own honest changes/understanding to BUILDLOG.md.
- [ ] Sebastian submits the public repository URL through the internship portal.

Optional stretch goals are intentionally excluded. Neither submission nor live
Stripe verification is claimed complete by this document.
