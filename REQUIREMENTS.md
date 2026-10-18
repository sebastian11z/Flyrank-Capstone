# Completion checklist from the capstone brief

Every row needs current evidence in EVIDENCE.md. Unchecked means unproven.

Current status (2026-10-18): all technical core requirements and phase gates have
local tests and actual Stripe acceptance evidence in EVIDENCE.md. Sebastian's
code explanation/ownership review and portal submission remain human steps.

| Source | Requirement | Planned proof |
|---|---|---|
| 6 / probe 1 | One event under retries; original response returned | HTTP + concurrent DB tests |
| 6 / probe 2 | Plan quotas, exact boundary, explained 429 and 402 | Boundary/concurrency/status tests |
| 6 / probe 5 | Monthly tenant rollup, cached/reasoning rules, pinned integer prices | Known totals + rollover tests |
| 6 / probe 3 | Real Stripe test Checkout upgrades Free to Pro | Actual Checkout + CLI transcript (external) |
| 6 / probe 4 | Signatures, duplicate suppression, subscription updates | SDK signature tests + actual CLI replay (external) |
| 6 | Tenants/plans/subscriptions/events; tenant isolation | Migrations + cross-tenant tests |
| 6 / 10 | README, diagram, exact setup/run/seed, honest limitations | Clean database setup + command smoke test |
| 10 / 12 | capstone.yaml, EVIDENCE.md, BUILDLOG.md, .env.example | File/manifest checks |
| 12 shared 1 | Separate data, logic, HTTP | Source review and DESIGN.md |
| 12 shared 2 | Bad input yields clean 4xx | Invalid input/auth/payload tests |
| 12 shared 3 | Background work, retries, failure alert | Durable queue/restart/retry/alert tests |
| 12 shared 4 | Real persistence, migrations, indexes, isolated tenants | Migration/reopen/SQL tests |
| 12 shared 5 | Idempotency | Metering and webhook tests |
| 12 shared 6 | Env-only secrets, no logged/stored plaintext secrets | Auth hashes + redaction + tracked-file scan |
| 12 shared 7 | Per-call attributed AI cost and budget guard if AI used | No model called; simulated cost + quota proofs |
| 3 / 8 | One-page design committed before build; four phase gates | Local Git history + live Stripe gate (external) |
| 10 | Dedicated public repo, meaningful phase commits | GitHub visibility and commit inspection (external) |
| 11 | Submit repository URL through internship portal | Sebastian's submission (external) |
| 3 | Honest AI log and ability to explain chosen code | BUILDLOG.md + Sebastian's walkthrough (external) |

Core scope: two plans, two metered quantities, one dummy generation endpoint.
Optional stretch goals are excluded. Automated tests are included because billing
correctness needs direct evidence; a test pass is not a real payment-flow proof.
