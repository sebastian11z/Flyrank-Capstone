import asyncio
import logging
import sqlite3
import threading
from contextlib import asynccontextmanager
from typing import Annotated

import stripe
from fastapi import Depends, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.config import Settings
from app.db import connect, migrate, tenant_for_key
from app.jobs import worker_loop
from app.metering import record, usage
from app.models import DomainError, Generation
from app.payments import StripeGateway, checkout, enqueue_event

logger = logging.getLogger("billing.http")


def create_app(settings=None, gateway=None, *, start_worker=True):
    settings = settings or Settings.from_env()
    gateway = gateway or StripeGateway(settings)

    @asynccontextmanager
    async def lifespan(application):
        migrate(settings.db_path)
        stop = threading.Event()
        thread = threading.Thread(target=worker_loop, args=(settings, gateway, stop), daemon=True)
        if start_worker:
            thread.start()
        yield
        stop.set()
        if start_worker:
            await asyncio.to_thread(thread.join, 25)

    api = FastAPI(title="FlyRank Usage Metering & Billing", version="1.0.0", lifespan=lifespan,
                  description="Test-mode only. Token totals include their cached/reasoning subsets.")

    @api.exception_handler(DomainError)
    async def domain_error(request, error):
        return JSONResponse(error.body, status_code=error.status, headers=error.headers)

    @api.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Do not echo request/header values, which may contain API keys.
        details = [{"location": item["loc"], "message": item["msg"], "type": item["type"]}
                   for item in error.errors()]
        return JSONResponse({"error": "validation_error", "message": "Invalid request", "details": details}, status_code=422)

    @api.exception_handler(stripe.StripeError)
    async def stripe_error(request, error):
        logger.warning("STRIPE_REQUEST_FAILED error_type=%s", type(error).__name__)
        return JSONResponse({"error": "stripe_unavailable", "message": "Stripe request failed; check test configuration and retry"},
                            status_code=503)

    @api.exception_handler(sqlite3.OperationalError)
    async def database_error(request, error):
        logger.error("DATABASE_UNAVAILABLE error_type=%s", type(error).__name__)
        return JSONResponse({"error": "database_unavailable", "message": "Database temporarily unavailable; retry"}, status_code=503)

    def authenticated_tenant(x_api_key: Annotated[str | None, Header(max_length=512)] = None):
        tenant_id = tenant_for_key(settings.db_path, x_api_key) if x_api_key else None
        if tenant_id is None:
            raise DomainError(401, "unauthorized", "Provide a valid X-API-Key header")
        return tenant_id

    Tenant = Annotated[str, Depends(authenticated_tenant)]

    @api.get("/health")
    def health():
        with connect(settings.db_path) as db:
            failed = db.execute("SELECT COUNT(*) FROM webhook_jobs WHERE status='failed'").fetchone()[0]
        return JSONResponse({"status": "degraded" if failed else "ok", "failed_background_jobs": failed},
                            status_code=503 if failed else 200)

    @api.post("/generate")
    def generate(request: Generation, tenant_id: Tenant,
                 idempotency_key: Annotated[str, Header(min_length=1, max_length=128, pattern=r"^[\x21-\x7e]+$")]):
        return record(settings.db_path, tenant_id, request, idempotency_key)

    @api.get("/usage")
    def get_usage(tenant_id: Tenant):
        return usage(settings.db_path, tenant_id)

    @api.post("/billing/checkout")
    def start_checkout(tenant_id: Tenant):
        return checkout(settings, gateway, tenant_id)

    @api.get("/billing/status")
    def billing_status(tenant_id: Tenant):
        with connect(settings.db_path) as db:
            jobs = db.execute("SELECT event_id,event_type,status,attempts,last_error_type FROM webhook_jobs "
                              "WHERE tenant_id=? ORDER BY available_at DESC,event_id LIMIT 20", (tenant_id,)).fetchall()
        current = usage(settings.db_path, tenant_id)
        return {"tenant_id": tenant_id, "plan": current["plan"], "subscription_status": current["subscription_status"],
                "jobs": [dict(job) for job in jobs]}

    @api.post("/webhooks/stripe")
    async def stripe_webhook(request: Request):
        if not settings.webhook_secret:
            raise DomainError(503, "webhook_not_configured", "Set STRIPE_WEBHOOK_SECRET in .env")
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > 262_144:
                raise DomainError(413, "payload_too_large", "Webhook exceeds 256 KiB")
        try:
            event = stripe.Webhook.construct_event(
                bytes(payload), request.headers.get("stripe-signature", ""), settings.webhook_secret,
            ).to_dict()
        except (ValueError, TypeError, AttributeError, stripe.SignatureVerificationError):
            raise DomainError(400, "invalid_webhook", "Invalid payload or Stripe signature") from None
        return await run_in_threadpool(enqueue_event, settings, event)

    @api.get("/billing/success", response_class=HTMLResponse)
    def checkout_success():
        return "<html lang='en'><title>Checkout received</title><h1>Checkout received</h1><p>Your plan updates after the verified Stripe webhook is processed. Check GET /usage with your API key.</p></html>"

    @api.get("/billing/cancel", response_class=HTMLResponse)
    def checkout_cancel():
        return "<html lang='en'><title>Checkout canceled</title><h1>Checkout canceled</h1><p>Your plan has not changed. You can reopen your existing Checkout session.</p></html>"

    return api


app = create_app()

