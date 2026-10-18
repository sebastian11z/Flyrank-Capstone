CREATE TABLE plans (
    id TEXT PRIMARY KEY CHECK (id IN ('free', 'pro')),
    api_call_limit INTEGER NOT NULL CHECK (api_call_limit > 0),
    token_limit INTEGER NOT NULL CHECK (token_limit > 0)
);
INSERT INTO plans VALUES ('free', 1000, 100000), ('pro', 10000, 1000000);

CREATE TABLE tenants (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    api_key_hash TEXT NOT NULL UNIQUE,
    stripe_customer_id TEXT UNIQUE
);
CREATE TABLE subscriptions (
    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
    plan_id TEXT NOT NULL REFERENCES plans(id),
    status TEXT NOT NULL,
    stripe_subscription_id TEXT UNIQUE,
    updated_at TEXT NOT NULL
);
CREATE TABLE usage_events (
    id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL REFERENCES tenants(id),
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    period TEXT NOT NULL,
    api_calls INTEGER NOT NULL CHECK (api_calls = 1),
    input_tokens INTEGER NOT NULL CHECK (input_tokens >= 0),
    cached_input_tokens INTEGER NOT NULL CHECK (cached_input_tokens BETWEEN 0 AND input_tokens),
    output_tokens INTEGER NOT NULL CHECK (output_tokens >= 0),
    reasoning_tokens INTEGER NOT NULL CHECK (reasoning_tokens BETWEEN 0 AND output_tokens),
    cost_micro_usd INTEGER NOT NULL CHECK (cost_micro_usd >= 0),
    pricing_version TEXT NOT NULL,
    response_json TEXT NOT NULL,
    UNIQUE (tenant_id, idempotency_key)
);
CREATE INDEX usage_tenant_period ON usage_events(tenant_id, period);

