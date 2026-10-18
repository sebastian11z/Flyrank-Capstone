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

