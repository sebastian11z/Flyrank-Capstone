CREATE TABLE checkout_attempts (
    tenant_id TEXT PRIMARY KEY REFERENCES tenants(id),
    attempt_id TEXT NOT NULL UNIQUE,
    created_at INTEGER NOT NULL,
    session_id TEXT
);
CREATE TABLE webhook_jobs (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT REFERENCES tenants(id),
    event_type TEXT NOT NULL,
    customer_id TEXT NOT NULL,
    subscription_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('pending','processing','done','ignored','failed')),
    attempts INTEGER NOT NULL DEFAULT 0,
    available_at INTEGER NOT NULL,
    lease_until INTEGER,
    lease_token TEXT,
    last_error_type TEXT
);
CREATE INDEX jobs_ready ON webhook_jobs(status, available_at);
CREATE INDEX jobs_tenant ON webhook_jobs(tenant_id, available_at);

