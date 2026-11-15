# Build log

I worked on this capstone as Sebastian Garcia, a FlyRank AI backend intern. My role was prompting, working through the system design with Codex, and completing the hosted Stripe test Checkout. Codex proposed FastAPI and SQLite, drafted the design document, and wrote most of the code, tests, and documentation. I used follow-up prompts to understand how the implementation worked and to connect it back to the capstone requirements.

## October 18, 2026

### Planning and design

I prompted Codex to work from the full brief and asked for detailed explanations alongside the build. I worked through how the design separates request handling, billing rules, and persistence. The part I wanted to understand was how a tenant can retry the same request without being charged twice. In this design, the database checks for an earlier result, checks both quotas, and records the event within one transaction. The unique tenant-and-key constraint is a second safeguard. [Design](DESIGN.md)

### Metering, payments, and debugging

Codex implemented the database migrations, the metering and quota checks, Stripe Checkout, and the webhook worker. The tests caught several problems in the first implementation:

- The Stripe SDK returned resource objects. Codex had treated them like dictionaries and used the wrong conversion method. The correction uses the SDK's `.to_dict()` method.
- The amount and event ID were correct on a retry, but the JSON field order changed. The correction preserves the original response bytes on replay.
- Stripe rejected the Checkout card-filter parameter during the real test setup. Codex changed it to `allowed_payment_method_types` and updated the request-contract test.
- The quota reset header was one second too high when the remaining time was already a whole number. The correction rounds up only when needed.

I asked for help configuring Stripe, then completed the hosted Checkout myself using a test card. The signed webhook reached the app through Stripe CLI, and the worker upgraded the demo tenant from Free to Pro. Codex verified duplicate replay and forged-signature rejection, then exercised update and cancellation on the test subscription. The tenant returned to Free and its usage stayed intact. [Acceptance evidence](EVIDENCE.md)

## November 15, 2026

### Code walkthrough and submission

I used follow-up prompts to break down parts of the code I found confusing, including the database context manager, exception handling, and FastAPI's thread pool. The database context manager centralizes commit, rollback, and connection cleanup. I compared the webhook with JavaScript's `await fetch` to understand where it waits. The webhook waits for the SQLite job to be saved on a thread-pool thread before acknowledging Stripe. The separate worker handles the subscription lookup and update afterward. [Codebase guide](CODEBASE_GUIDE.md)

One billing example ties the pricing rules together for me. With 1,000 input tokens including 400 cached tokens, and 500 output tokens including 200 reasoning tokens, the quota increases by 1,500 tokens. The stored cost is 6,600 micro-USD. The cached portion has its own rate, and the output rate already includes the reasoning tokens. [Pricing and worked example](README.md#pinned-pricing-and-money-arithmetic)

Codex helped with the final documentation and corrected test discovery after the local run picked up a second checkout under `tmp/`. The submission uses simulated token counts and Stripe test-mode subscriptions. The [README](README.md) has the run commands and limitations, and [EVIDENCE.md](EVIDENCE.md) records the checks.
