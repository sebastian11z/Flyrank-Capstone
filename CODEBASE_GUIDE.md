# A beginner's guide to the FlyRank billing codebase

This guide explains the actual files in this repository, how they work together,
and why the important decisions were made. Read it with the source files open.
You do not need to understand every Python symbol before starting.

The three questions we are working toward are:

1. How do retries avoid duplicate usage, even when requests arrive together?
2. Why are cached input and reasoning tokens not counted twice?
3. Why does a verified Stripe webhook grant Pro access instead of the Checkout
   success page?

The detailed answers are in [Section 10](#10-the-three-concepts-to-explain-yourself).
Start with the earlier sections if the vocabulary or code is unfamiliar. Sample
answers are learning aids: practice explaining the reasoning in your own words.

## Contents

- [1. What this project actually does](#1-what-this-project-actually-does)
- [2. The vocabulary you need](#2-the-vocabulary-you-need)
- [3. How to read the Python syntax](#3-how-to-read-the-python-syntax)
- [4. The complete project map](#4-the-complete-project-map)
- [5. Following a request through the system](#5-following-a-request-through-the-system)
- [6. Every application file](#6-every-application-file)
- [7. Every database migration](#7-every-database-migration)
- [8. Every script and test file](#8-every-script-and-test-file)
- [9. Every configuration and documentation file](#9-every-configuration-and-documentation-file)
- [10. The three concepts to explain yourself](#10-the-three-concepts-to-explain-yourself)
- [11. Practice without guessing](#11-practice-without-guessing)
- [12. Limits and claims you can defend](#12-limits-and-claims-you-can-defend)

## 1. What this project actually does

Imagine that you operate an online service. Different companies use your service.
You need to answer three questions for each company:

- How much has it used this month?
- Does its plan allow another request?
- What is the calculated cost of its recorded usage?

This backend answers those questions. It also lets a company buy a simulated Pro
subscription through Stripe's test environment and updates the company's access
when a verified Stripe message arrives.

There are two plans:

| Plan | Allowed API calls per UTC calendar month | Allowed AI tokens per UTC calendar month |
|---|---:|---:|
| Free | 1,000 | 100,000 |
| Pro | 10,000 | 1,000,000 |

One accepted generation request consumes one API call plus its supplied token
counts. The generation is simulated: the backend does not contact an AI model or
produce a real AI answer. You supply counts so the accounting rules can be tested
without paying an AI provider.

The project calculates usage costs, but does not collect those usage costs through
Stripe. Pro's USD 19 monthly subscription price is a separate figure. For example,
`cost_usd: "0.006600"` means recorded usage has that calculated cost; it does not
mean Stripe charged the user that amount.

Think of the system as a careful accountant connected to a subscription provider.
The accountant must recognize repeat paperwork, enforce allowances, and keep an
accurate record even when connections fail.

## 2. The vocabulary you need

| Term | Meaning in this project |
|---|---|
| Backend | The program that receives requests, applies rules, and stores data. |
| Client | A program that sends a request to the backend: a script, browser, or test. |
| API | The agreed ways another program can communicate with the backend. |
| Endpoint or route | An address and HTTP method, such as `GET /usage`. |
| HTTP | The request/response communication used by the API. |
| `GET` | An HTTP method used here to read information. |
| `POST` | An HTTP method used here to request an action or deliver an event. |
| Header | Extra request information, such as an API key or idempotency key. |
| JSON | A text format for structured data, such as `{"plan":"free"}`. |
| Tenant | One customer organization. Tenant A's records must stay separate from B's. |
| Authentication | Establishing which tenant a caller belongs to using its API key. |
| Plan | A named collection of limits: Free or Pro. |
| Subscription | A tenant's current plan, payment status, and Stripe subscription reference. |
| Quota | The amount a plan permits during an accounting period. |
| Metering | Recording how much was used. |
| Usage event | One stored record of an accepted simulated generation. |
| Idempotency | Repeating the same identified action has one recorded effect. |
| Database | Persistent structured storage. This project uses SQLite. |
| SQL | The language used to create, read, and modify database records. |
| Migration | A versioned SQL file that creates or changes the database structure. |
| Transaction | A group of database steps that commits together or rolls back. |
| Commit | Save a database transaction; in Git, record a version of files. These are different uses of the same word. |
| Rollback | Undo the uncommitted database changes when a transaction fails. |
| Rollup | Add individual events together to produce a summary. |
| Webhook | An HTTP message sent by Stripe to tell our backend about an event. |
| Signature | A cryptographic check of a webhook's bytes and authenticity. |
| Background worker | A separate thread that processes queued Stripe work outside the HTTP handler. |
| Queue or inbox | Stored jobs waiting for processing. Here, it is a database table. |
| SDK | A library that provides Python methods for calling another service's API. |
| UTC | The timezone used to decide accounting months, regardless of the viewer's timezone. |
| Environment variable | A named setting available to a running process. `.env` supplies local values. |

A tenant API key and an idempotency key do different jobs. `X-API-Key` identifies
the customer organization; `Idempotency-Key` identifies one action by that
organization. Reusing an action key is how a retry says, "This is the same work."

## 3. How to read the Python syntax

These patterns appear throughout the source.

### Variables, functions, and return values

```python
tokens = request.input_tokens + request.output_tokens
```

The expression on the right is calculated and assigned to the name on the left.
The dot means "access something belonging to this object." Here, `request` holds
the validated input fields.

```python
def cost_of(request: Generation):
    return ...
```

`def` defines a function: a named set of steps. A parameter, here `request`, is
information given to that function. `return` sends a result back to its caller.
The `: Generation` annotation describes the expected kind of input. Type hints
alone do not validate arbitrary data; Pydantic performs the boundary validation
used by this application.

### Conditions and loops

```python
if current + requested > limit:
    raise DomainError(...)
```

`if` runs the indented block only when the condition is true. `>` means greater
than, so equality is allowed. `raise` interrupts normal execution with an error
that a higher layer can handle.

`for` repeats a block for each item. The quota loop runs the same comparison once
for API calls and once for AI tokens.

### Dictionaries and lists

```python
{"plan": "pro", "api_calls": 10000}
```

A dictionary associates keys with values. `response["plan"]` retrieves a value
by key; `response.get("plan")` can return `None` if that key is missing. A list,
such as `["free", "pro"]`, holds a sequence of items.

`**request.model_dump()` copies the validated model's fields into another
dictionary. `model_dump()` gives ordinary Python data; `json.dumps()` converts
Python data into JSON text. `json.loads()` converts JSON text back into Python
data. Encoding that text as bytes is another operation, used for signatures.

### Classes, decorators, and dependencies

A class defines a kind of object. `Generation` describes accepted request data;
`StripeGateway` groups the methods that call Stripe.

A line beginning with `@` is a decorator. It adds behavior to the definition below
it. `@api.get("/usage")` registers an HTTP route. `@pytest.fixture` marks reusable
test setup. `@contextmanager` makes the database helper usable with `with`.

`Depends(authenticated_tenant)` tells FastAPI to run the authentication dependency
and supply its result to a route. This is how route functions receive a tenant ID
without trusting an ID sent in the request body.

### Resource cleanup and errors

```python
with connect(path, write=True) as db:
    ...
```

`with` asks a context manager to prepare a resource and clean it up afterward.
Here, it opens a connection and starts a transaction, then commits or rolls back
and closes the connection. A `return` inside the block still runs that cleanup.

`try` starts a block that might fail; `except` handles selected failures;
`finally` runs cleanup whether the operation succeeded or failed. Plain `raise`
inside an exception handler passes the same error upward.

### Async functions and threads

`async def` defines a function that can cooperate with other asynchronous work.
`await` waits for an asynchronous operation without simply blocking the event
loop for that wait. Synchronous routes use FastAPI's normal thread-pool support.
The webhook route explicitly sends its synchronous database enqueue operation to
a thread pool. The long-running Stripe job worker uses its own Python thread.

These mechanisms help the HTTP server keep responding. They do not automatically
prevent duplicate writes; the database transaction and constraints do that.

### Imports and entry points

`from app.db import connect` makes another file's helper available in this file.
`python -m app` runs the package's `__main__.py`. A guard such as
`if __name__ == "__main__":` runs its block when that module is launched as the
entry point, rather than every time another module imports it.

## 4. The complete project map

The maintained files are:

```text
.
|-- app/
|   |-- __init__.py
|   |-- __main__.py
|   |-- config.py
|   |-- db.py
|   |-- models.py
|   |-- seed.py
|   |-- metering.py
|   |-- main.py
|   |-- payments.py
|   `-- jobs.py
|-- migrations/
|   |-- 001_initial.sql
|   `-- 002_payments.sql
|-- scripts/
|   |-- probe.py
|   `-- replay_event.py
|-- tests/
|   |-- __init__.py
|   |-- conftest.py
|   |-- fakes.py
|   |-- test_metering.py
|   |-- test_jobs.py
|   `-- test_http_payments.py
|-- .github/workflows/tests.yml
|-- .env.example
|-- .gitignore
|-- requirements.txt
|-- capstone.yaml
|-- README.md
|-- DESIGN.md
|-- REQUIREMENTS.md
|-- EVIDENCE.md
|-- BUILDLOG.md
|-- TUTORIAL.md
`-- CODEBASE_GUIDE.md
```

You may also see local working files: `.env`, `data/billing.sqlite3`, temporary
files under `tmp/`, downloaded tools, `.venv/`, Python caches, and `.git/`.
Their purpose is explained in Section 9. They are not additional application
modules you need to read line by line.

The application uses three responsibilities:

| Responsibility | Main files | The question they answer |
|---|---|---|
| HTTP boundary | `app/main.py`, `app/models.py` | Is this request valid, who sent it, and what response should be returned? |
| Business rules | `app/metering.py`, `app/payments.py`, `app/jobs.py` | Is the action allowed, what does it cost, and how should payment state change? |
| Data storage | `app/db.py`, `migrations/` | How do we store and retrieve the records safely? |

Keeping these responsibilities recognizable makes it easier to test a rule
without starting a browser or calling Stripe.

## 5. Following a request through the system

### Startup

When you run `python -m app`:

1. `app/__main__.py` starts Uvicorn, the HTTP server.
2. Uvicorn imports the application object from `app/main.py`.
3. `create_app()` loads settings, constructs the Stripe adapter, and registers
   routes. Its startup lifecycle applies missing migrations and starts a worker.
4. The server listens on `127.0.0.1:8000`. The worker checks for ready jobs.

Seeding is a separate command. Starting the server creates the schema if needed;
`python -m app.seed` creates the two demonstration tenants and their keys.

### An accepted generation

```text
Client sends POST /generate with headers and token counts
    -> FastAPI validates the request and authenticates its tenant
    -> metering.record() opens a write transaction
    -> previous result for this tenant/key? replay it if the body matches
    -> otherwise check payment status and both monthly quotas
    -> calculate cost and insert one event with its original response
    -> commit transaction
    -> return JSON to the client
```

For the example request with 1,000 input, 400 cached, 500 output, and 200
reasoning tokens, the response contains one call, 1,500 tokens, and a charge of
6,600 micro-USD. A retry with the same tenant/key and normalized body returns
that same event ID and saved response.

### A usage lookup

`GET /usage` authenticates the caller, reads its current plan, and adds its usage
events for the current UTC month. It reads data; it does not create a generation,
charge another API call, or contact an AI model.

### A subscription upgrade

```text
POST /billing/checkout
    -> persist a Checkout attempt and customer mapping
    -> ask Stripe for a hosted test Checkout session
    -> browser opens Stripe's payment page
    -> user completes test Checkout
    -> Stripe sends signed checkout.session.completed through the CLI listener
    -> HTTP handler verifies signature and saves a durable job
    -> background worker retrieves the current subscription from Stripe
    -> verify its customer, price, quantity, and test mode
    -> update the tenant's plan/status and mark job done in one transaction
    -> GET /usage now shows Pro limits
```

The browser return page and webhook are separate paths. The return page displays
a message; it does not update the subscription table.

## 6. Every application file

### `app/__init__.py` - package marker

This file is empty. It marks `app` as a regular Python package, letting other
files use imports such as `from app.db import connect`. It contains no billing
rules and does not start the server.

### `app/__main__.py` - the command that starts the server

This is the entry point for `python -m app`. Its call to `uvicorn.run()` says:

- Load `app` from the module `app.main`.
- Listen only on the local machine (`127.0.0.1`), port `8000`.
- Disable request access logs, which can contain URLs and accidentally supplied
  secrets in query strings. Other application/error logging still exists.

Uvicorn handles network communication. The actual route definitions are in
`main.py`. This file does not seed data or define the pricing rules.

### `app/config.py` - settings and pinned pricing

This file separates settings and named prices from the logic that uses them.

`ROOT` locates the repository directory using this file's location. That lets
the code find `.env` and migrations without hardcoding Sebastian's computer path.

The pricing constants are:

| Constant | Value | Meaning |
|---|---:|---|
| `PRICING_VERSION` | `illustrative-usd-v1` | A label saved with an event's charge. |
| `API_CALL_MICRO_USD` | 1,000 | Cost of one accepted API action. |
| `INPUT_TOKEN_MICRO_USD` | 2 | Cost of one fresh input token. |
| `CACHED_TOKEN_MICRO_USD` | 1 | Cost of one cached input token. |
| `OUTPUT_TOKEN_MICRO_USD` | 8 | Cost of one output token, including reasoning. |
| `PRO_MONTHLY_CENTS` | 1,900 | The separate Stripe subscription price: USD 19.00. |

One USD contains 100 cents or 1,000,000 micro-USD. Those are different units:
the Stripe price uses cents; usage accounting uses micro-USD for finer precision.
These AI usage prices are illustrative constants, not a claim about a provider's
current prices.

`Settings` is a dataclass: Python creates a convenient object for the listed
fields. `frozen=True` stops normal reassignment of its fields after construction.
It holds the DB path, Stripe credentials/price, and base URL.

`__post_init__()` validates those settings: a configured Stripe key must have
the test-key prefix; a signing secret and price ID must have the expected
prefixes; the base URL must start with HTTP or HTTPS. A prefix check is only an
early check. The real Stripe API call determines whether a key works or a price
actually exists.

`from_env()` reads `.env` and process environment variables, supplies defaults,
and returns a `Settings` object. Existing process environment values take
precedence over `.env` with this loader's default behavior. The base URL loses
a trailing slash to make callback URLs consistent. Restart the application
after changing these settings because its existing settings object is not
automatically refreshed.

### `app/db.py` - safe database access

This file contains four helpers.

**`connect(path, write=False)`** opens a SQLite connection and manages a
transaction around the caller's block:

1. Open the database, allowing up to 30 seconds to wait for locks.
2. Return rows that support field names, such as `row["tenant_id"]`.
3. Enable foreign-key checks.
4. Start `BEGIN` for a read transaction or `BEGIN IMMEDIATE` for a write.
5. Use `yield` to let the caller execute its SQL.
6. Commit on success; roll back on an exception; close in either case.

`BEGIN IMMEDIATE` reserves SQLite's writer access before reading the values used
for a write decision. Another writer waits rather than making its decision from
the same old allowance. Readers can still operate under SQLite's WAL mode.

**`migrate(path)`** creates the parent data directory, enables WAL mode, creates
the `schema_migrations` bookkeeping table, and applies SQL files in filename
order if they have not already been recorded. Each migration and its bookkeeping
entry are committed together. Re-running startup does not apply the same recorded
migration again. WAL is SQLite's write-ahead log: part of its storage/concurrency
mechanism, not our Stripe job queue.

**`key_hash(key)`** returns a SHA-256 digest of an API key. A digest is a
repeatable fingerprint, not reversible encryption. The original random key is
kept locally; the tenant table stores its digest. SHA-256 here is used with
high-entropy random API tokens, not human passwords.

**`tenant_for_key(path, key)`** hashes the provided key and searches for a matching
tenant. It returns the tenant ID or `None`. The HTTP layer turns no match into
an authentication error.

SQL parameters use `?` placeholders and separate values. For example:

```python
db.execute("SELECT id FROM tenants WHERE api_key_hash = ?", (key_hash(key),))
```

This tells SQLite to treat the supplied value as data. The migration-name
interpolation is different: migration filenames are repository-controlled,
not values supplied in an HTTP request.

### `app/models.py` - accepted inputs and understandable errors

`TokenCount` describes an integer field with these constraints: strict integer,
at least zero, and at most 1,000,000,000. Strictness matters because Python
normally treats `True` as an integer-like value; an API token count of `true`
should be rejected rather than quietly interpreted as one.

`Generation` is the request model. Its four counts default to zero, so `{}`
means an API-only simulated action. `extra="forbid"` rejects unexpected fields,
including a caller trying to choose another tenant in the body.

`valid_subsets()` checks relationships between otherwise valid fields:

```text
cached_input_tokens <= input_tokens
reasoning_tokens <= output_tokens
```

Input/output counts are totals with subsets inside them. Negative values or
contradictory subsets produce a validation response, not a recorded event.

`DomainError` carries a deliberate business failure: HTTP status, machine-readable
error code, human-readable message, optional headers, and details. For example,
a quota error can include the meter and reset time. It lets the business logic
explain a problem without constructing FastAPI responses itself.

### `app/seed.py` - create demonstration tenants safely

Seeding supplies initial demonstration records after creating the schema.
It is separate from normal generation and Checkout requests.

`seed(settings, keys)` checks that keys are distinct and at least 32 characters,
runs migrations, and creates each tenant with a Free subscription. Existing
records are preserved. If an existing tenant has a different key digest, seeding
fails rather than silently replacing that tenant's access credentials.

`main()` loads settings, reads `DEMO_TENANT_A_KEY` and `DEMO_TENANT_B_KEY`,
generates missing keys using `secrets.token_urlsafe(32)`, saves generated keys
to `.env`, and calls `seed()`. `secrets` is Python's randomness module intended
for things such as credentials.

Run it with `python -m app.seed`. Repeating it is not a way to reset quotas or
undo Stripe state. Losing or replacing `.env` can leave you without the keys
matching existing database records, which is why the seed refuses silent rotation.

### `app/metering.py` - the core accounting rules

This file is the main place to study for retries, quota boundaries, and money.

| Name | What it does |
|---|---|
| `utc_now()` | Supplies the current UTC time; tests can replace it to simulate month changes. |
| `canonical(value)` | Produces consistent sorted JSON text for a request fingerprint. |
| `usd_string(micro_usd)` | Displays integer micro-USD as an exact six-decimal USD string. |
| `cost_of(request)` | Calculates one call's API fee plus its token cost using integers. |
| `plan_for(db, tenant_id)` | Joins the tenant's subscription with its plan's limits. |
| `rollup(db, tenant_id, period)` | Adds accepted events for one tenant and month. |
| `record(path, tenant_id, request, key)` | Atomically checks/replays/records one action. |
| `usage(path, tenant_id)` | Returns the current month, usage, plan, limits, and stored cost total. |

`PAYMENT_BLOCKED` names statuses that prevent a new action with 402. The worker
normally converts a canceled or incomplete-expired subscription to Free; active
or trialing Pro is allowed. The status and plan are separate fields: a Pro row
can still have a payment problem.

`rollup()` uses `COUNT` and `SUM` in SQL, scoped by tenant and `YYYY-MM` period.
`COALESCE(..., 0)` turns an empty sum into zero so a new tenant receives useful
numeric totals instead of `null` values.

Read `record()` in this order:

1. Convert the validated request to consistent JSON and hash it.
2. Open a write transaction before reading prior usage.
3. Look up an existing event using both tenant and idempotency key.
4. If found, return the stored response when the body matches; otherwise raise
   a 409 conflict. No new event is inserted in either case.
5. For a new action, determine this UTC month and retrieve the plan/status.
6. Reject a payment problem with 402.
7. Add the current month's accepted events.
8. Check the API-call and token quotas. Crossing either gets 429.
9. Calculate the cost, generate an event ID, and build a response dictionary.
10. Insert one event including the request hash, charge, pricing version, and
    complete response JSON. The context manager commits before the result leaves.

The exact boundary is allowed: `used + requested == limit` passes. `Retry-After`
uses the number of seconds until the next UTC month, rounded upward. A blocked
action creates no usage event and reserves no idempotency key.

`usage()` reads existing events and adds their saved charges. It does not
recalculate past events using today's prices. It also reports the current
pricing constants separately. If prices are changed later, that pricing display
describes the current configuration, while the cost total still sums the
historical event charges.

### `app/main.py` - HTTP routes, authentication, and lifecycle

This is the doorway into the application. Its route functions are deliberately
small because metering and payment decisions live in other modules.

**`create_app(settings=None, gateway=None, start_worker=True)`** builds the
FastAPI application. In normal operation it reads real settings and constructs
`StripeGateway`. Tests can supply a fake gateway and disable the automatic worker
so they control exactly when a job runs. That substitution is dependency
injection: giving a component its collaborators instead of hiding them inside it.

**`lifespan()`** defines startup and shutdown behavior. Startup migrates the DB
and starts the worker thread when enabled. Shutdown sets a stop signal and waits
up to 25 seconds for the thread. The persisted queue is what allows interrupted
work to be recovered; a Python thread alone would not preserve jobs after exit.

**`authenticated_tenant()`** reads `X-API-Key`, asks `tenant_for_key()` for its
tenant, and raises 401 if none matches. The `Tenant` annotation connects that
dependency to protected routes. A client is never trusted to select a tenant by
sending a `tenant_id` field.

The route functions are:

| Route function | Route | Actual behavior |
|---|---|---|
| `health()` | `GET /health` | Reads DB job failures; returns 503 if exhausted jobs exist, otherwise 200. It is not a comprehensive production health monitor. |
| `generate()` | `POST /generate` | Accepts a validated `Generation`, tenant, and action key; calls `record()`. |
| `get_usage()` | `GET /usage` | Calls `usage()` for the authenticated tenant. |
| `start_checkout()` | `POST /billing/checkout` | Calls the payment workflow and returns a hosted session ID/URL. |
| `billing_status()` | `GET /billing/status` | Returns the tenant's plan/status and latest 20 stored jobs. |
| `stripe_webhook()` | `POST /webhooks/stripe` | Reads raw bytes, limits payload size, verifies signature, and enqueues a valid event. |
| `checkout_success()` | `GET /billing/success` | Returns a short HTML message. It performs no subscription write. |
| `checkout_cancel()` | `GET /billing/cancel` | Returns a short HTML message. It does not cancel a subscription at Stripe. |

The last distinction matters: canceling the Checkout browser flow is different
from canceling an already-created subscription.

There are also four exception handlers:

- `domain_error()` converts deliberate business errors to their chosen status
  and JSON body, preserving headers such as `Retry-After`.
- `validation_error()` returns 422 with useful field locations/messages while
  avoiding echoing supplied header values that could contain keys.
- `stripe_error()` returns a generic 503 for SDK errors and logs the error class,
  not a potentially sensitive provider message.
- `database_error()` returns a generic 503 for SQLite operational failures.

The webhook route rejects bodies above 256 KiB. It gives the original request
bytes to `stripe.Webhook.construct_event()` along with the `Stripe-Signature`
header and local signing secret. Invalid JSON/signatures receive 400. Valid
events are converted to ordinary dictionaries and handed to `enqueue_event()`
through a thread pool.

Finally, `app = create_app()` creates the application object Uvicorn imports.

### `app/payments.py` - Checkout and the verified event inbox

`StripeGateway` is the application's adapter to the Stripe SDK. Its methods are:

| Method | Purpose |
|---|---|
| `__init__()` | Build a test-key SDK client, a 10-second HTTP timeout, and one configured network retry. No key means no SDK client. |
| `create_customer()` | Create/reuse a Stripe customer under a stable provider idempotency key and return its ID. |
| `validate_price()` | Retrieve the configured price and check active/test/USD/1,900 cents/monthly/one-month interval/licensed rules. |
| `create_checkout()` | Request a subscription Checkout for that customer and price, with tenant references and a stable attempt key. |
| `retrieve_checkout()` | Read a session's current state from Stripe. |
| `retrieve_subscription()` | Read a subscription's current state from Stripe. |

`licensed` here describes a fixed subscription quantity, rather than Stripe's
metered subscription billing. The project's own usage calculations are not
submitted as Stripe metered charges.

The SDK returns resource objects. `.to_dict()` converts them into ordinary
Python dictionaries for the business logic. The current Checkout call uses
`allowed_payment_method_types` to filter to cards; an older field name caused a
real API error during testing and was corrected.

`require_stripe()` returns an understandable 503 if the test key or price ID is
missing. Metering itself can still operate without configured payments.

`checkout()` coordinates an upgrade request:

1. Check payment configuration and the tenant's subscription. Existing Pro or
   delinquent subscriptions receive 409 instead of another purchase flow.
2. Persist a Checkout attempt with a random stable `attempt_id` before contacting
   Stripe. Repeated calls reuse the same current attempt for the tenant.
3. If a session is already stored, retrieve it. Reuse an open session; report a
   completed session awaiting sync; clear an expired attempt and ask for a retry.
4. An uncertain attempt with no saved session older than 23 hours needs operator
   reconciliation. It is not automatically replaced with another purchase.
5. Validate the price, create/reuse the Stripe customer if needed, and save the
   customer mapping before returning Checkout to the caller.
6. Ask Stripe to create Checkout under `metering-checkout-<attempt_id>`, reject
   a non-test response, save the session ID, and return its URL.

Stripe has its own idempotency mechanism; our stable key is how we ask Stripe to
recognize a retried session-creation request. That is distinct from the usage
event key and from the webhook event ID. Do not automatically change such a key
after a timeout: Stripe may have created the session even though its reply was lost.

`SUPPORTED_EVENTS` names the three event types handled by this capstone.
`enqueue_event()` accepts an event **after** HTTP signature verification:

- Require test mode and the needed event/customer/subscription ID shapes.
- Ignore event types outside the supported set.
- Find a local tenant by the stored Stripe customer ID, not by arbitrary metadata.
- Insert one job under the event's unique ID. Known tenants start at `pending`;
  unknown customers are stored as `ignored`.
- Return `queued`, `ignored`, or `duplicate` based on that insertion.

Unsupported types are ignored without an inbox row. Known supported events get
durable deduplication because `event_id` is the table's primary key. Enqueuing
does not itself change the tenant to Pro.

### `app/jobs.py` - durable background synchronization

This file does the potentially slow Stripe retrieval after the webhook handler
has acknowledged a durable job. Its main operations are:

**`claim(path, now)`** selects one eligible job in a write transaction. It does
not claim another while an unexpired processing lease exists. It prioritizes
expired processing work over later pending work, increments the attempt count,
and saves a new 120-second lease and random `lease_token`.

A lease is a time-limited claim on work. A fencing token identifies this specific
claim. If an old worker eventually finishes after a replacement worker has
reclaimed the job, the old token no longer authorizes a write.

**`run_once(settings, gateway, now=None)`** processes at most one claimed job:

1. Retrieve the current subscription from Stripe rather than trusting the event's
   possibly old snapshot.
2. Check its test mode, customer ID, subscription ID, exactly one item, configured
   price ID, quantity one, and recognized status.
3. Map canceled/incomplete-expired subscriptions to Free. Other recognized
   subscription statuses keep the Pro plan label; payment-blocked statuses still
   prevent generation through `metering.py`.
4. Recheck that this job's processing token still matches before applying a result.
5. Avoid letting a late deletion of an older subscription overwrite a different
   current paid subscription. A conflicting paid subscription is treated as a
   failure requiring attention.
6. Update local subscription state and mark the job done in the same transaction.
   On a Free transition, remove the old Checkout attempt so a later demo can start
   a new purchase flow.

If processing fails, it records only the error class, schedules a retry, and
logs a warning. After three claimed attempts, it records `failed` and emits a
CRITICAL failure alert. Delays after the first two failures are 2 and 4 seconds.
Crashes that leave an expired processing job also count toward the attempt limit.

`run_once()` returns whether it claimed work, not whether the subscription sync
succeeded. The stored job status is the way to distinguish success from failure.

**`worker_loop()`** repeatedly calls `run_once()` until its stop event is set.
It waits briefly between iterations and logs unexpected worker-level failures
without dumping exception messages. Jobs are stored in SQLite, so they survive
a thread/process restart; the thread merely executes them.

**`retry_failed(path, event_id)`** lets a local operator requeue a failed job
after correcting its cause. It resets attempts and lease fields only if the job
is currently failed. It refuses to reset completed work. The module's command-line
block exposes this as `python -m app.jobs evt_FAILED_EVENT_ID`.

The failure alert is a local log plus degraded `/health`, not an email or paging
integration. The code is intended for the documented single-process capstone
setup, not an unsupported claim of a fully distributed queue service.

## 7. Every database migration

A database table is like a structured worksheet: columns describe fields, rows
hold records. Unlike an ordinary worksheet, the database can enforce rules about
which records are allowed.

### SQL symbols you will see

| SQL term | Meaning |
|---|---|
| `CREATE TABLE` | Define a place to store a kind of record. |
| `INSERT` | Add a record. |
| `SELECT` | Read records. |
| `WHERE` | Limit an operation to matching records. |
| `UPDATE` | Change matching records. |
| `DELETE` | Remove matching records. |
| `PRIMARY KEY` | The field or fields that uniquely identify a row. |
| `UNIQUE` | Reject duplicate values or duplicate combinations of values. |
| `NOT NULL` | Require a value rather than a missing value. |
| `CHECK` | Enforce a condition, such as a nonnegative count. |
| `REFERENCES` | Link a field to another table's valid identifier: a foreign key. |
| `JOIN` | Combine related records, such as a subscription and its plan. |
| `INDEX` | An extra lookup structure that helps find relevant rows efficiently. |

An index improves lookup; a unique constraint additionally enforces correctness.
Application checks and database constraints are complementary. A future bug in
application code should not easily allow an invalid database record.

### `migrations/001_initial.sql` - core accounting tables

This migration creates four tables and inserts the Free/Pro plan definitions.

**`plans`** stores the shared definitions of the two plans:

| Column | Meaning |
|---|---|
| `id` | Either `free` or `pro`, enforced by a check. |
| `api_call_limit` | Monthly API allowance; must be positive. |
| `token_limit` | Monthly token allowance; must be positive. |

Plan limits are seeded in this SQL file, not in `seed.py` or the pricing constants.
Pricing tells us what a unit costs; quotas tell us how many units are allowed.

**`tenants`** stores the customer organizations:

| Column | Meaning |
|---|---|
| `id` | Local organization ID, such as `tenant_a`. |
| `name` | Display name. |
| `api_key_hash` | Unique digest used to recognize the tenant's API key. |
| `stripe_customer_id` | Optional unique link to its Stripe customer. |

**`subscriptions`** stores each tenant's current access/payment state:

| Column | Meaning |
|---|---|
| `tenant_id` | Both primary key and foreign key: at most one current subscription row per tenant. |
| `plan_id` | Foreign key pointing to the current Free/Pro definition. |
| `status` | Local subscription status, such as `free`, `active`, or `past_due`. |
| `stripe_subscription_id` | Optional unique link to a Stripe subscription. |
| `updated_at` | When the local record was last updated. |

**`usage_events`** is the ledger of accepted generation actions:

| Column | Meaning |
|---|---|
| `id` | Unique local event ID. |
| `tenant_id` | Which organization owns the event. |
| `idempotency_key` | Which action/retry identity was supplied. |
| `request_hash` | Fingerprint of the normalized validated body. |
| `occurred_at` | UTC timestamp for the action. |
| `period` | Accounting month, such as `2026-10`. |
| `api_calls` | Always one for this one-action-per-event design. |
| `input_tokens` | Inclusive input token count. |
| `cached_input_tokens` | Cached portion of that input count. |
| `output_tokens` | Inclusive output token count. |
| `reasoning_tokens` | Reasoning portion of that output count. |
| `cost_micro_usd` | The charge calculated when the event was created. |
| `pricing_version` | Which named pricing rules were used. |
| `response_json` | The original response, saved for exact retries. |

One event represents both usage quantities: API calls and tokens. There is not
one extra usage-event row for each token category.

The important constraint is:

```sql
UNIQUE (tenant_id, idempotency_key)
```

The same action key can appear once for A and once for B, but cannot appear twice
for A. `usage_tenant_period` speeds up looking up one tenant's events for a month.
Checks also reject negative quantities, inconsistent subsets, and negative costs.

### `migrations/002_payments.sql` - external work and retry state

This migration creates two more tables.

**`checkout_attempts`** remembers the current purchase attempt:

| Column | Meaning |
|---|---|
| `tenant_id` | Primary key: one current attempt row per tenant. |
| `attempt_id` | Stable unique ID used in Stripe's session-creation idempotency key. |
| `created_at` | Integer Unix timestamp, used to detect an old uncertain attempt. |
| `session_id` | Stripe session ID after the response is saved; may initially be missing. |

It is possible for Stripe to create a session while the network loses the reply.
That is why a row can exist before `session_id` is known and why the stable
`attempt_id` is important.

**`webhook_jobs`** is the durable inbox:

| Column | Meaning |
|---|---|
| `event_id` | Stripe event ID; primary key for duplicate prevention. |
| `tenant_id` | Local owner, or missing for an unknown customer. |
| `event_type` | Which supported event happened. |
| `customer_id` | Stripe customer reference used for ownership checks. |
| `subscription_id` | Subscription to retrieve from Stripe. |
| `status` | `pending`, `processing`, `done`, `ignored`, or `failed`. |
| `attempts` | How many processing claims have been made. |
| `available_at` | Earliest timestamp when a pending job can be tried. |
| `lease_until` | Expiration time of a processing claim. |
| `lease_token` | Identity of the current processing claim. |
| `last_error_type` | Sanitized exception class if a processing attempt failed. |

The queue indexes support ready-job lookup and tenant job lookup. No raw Stripe
request body, signature, secret key, or card number is stored in this table.

There is also a seventh bookkeeping table, **`schema_migrations`**, created by
`db.migrate()` rather than either SQL file. It stores the names of applied
migrations. In total, there are six domain/workflow tables plus that bookkeeping
table.

## 8. Every script and test file

### `scripts/probe.py` - act like a real API client

This script talks to an already-running HTTP server. It loads the local tenant
key without printing it and uses HTTPX to send requests.

| Command | What it does | Changes demo state? |
|---|---|---|
| `python scripts/probe.py core` | Reads usage, sends the same generation twice, checks identical responses and one new event costing 6,600 micro-USD. | Yes: one accepted action per script run. |
| `python scripts/probe.py usage` | Prints current tenant A usage. | No. |
| `python scripts/probe.py status` | Prints tenant A's plan/status and recent jobs. | No. |
| `python scripts/probe.py checkout` | Requests Checkout and opens its URL locally. | Yes: may create/reuse a test Checkout/customer. |

The `core` command creates a fresh random action key each time you run the
script. Its two requests are retries of each other; a later separate script run
represents a new action. Repeating the whole script does not mean the same
idempotency key is reused forever.

`raise_for_status()` stops on an HTTP error. Its `assert` statements check the
expected behavior, including the difference between before and after totals.
The Checkout branch opens the hosted URL instead of printing that bearer link
into public evidence. Run these diagnostic scripts with ordinary Python, without
disabling assertions through `-O`.

### `scripts/replay_event.py` - replay a real test event safely

This is a diagnostic client for duplicate webhook handling:

1. Accept a correctly shaped `evt_...` ID on the command line.
2. Load the three relevant keys locally: Stripe API, webhook signing, tenant A.
3. Retrieve the real test event from Stripe through the official SDK.
4. Confirm it is a test event and appears as a completed job in tenant A's recent
   job list. The helper is limited to those latest 20 jobs.
5. Encode the retrieved event and calculate two fresh local HMAC signatures.
6. Send it twice to the webhook endpoint, expecting `200 duplicate` each time.
7. Confirm the stored job state is unchanged and print only sanitized job data.

HMAC is a keyed cryptographic calculation: it uses a shared secret plus the
message bytes. This helper can make a valid local test signature because it has
the listener secret from `.env`. That does not let an arbitrary internet caller
forge Stripe signatures without that secret.

The initial real webhook comes through Stripe CLI. The two replay POSTs are
sent by this script, not by the CLI. The helper does not itself upgrade a tenant
or mark a job done: it requires the original processing to have happened first.

### How the automated tests work

Tests are small programs that set up a situation and check an expected result.
An `assert` fails the test if its condition is false. `pytest.raises(...)` checks
that an operation raises an expected error rather than succeeding silently.

A **fixture** is reusable setup that pytest supplies by parameter name. A test
with a `settings` parameter receives the fixture called `settings`. This is not
the ordinary production route dependency system, although both supply collaborators.

`@pytest.mark.parametrize` runs a test repeatedly with different inputs. That is
why the suite's 59 collected cases at this snapshot exceed the number of named
test functions. Tests use temporary databases and fake Stripe collaborators;
the real-account evidence is recorded separately in `EVIDENCE.md`.

### `tests/__init__.py` - test package marker

This empty file makes `tests` a regular package so shared helpers can be imported
using paths such as `tests.fakes`. It has no assertions or server startup code.

### `tests/conftest.py` - common isolated setup

Pytest automatically discovers this special filename. Its `settings(tmp_path)`
fixture creates a database path in a temporary directory, supplies fake Stripe
settings, seeds two tenants, and returns the settings object.

`KEY_A` and `KEY_B` are deliberately fake test API keys. The test suite never
needs your real Stripe secret to contact Stripe. These constants are unrelated
to the real generated demo keys in your `.env`.

### `tests/fakes.py` - predictable Stripe stand-ins

`FakeStripe` provides the same gateway methods the workflows need, backed by
Python dictionaries rather than real network calls. It counts created customers
and remembers Checkout attempt IDs so tests can check reuse.

Its `validate_price()` intentionally does nothing: this simple fake is not
testing real Stripe price retrieval. Other tests exercise that validation and
the official SDK transport contract separately.

`subscription()` builds a fake subscription object. `event()` builds a fake
event with the right customer/subscription references for either Checkout or
subscription events. `deepcopy()` returns an independent subscription copy so
retrieving it does not accidentally hand the business logic the fake's original
mutable object.

These are test doubles, not a hidden production bypass for granting Pro.

### `tests/test_metering.py` - accounting and database guarantees

This file calls the business logic directly. It does not need a network server.

| Test | The behavior it checks |
|---|---|
| `test_exact_pricing_and_monthly_rollup` | The worked event is 1,500 tokens and 6,600 micro-USD, and the summary agrees. |
| `test_simultaneous_retries_create_one_event_and_original_response` | 24 calls through 12 threads with one key create one event; a different body gets 409. |
| `test_exact_api_call_boundary_then_429` | All 1,000 allowed calls work; a new one fails; replaying an accepted key still works. |
| `test_token_boundary_and_concurrent_last_slot` | Ten callers compete for one remaining token; exactly one is allowed. |
| `test_402_and_retry_still_returns_original` | A new action is blocked by payment status, while an old successful key replays. |
| `test_utc_month_rollover_retains_old_idempotency` | New-month totals reset by filtering, while an old key still returns its old result. |
| `test_tenant_isolation_persistence_migrations_and_safe_reseed` | Data survives reopening/reseeding, tenants are separate, hashes are stored, and unsafe key replacement fails. |
| `test_token_validation` | Negative/fractional/boolean/string/oversized counts, invalid subsets, and extra tenant fields are rejected. |
| `test_live_key_rejected_without_echoing_it` | A live-prefixed key is rejected without being included in the error text. |
| `test_pricing_subset_properties_and_immutable_historical_cost` | More reasoning inside fixed output adds no charge, more cached input saves money, and changing today's constant does not reprice an old event. |
| `test_retry_after_exact_last_second_and_year_rollover` | The last second of December gives exactly one second of Retry-After and the correct next-year reset. |

`ThreadPoolExecutor` runs competing calls in threads. `monkeypatch` temporarily
replaces the clock or a pricing constant for a test; pytest restores it afterward.
These are ways to exercise difficult cases without waiting a month or editing
production settings.

### `tests/test_jobs.py` - background reliability and subscription truth

The file's `gateway` fixture sets up a fake owned customer/subscription. Tests
then enqueue events and control when individual processing attempts happen.

| Test | The behavior it checks |
|---|---|
| `test_background_updates_payment_and_cancellation_from_current_truth` | Current active, past-due, unpaid, and canceled subscriptions map to the expected local state. |
| `test_out_of_order_snapshot_cannot_resurrect_canceled_subscription` | An old active event snapshot cannot override the current canceled Stripe record. |
| `test_durable_job_retries_then_failure_alert_without_secrets` | Retries respect backoff; the third failure is durable and critical; sensitive exception text is absent from logs. |
| `test_durable_job_recovers_after_restart_and_fences_stale_worker` | An expired claim can be recovered from the same stored DB, using a new token. |
| `test_stale_worker_cannot_commit_after_lease_is_reclaimed` | Reclaiming during an old worker's fetch prevents that old worker from applying its result. |
| `test_worker_rejects_wrong_customer_mode_or_plan` | Wrong ownership, live mode, price, or quantity does not grant access. |
| `test_unknown_customer_is_ignored_and_deduplicated` | An unrelated customer is ignored, and retry delivery stays deduplicated. |
| `test_late_old_subscription_deletion_cannot_undo_new_active_one` | A delayed deletion for a previous subscription cannot downgrade a newer active one. |
| `test_three_crashed_attempts_alert_and_operator_retry` | Repeated abandoned claims eventually fail; a deliberate retry works; completed jobs cannot be reset. |

`caplog` captures logs for assertions. Supplying `now=` to job functions makes
retry-time checks deterministic: the test advances a number instead of sleeping
through all the backoff delays.

### `tests/test_http_payments.py` - what callers see

This file uses FastAPI's `TestClient`: requests pass through the application's
routes and validation without requiring a separate TCP server. Most tests
disable automatic background processing and call `run_once()` explicitly.

`signed()` constructs a locally signed test payload. The `gateway` fixture
returns `FakeStripe`; the `client` fixture opens/closes the test application's
lifecycle. The main assertions are:

| Test | The behavior it checks |
|---|---|
| `test_http_idempotency_response_identity_and_isolation` | HTTP retry response bytes match, tenant B sees none of A's usage, and conflicting reuse gets 409. |
| `test_http_quota_and_payment_status_codes` | Callers receive explained 429/402 responses and Retry-After. |
| `test_bad_input_is_4xx_not_500` | Invalid request bodies produce validation errors. |
| `test_auth_headers_and_validation_do_not_echo_credentials` | Missing/bad authentication and malformed action keys are rejected without echoing sensitive input. |
| `test_checkout_and_signed_webhook_upgrade_then_replay` | Checkout alone does not upgrade; one valid job does; replay stays a duplicate and jobs remain tenant-scoped. |
| `test_forged_stale_or_changed_signature_changes_nothing` | Bad, old, or altered signatures return 400 with no queued work. |
| `test_signed_but_invalid_webhook_returns_400` | Signature validity does not excuse a malformed event or live-mode payload. |
| `test_disabled_stripe_is_explicit_503` | Missing payment configuration produces a useful unavailable response. |
| `test_sdk_failure_is_sanitized` | A provider failure does not leak its sensitive message into HTTP output/logs. |
| `test_checkout_unknown_result_reuses_durable_attempt_after_network_failure` | Losing a session-creation response still reuses the provider attempt identity. |
| `test_official_sdk_encodes_checkout_and_customer_contract` | The real SDK encodes expected fields, current card filter, and idempotency headers through a recording fake HTTP transport. |
| `test_worker_runs_off_http_path_and_health_exposes_exhausted_jobs` | The handler responds while a separate worker fetch is blocked; failed jobs degrade health. |
| `test_checkout_rejects_misconfigured_stripe_price` | Wrong mode, active flag, amount, currency, interval, or billing type is rejected. |
| `test_checkout_expiry_and_ambiguous_old_attempt` | Expired sessions can be replaced safely, while an uncertain old attempt requires reconciliation. |

The recording HTTP transport in the SDK test is still fake external data; it
does not prove Stripe will accept every future request. Real-account testing
caught an outdated Checkout parameter, which is why both forms of evidence matter.

Some compact test expressions use `lambda`, a short unnamed function, or a
generator's `.throw()` to deliberately raise an error. Their purpose is to
simulate a failing collaborator. They are not the application's error-handling
implementation.

## 9. Every configuration and documentation file

### `.env.example` - a safe settings template

This file lists configuration names and safe defaults without real credentials.

| Name | Used for |
|---|---|
| `DATABASE_PATH` | Where SQLite stores local records. Relative paths resolve under the project root. |
| `BASE_URL` | Callback URLs and the diagnostic scripts' target address. The standard server entry point still binds to localhost port 8000; this setting does not change its listening port. |
| `DEMO_TENANT_A_KEY` | Local client credential for demo tenant A, generated by seed if absent. |
| `DEMO_TENANT_B_KEY` | Separate client credential for demo tenant B. |
| `STRIPE_SECRET_KEY` | Outgoing authentication from the backend to Stripe; only test keys are permitted. |
| `STRIPE_WEBHOOK_SECRET` | Incoming signature verification for this local listener. |
| `STRIPE_PRO_PRICE_ID` | Identifies the recurring test price; an identifier, not an amount or secret API key. |

Copy the template only when creating a new local setup. Do not overwrite an
existing `.env` containing working keys. Never paste actual keys into this guide
or use a real card to demonstrate test Checkout.

### `.gitignore` - keep private/generated local files out of new commits

It excludes `.env` and its variants, virtual environments, caches, local DB files,
temporary tools, and the source capstone PDF. `!.env.example` is an exception:
the safe template is included in Git.

Ignoring a path is not encryption and does not remove something already committed
to Git history. It also does not prevent somebody from deliberately forcing an
ignored file into a commit. This is why secret checks matter in addition to the
ignore file.

### `requirements.txt` - the installed Python packages and versions

`python -m pip install -r requirements.txt` reads this list. `==` pins the recorded
version rather than asking for whichever newest release happens to exist later.

The main packages to recognize are:

| Package | Role |
|---|---|
| `fastapi` | HTTP application and route handling. |
| `uvicorn` | Server that runs the FastAPI application. |
| `pydantic` | Validates request data and field relationships. |
| `stripe` | Official SDK for Stripe API calls and webhook verification. |
| `python-dotenv` | Loads and updates local `.env` values. |
| `httpx` | HTTP client used by probes and the current TestClient compatibility path. |
| `pytest` | Discovers and runs automated tests. |

Other pinned lines are support dependencies. `starlette` supplies underlying web
machinery; `pydantic_core`, typing and annotation helpers support validation and
type information; `requests` is used by the chosen Stripe HTTP client. HTTP,
certificate, encoding, and networking helpers include `httpcore`, `h11`, `anyio`,
`urllib3`, `certifi`, `charset-normalizer`, and `idna`. Test/terminal helpers
include `pluggy`, `iniconfig`, `packaging`, `Pygments`, `colorama`, and `click`.
`opentelemetry-api` is an installed support dependency; this project does not
configure a telemetry exporter.

These libraries are installed into `.venv/`; they are not project source files
you authored. SQLite comes with the Python standard library, so there is no
separate SQLite package in this list.

### `.github/workflows/tests.yml` - automatic GitHub checks

This YAML file defines a GitHub Actions workflow, triggered by pushes and pull
requests. A matrix means running the same steps once on Ubuntu and once on
Windows, using Python 3.12:

1. Check out the repository files.
2. Install Python and use pip caching.
3. Install pinned dependencies.
4. Run `python -m pytest -q`.

The workflow has read-only repository-content permission. It neither deploys
the API nor receives our real local Stripe keys. Passing CI means those tests
passed for that commit; it does not mean a permanent public API is hosted.

### `capstone.yaml` - evaluator instructions

This is a small manifest describing installation, run, seed and test commands,
the local base URL, and the six main probe endpoints. It names authentication
and signature environment variables without storing their actual values.

An evaluator can read it to understand how to exercise the service. The Python
application does not read this manifest to define its routes: `main.py` does
that. If routes or commands change, this manifest must remain consistent with
the code and README.

### `README.md` - setup, operation, and honest scope

This is the repository's front page. It explains the product, exact setup/run
commands, architecture diagram, API contract, quotas, pricing, Stripe setup,
background failure recovery, and limitations. Use it when running or demonstrating
the project. A new reader should not need the chat history to get started.

### `DESIGN.md` - the design written before implementation

This records the problem, data model, API decisions, layer boundaries, pricing
convention, retry strategy, background work, and explicit non-goal. It is the
compact design document for the first build phase, preserved in the Git history.
It explains what we intended; tests and evidence are needed to prove the actual
implementation behaves that way.

### `REQUIREMENTS.md` - map the brief to the expected proof

This file lists the requirements and the type of evidence needed for each one.
It is a coverage map: it prevents a correct-looking implementation from forgetting
a background job, manifest, tenant isolation, or real Checkout demonstration.
The current proof and completion details are in `EVIDENCE.md`.

### `EVIDENCE.md` - recorded verification results

This connects requirement checkboxes to actual test results and sanitized HTTP/
Stripe CLI transcripts. It includes real Checkout, replay, forged-signature,
update, and cancellation proof. It separates automated fake-data tests from
real Stripe test-account checks, and leaves human-owned steps unchecked until
they actually happen.

It is historical evidence for recorded revisions/runs. Reading a passing result
does not test newly modified code; after a behavior change, collect fresh evidence
appropriate to that change.

### `BUILDLOG.md` - honest authorship and development history

This records where AI helped, mistakes it made, changes made after real tests,
and Sebastian's actual participation. For example, it records the SDK object
conversion mistake and the outdated Checkout parameter uncovered during setup.

Do not write that you independently authored or reviewed something unless that
actually happened. After studying, add your own truthful notes about what you
understand, tried, or changed. Creating this guide is not evidence that you have
already understood its contents.

### `TUTORIAL.md` - a focused engineering walkthrough

The existing tutorial explains the major correctness decisions, their tradeoffs,
and exercises for defending the implementation. It assumes more comfort with
backend vocabulary than this beginner guide. Read it after you can follow the
basic file and request maps here.

### `CODEBASE_GUIDE.md` - this beginner reference

This file provides the vocabulary, full file inventory, function explanations,
worked examples, and practice answers for the three submission questions. It
documents the current source, rather than creating additional application behavior.

### Local working files you may also see

| File or directory | Purpose |
|---|---|
| `.env` | Actual local settings and credentials. Private and ignored by Git. |
| `data/billing.sqlite3` | Persistent local SQLite database holding demo usage, subscription and job state. |
| SQLite `-wal` and `-shm` companion files | Part of SQLite's active WAL mechanism; do not treat them as disposable standalone app source. |
| `.venv/` | Isolated Python executable and installed third-party packages for this project. |
| `__pycache__/`, `*.pyc` | Python's generated bytecode caches. |
| `.pytest_cache/` | Pytest's generated run information. |
| `tmp/` | Working artifacts such as rendered brief pages, test transcripts, a fresh verification clone, and the downloaded Stripe CLI. |
| `Usage Metering Billing Engine Live Capstone.pdf` | The local source brief, excluded from the public code repository. |
| `.git/` | Git's internal version-history and repository bookkeeping, not application logic. |

The GitHub repository contains source and instructions. It does not contain your
local database, actual keys, or the installed virtual environment. Backups of a
real local setup need the database and matching configuration; casually deleting
either is not a quota-reset or Stripe-reconciliation strategy.

## 10. The three concepts to explain yourself

### 10.1 Retries and concurrent requests must not create duplicate usage

**The problem:** a client sends a generation request. The backend saves it, but
the connection drops before the response reaches the client. The client cannot
tell whether the operation happened, so it tries again. Treating the retry as a
new action would add a second event and a second calculated charge.

The client therefore gives the action an identity:

```text
X-API-Key: the tenant's private credential
Idempotency-Key: demo-action-001
```

The action key is an example, not an authentication secret. A caller should
create a new action key for new work and reuse the existing one for a retry.

#### A. Remember both the action and its result

In `metering.record()`, this query runs inside the write transaction:

```sql
SELECT request_hash, response_json FROM usage_events
WHERE tenant_id = ? AND idempotency_key = ?
```

The database looks for that specific tenant's action. If it exists, these lines
handle the result:

```python
if prior:
    if prior["request_hash"] != request_hash:
        raise DomainError(409, "idempotency_conflict", "This key belongs to a different request")
    return json.loads(prior["response_json"])
```

Read them literally:

1. `if prior` means "a previous record was found."
2. Compare the old and new request fingerprints. A reused label must not quietly
   mean a different action.
3. If they differ, return a 409 conflict through the error handler.
4. Otherwise turn the saved JSON text back into data and return the old response.
   The code never reaches the new-event insertion for that retry.

The fingerprint uses the validated fields, including default zeros, serialized
with consistent sorting. Changing JSON field order alone does not change the
meaning of the action. An omitted zero and an explicitly supplied zero also
normalize to the same field value.

#### B. Check for a prior result before checking today's allowances

Suppose the first request took a tenant from 999 to 1,000 calls. A retry arrives
afterward. If the backend checked the quota first, it could reject the retry as
over quota, even though that action already succeeded.

Our retry lookup happens first. The backend replays the successful result. The
same principle applies if payment becomes past due or a new month starts after
the original action: the retry describes historical work, not new usage.

This is why the stored response includes the original event ID, month, cost,
and quantities. Rebuilding a new response using the current month would weaken
that history-preserving contract.

#### C. Protect the whole decision when requests arrive together

A duplicate lookup alone is insufficient. Consider two callers without a
transaction:

```text
Caller A: no prior row found
Caller B: no prior row found
Caller A: insert an event
Caller B: insert an event
```

Both looked before either wrote. That is a race condition: the outcome depends
on how their steps overlap.

Our implementation starts `BEGIN IMMEDIATE` before the lookup and quota read:

```text
Caller A reserves SQLite writer access.
Caller B waits for writer access.
A looks up the key, checks quotas, inserts an event, and commits.
B can now continue, finds A's event, and returns A's saved response.
Final event count for that tenant/key: one.
```

The unique `(tenant_id, idempotency_key)` constraint is an additional database
backstop. It rejects another row with that same pair even if another code path
tries to insert one.

The transaction also protects **different** actions competing for the last
allowance. At 999 calls, two different action keys do not mean both may use the
last call. One takes usage to 1,000; the next writer sees 1,000 and gets 429.
The unique constraint alone would not protect that quota race because the two
keys are different. The transaction is needed for the shared allowance.

#### D. Understand what is and is not guaranteed

One action can be submitted many times; it has one accepted stored effect for
that tenant/key. Failed new requests create no event, so a later retry after an
upgrade can be evaluated again. Replaying an accepted request returns history.

This is not a promise that any future external AI call will execute only once.
There is no such call in this project. Adding one would introduce a separate
side effect that needs its own durable workflow/idempotency design. Likewise,
Stripe Checkout uses a separate stable provider key; a local SQLite transaction
does not reach across the network and atomically commit inside Stripe.

**A concise explanation to practice:**

> Each action has an idempotency key scoped to its tenant. We save the normalized
> request fingerprint and original response with the usage event. A matching retry
> returns that response; a different body under the same key gets 409. The lookup,
> quota check, and event insertion happen in one SQLite write transaction, and a
> unique tenant/key constraint prevents duplicate rows. That also stops concurrent
> callers from both taking the last quota allowance.

**Check yourself:** Why is a unique action-key constraint insufficient for two
different actions at 999/1,000 calls? Answer without merely saying "because of
idempotency." Describe what each caller would read and write.

### 10.2 Cached input and reasoning tokens must not be counted twice

**The problem:** related token fields can describe overlapping categories.
Adding every number together can count the same token more than once.

This API uses an explicit inclusive convention:

```text
input_tokens = all input tokens
    cached_input_tokens are already inside this total

output_tokens = all output tokens
    reasoning_tokens are already inside this total
```

The caller must supply counts using that convention. If a real provider were
connected later, its response would need to be mapped into this API's documented
convention; do not assume all providers use identical field definitions.

#### A. Draw the two groups before doing arithmetic

For this example:

```json
{
  "input_tokens": 1000,
  "cached_input_tokens": 400,
  "output_tokens": 500,
  "reasoning_tokens": 200
}
```

The groups are:

```text
Input total: 1,000
  Fresh input:   600
  Cached input:  400

Output total: 500
  Non-reasoning output: 300
  Reasoning output:     200
```

Total unique quota tokens are `1000 + 500 = 1500`.

The tempting formula `1000 + 400 + 500 + 200 = 2100` is wrong for this API. It
adds the cached subset and reasoning subset a second time. Their separate
reporting fields help explain usage; they do not represent additional tokens.

#### B. Apply the correct rate to each unique category

Cached input is cheaper than fresh input. We need to split the inclusive input
total into its non-overlapping parts:

```text
Fresh input = total input - cached input
            = 1000 - 400
            = 600
```

Reasoning output has the same rate as the rest of output, so we charge the
inclusive output total once. `cost_of()` implements:

```python
def cost_of(request: Generation):
    return (
        API_CALL_MICRO_USD
        + (request.input_tokens - request.cached_input_tokens) * INPUT_TOKEN_MICRO_USD
        + request.cached_input_tokens * CACHED_TOKEN_MICRO_USD
        + request.output_tokens * OUTPUT_TOKEN_MICRO_USD
    )
```

The names describe units, not arbitrary magic numbers. With the pinned rates:

| Item | Calculation | Cost in micro-USD |
|---|---|---:|
| One API call | `1 * 1000` | 1,000 |
| Fresh input | `(1000 - 400) * 2` | 1,200 |
| Cached input | `400 * 1` | 400 |
| All output, including reasoning | `500 * 8` | 4,000 |
| Total | `1000 + 1200 + 400 + 4000` | **6,600** |

There is no extra `reasoning_tokens * 8` term. That would charge for tokens
already present in `output_tokens`.

#### C. Keep money in integer units

```text
1 USD = 1,000,000 micro-USD
6,600 micro-USD = USD 0.006600
```

All calculations above are integer additions and multiplications. Binary
floating-point numbers cannot represent every decimal fraction exactly. Choosing
integer micro-units lets these pinned rates and totals be represented exactly.

`usd_string(6600)` uses integer division and a zero-padded remainder to produce
`"0.006600"`. The API returns a string for the decimal display and an integer
for `cost_micro_usd`; it does not turn the amount into a float for storage.

#### D. Validate the relationships

Cached input greater than total input would imply a negative fresh count.
Reasoning greater than total output would contradict the inclusive output
convention. The `Generation` validator rejects both cases before metering.
Database checks repeat the subset rules as another line of protection.

Two useful predictions are:

- Keep output at 500 and change reasoning from 200 to 300: total tokens and cost
  stay unchanged, because output is still 500.
- Keep input at 1,000 and change cached from 400 to 500: total tokens stay
  unchanged, but cost falls by 100 micro-USD because 100 tokens move from a rate
  of 2 to a rate of 1.

These are tested properties, not just one memorized example.

**A concise explanation to practice:**

> Our input count already includes cached input, and our output count already
> includes reasoning. The quota total is input plus output. For pricing, we
> subtract cached input from total input to find the fresh portion, charge each
> input portion at its rate, and charge total output once. Reasoning is already
> billed inside that output total. We store the result as integer micro-USD to
> keep the arithmetic exact.

**Check yourself:** Can you compute the total tokens and cost for input=200,
cached=50, output=100, reasoning=40? Include the API call fee. The answer is in
Section 11 so you can try the calculation first.

### 10.3 A verified webhook grants Pro, not the success page

**The problem:** the user's browser is not a trustworthy source of subscription
truth. Someone can visit a URL directly, and a legitimate customer's browser
might disconnect before returning from a successful payment.

If `/billing/success` changed the database to Pro, simply visiting that route
could grant access without the required payment flow. It would also tie payment
fulfillment to whether the browser completed a redirect.

#### A. Give the browser and Stripe different responsibilities

The browser shows Checkout and a confirmation message. Stripe reports the
payment/subscription event to the backend. Our backend authenticates the message
and synchronizes its local view.

The actual `checkout_success()` route only returns HTML. There is no call to
`UPDATE subscriptions` in that route. The same is true of the browser-cancel page.

#### B. Verify the exact incoming bytes

The important call in `stripe_webhook()` is:

```python
stripe.Webhook.construct_event(
    bytes(payload),
    request.headers.get("stripe-signature", ""),
    settings.webhook_secret,
).to_dict()
```

The three inputs have different roles:

| Input | Why it is needed |
|---|---|
| Original body bytes | The message whose contents are being checked. |
| `Stripe-Signature` header | The signature and timestamp supplied with the message. |
| `whsec_...` secret | The locally configured secret used to verify it. |

The SDK checks signature validity and timestamp tolerance. Parsing and rewriting
JSON before checking would risk changing the signed bytes. Even adding a space
to the body after it was signed makes the existing signature fail.

The `sk_test_...` API key has a separate job: it authenticates our outgoing API
calls to Stripe. A tenant's `X-API-Key` is different again: it authenticates a
client calling our own protected routes. A price ID is an object identifier,
not a credential that authenticates either direction.

#### C. Persist the work before acknowledging delivery

After verification and event validation, the handler inserts a job under the
unique Stripe event ID. Only after the transaction commits does it acknowledge
delivery. `200 queued` means the work was durably received; it does not promise
the background sync already succeeded.

If the same supported event arrives again, the primary key prevents another
job and the handler returns `duplicate`. The worker will not process a second
copy merely because Stripe retried delivery.

The signed customer reference must match a customer previously stored for a
tenant. A valid event from an unrelated customer does not get to choose a local
tenant through metadata.

#### D. Synchronize current subscription truth

The worker retrieves the subscription from Stripe using the outgoing test key.
It checks that it belongs to the expected customer, is test mode, and has the
configured Pro price and quantity. Then it applies the supported status.

Fetching current state matters because event delivery can be out of order:

```text
An old event snapshot says active.
A later event/canonical subscription says canceled.
The old event arrives late.
```

Blindly applying the old snapshot could grant paid access again. Reading current
subscription truth lets the worker retain the canceled/Free result instead.

The local subscription change and job's `done` marker share one transaction.
They either commit together or roll back together. The worker also checks its
claim token so an old, replaced worker cannot apply a stale result.

#### E. Recognize eventual consistency

There can be a short interval between browser payment completion and the local
plan becoming Pro. The delivery and worker processing are separate operations.
During that interval, `/billing/status` is the diagnostic source for queued or
failed work; merely reloading the success page cannot legitimately force Pro.

In the real acceptance run, signed Checkout upgraded the tenant, replaying the
event twice left attempts at one, and signed cancellation later returned it to
Free while preserving the usage ledger. These are recorded results, not just
assumptions about the route names.

**A concise explanation to practice:**

> The browser success page only displays a message because a browser redirect
> is not payment proof. We verify Stripe's webhook signature against the original
> body and our signing secret, then save a job deduplicated by event ID. The
> worker retrieves current Stripe subscription state, checks ownership and the
> Pro price, and atomically updates our local subscription and marks the job done.
> That makes verified payment state, rather than visiting a URL, control access.

**Check yourself:** What should happen if payment succeeds but the browser closes
before redirecting? What should happen if somebody visits `/billing/success`
without paying? Explain the two cases separately.

## 11. Practice without guessing

### A reading order that builds understanding

1. Read Sections 1-5 of this guide and identify the request body, headers, and
   response in an example.
2. Open `models.py`, then `config.py`, then `001_initial.sql`. Understand the
   shape of one valid action and where prices/quotas are defined.
3. Read `db.connect()` and follow `metering.record()` slowly. Trace one successful
   action, a retry, a conflicting retry, and an over-quota new action.
4. Follow the small `/generate` and `/usage` routes in `main.py` back to those
   helpers. Explain how the route receives the authenticated tenant.
5. Follow `payments.checkout()`, the webhook route, `enqueue_event()`, and
   `jobs.run_once()` in that order.
6. Read the matching tests. Predict their expected behavior before running them.
7. Read `TUTORIAL.md` and `EVIDENCE.md` after you can distinguish a claim, a
   synthetic test, and a real-account observation.

### Start with read-only exploration

From the project directory, with the existing server running:

```powershell
.\.venv\Scripts\python.exe scripts/probe.py usage
.\.venv\Scripts\python.exe scripts/probe.py status
```

These read your demo state without creating another usage event or subscription.
If no server is running, follow the README to start one. Do not start a second
server on a port already used by the first.

To run isolated tests instead of changing demo records:

```powershell
.\.venv\Scripts\python.exe -m pytest tests/test_metering.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_jobs.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_http_payments.py -q
```

Read the test names in Section 8 to decide what each run is checking. To see
individual case names, replace `-q` with `-v`. On an activated environment, `python`
can replace the explicit Windows virtual-environment path above.

### Predict these outcomes before looking at the answers

1. One tenant sends the same normalized body twice with the same action key.
   How many events are saved, and do event IDs differ?
2. The second request uses the same key but changes output from 20 to 21.
   Is that a retry, new event, or error?
3. A and B use the same key string. Does B receive A's response?
4. A tenant has used 999 calls. Two different new actions arrive together.
   How many should succeed?
5. A tenant is at its token quota but still has calls available. Can `{}` succeed?
6. Yesterday's successful action is retried today after a payment problem.
   Does the retry create a new charge or get blocked as a new request?
7. Input=200, cached=50, output=100, reasoning=40. What are quota tokens and cost?
8. Reasoning increases but total output stays unchanged. Does the cost increase?
9. An unsigned request claims `checkout.session.completed`. Does it create a job?
10. A valid supported event arrives twice. How many inbox rows does it create?
11. A worker's lease expires and another claim replaces its token. Can the old
    worker still update the subscription when it finishes?
12. A canceled subscription gets an old active event snapshot. Which state should
    the worker consult before updating access?

### Answer key and reasoning

1. One event. The retry returns the original event ID and saved response.
2. A 409 conflict. A successful action's identity cannot be reused for different
   normalized input.
3. No. Lookup and uniqueness include the authenticated tenant. B's action is
   evaluated against B's own subscription/usage.
4. One succeeds, reaching 1,000. The next writer reads 1,000 and gets 429. The
   transaction prevents both from consuming the same remaining allowance.
5. Yes, if payment status permits it. `{}` requests zero tokens but one API call.
6. It replays history. A different new action would get 402; the old accepted key
   returns its original response without another event.
7. Tokens: `200 + 100 = 300`. Cost: `1000 + (200-50)*2 + 50*1 + 100*8 = 2150`
   micro-USD, displayed as `"0.002150"`. Reasoning is already inside output.
8. No. The inclusive output total was already charged once.
9. No. Missing/bad signature gets 400 before enqueuing.
10. One row under the unique event ID. The duplicate delivery adds no new job.
11. No. The old token no longer matches, so it cannot commit that subscription
    result. A lease without that token check would be weaker.
12. The current subscription retrieved from Stripe, with ownership/mode/plan
    checks. An old snapshot should not resurrect canceled paid access.

### Build your own explanation from a small excerpt

Choose two or three source lines. Describe:

1. What data enters them.
2. What decision or transformation they perform.
3. What happens if their condition fails.
4. Which bug they prevent.
5. Which test exercises that behavior.

For example, explaining `return json.loads(prior["response_json"])` requires
more than saying "it returns JSON." You should be able to explain where `prior`
came from, what was saved, why the body fingerprint was checked first, and why
the replay happens before the quota check.

When you can do that comfortably, add a truthful note to `BUILDLOG.md` describing
what you reviewed and any experiments or corrections you actually made. Do not
copy a sample explanation and claim it proves independent understanding.

## 12. Limits and claims you can defend

These distinctions keep your explanation precise:

| Accurate claim | What it does not establish |
|---|---|
| Accepted metering has one database effect per retained tenant/action key. | Universal exactly-once execution of arbitrary external services. |
| SQLite transactions protect the current check-and-insert path. | Unlimited concurrent write throughput or a distributed deployment guarantee. |
| The worker uses durable jobs, backoff, leases and claim tokens. | An email/paging service, continuous reconciliation, or a general-purpose distributed queue product. |
| The API validates strict counts and inclusive subsets. | Automatic correctness for every AI provider's differently named token fields. |
| Stored charges and rollups use integer money. | Usage invoicing, collecting those charges through Stripe, or current market pricing. |
| Signed webhook jobs synchronize supported Stripe test subscriptions. | Production deployment readiness, live payments, or automatic recovery from every missed webhook. |
| Automated tests and real-account transcripts support the documented cases. | A guarantee that all future code changes, API changes, or operating conditions are covered. |

The intended scope is small: two plans, two usage quantities, one simulated
billable action, and a test-mode subscription integration. There is no real AI
generation, invoicing, overage billing, proration implementation, signup UI, or
production hosting in this core project.

The strongest demonstration is to explain the actual path and its boundaries:
identify the caller, show the validated data, trace the transaction or webhook
job, predict the outcome, and point to the evidence that checks it.
