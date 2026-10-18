import time
from uuid import uuid4

import stripe

from app.config import PRO_MONTHLY_CENTS, Settings
from app.db import connect
from app.metering import PAYMENT_BLOCKED, plan_for
from app.models import DomainError

SUPPORTED_EVENTS = {
    "checkout.session.completed", "customer.subscription.updated", "customer.subscription.deleted",
}


class StripeGateway:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.client = stripe.StripeClient(
            settings.stripe_secret, max_network_retries=1,
            http_client=stripe.RequestsClient(timeout=10),
        ) if settings.stripe_secret else None

    def create_customer(self, tenant_id, customer_key):
        return self.client.v1.customers.create(
            {"metadata": {"tenant_id": tenant_id}},
            options={"idempotency_key": f"metering-customer-{customer_key}"},
        )["id"]

    def validate_price(self):
        price = self.client.v1.prices.retrieve(self.settings.price_id).to_dict()
        if (price["livemode"] or not price["active"] or price["currency"] != "usd"
                or price["unit_amount"] != PRO_MONTHLY_CENTS
                or (price.get("recurring") or {}).get("interval") != "month"
                or price["recurring"].get("interval_count") != 1
                or price["recurring"].get("usage_type") != "licensed"):
            raise DomainError(503, "stripe_price_mismatch", "Configure an active USD 19/month test recurring price")

    def create_checkout(self, tenant_id, customer_id, attempt_id):
        return self.client.v1.checkout.sessions.create({
            "mode": "subscription", "customer": customer_id,
            "client_reference_id": tenant_id,
            "metadata": {"tenant_id": tenant_id},
            "subscription_data": {"metadata": {"tenant_id": tenant_id}},
            "line_items": [{"price": self.settings.price_id, "quantity": 1}],
            "payment_method_types": ["card"],
            "success_url": f"{self.settings.base_url}/billing/success",
            "cancel_url": f"{self.settings.base_url}/billing/cancel",
        }, options={"idempotency_key": f"metering-checkout-{attempt_id}"}).to_dict()

    def retrieve_checkout(self, session_id):
        return self.client.v1.checkout.sessions.retrieve(session_id).to_dict()

    def retrieve_subscription(self, subscription_id):
        return self.client.v1.subscriptions.retrieve(subscription_id).to_dict()


def require_stripe(settings):
    if not settings.stripe_secret or not settings.price_id:
        raise DomainError(503, "stripe_not_configured", "Set Stripe test key and Pro price ID in .env")


def checkout(settings, gateway, tenant_id):
    require_stripe(settings)
    with connect(settings.db_path, write=True) as db:
        plan = plan_for(db, tenant_id)
        if plan["plan_id"] == "pro" or plan["status"] in PAYMENT_BLOCKED:
            raise DomainError(409, "subscription_exists", "Manage the existing subscription in Stripe; do not buy twice")
        tenant = dict(db.execute("SELECT * FROM tenants WHERE id = ?", (tenant_id,)).fetchone())
        db.execute("INSERT OR IGNORE INTO checkout_attempts VALUES (?,?,?,NULL)",
                   (tenant_id, str(uuid4()), int(time.time())))
        attempt = dict(db.execute("SELECT * FROM checkout_attempts WHERE tenant_id = ?", (tenant_id,)).fetchone())

    if attempt["session_id"]:
        session = gateway.retrieve_checkout(attempt["session_id"])
        if session["status"] == "complete":
            raise DomainError(409, "checkout_sync_pending", "Checkout completed; wait for the verified webhook")
        if session["status"] == "expired":
            with connect(settings.db_path, write=True) as db:
                db.execute("DELETE FROM checkout_attempts WHERE tenant_id = ? AND attempt_id = ?",
                           (tenant_id, attempt["attempt_id"]))
            raise DomainError(409, "checkout_expired", "Checkout expired; retry to create a new session")
        return {"session_id": session["id"], "checkout_url": session["url"]}

    if int(time.time()) - attempt["created_at"] >= 23 * 3600:
        # Stripe may prune idempotency records after 24h. Never risk buying twice.
        raise DomainError(409, "checkout_needs_reconciliation", "Uncertain old Checkout attempt; reconcile in Stripe first")
    gateway.validate_price()
    customer_id = tenant["stripe_customer_id"]
    if not customer_id:
        customer_id = gateway.create_customer(tenant_id, tenant["api_key_hash"])
        with connect(settings.db_path, write=True) as db:
            db.execute("UPDATE tenants SET stripe_customer_id = ? WHERE id = ? AND stripe_customer_id IS NULL",
                       (customer_id, tenant_id))
            customer_id = db.execute("SELECT stripe_customer_id FROM tenants WHERE id = ?", (tenant_id,)).fetchone()[0]
    session = gateway.create_checkout(tenant_id, customer_id, attempt["attempt_id"])
    if session.get("livemode") is not False:
        raise DomainError(503, "stripe_mode_mismatch", "Only Stripe test Checkout is permitted")
    with connect(settings.db_path, write=True) as db:
        db.execute("UPDATE checkout_attempts SET session_id = ? WHERE tenant_id = ? AND attempt_id = ?",
                   (session["id"], tenant_id, attempt["attempt_id"]))
    return {"session_id": session["id"], "checkout_url": session["url"]}


def enqueue_event(settings, event):
    event_type = event.get("type")
    if event.get("livemode") is not False:
        raise DomainError(400, "invalid_event_mode", "Only Stripe test-mode events are accepted")
    if not isinstance(event_type, str):
        raise DomainError(400, "invalid_event", "Missing event type")
    if event_type not in SUPPORTED_EVENTS:
        return {"status": "ignored"}
    data = event.get("data")
    obj = data.get("object") if isinstance(data, dict) else None
    if not isinstance(obj, dict):
        raise DomainError(400, "invalid_event", "Missing event object")
    event_id, customer_id = event.get("id"), obj.get("customer")
    sub_id = obj.get("subscription") if event_type == "checkout.session.completed" else obj.get("id")
    if not all(isinstance(value, str) and value.startswith(prefix) and len(value) <= 255
               for value, prefix in ((event_id, "evt_"), (customer_id, "cus_"), (sub_id, "sub_"))):
        raise DomainError(400, "invalid_event", "Expected event, customer, and subscription IDs")
    with connect(settings.db_path, write=True) as db:
        tenant = db.execute("SELECT id FROM tenants WHERE stripe_customer_id = ?", (customer_id,)).fetchone()
        inserted = db.execute(
            "INSERT OR IGNORE INTO webhook_jobs "
            "(event_id,tenant_id,event_type,customer_id,subscription_id,status,available_at) VALUES (?,?,?,?,?,?,?)",
            (event_id, tenant["id"] if tenant else None, event_type, customer_id, sub_id,
             "pending" if tenant else "ignored", int(time.time())),
        ).rowcount
        return {"status": "queued" if inserted and tenant else "ignored" if inserted else "duplicate"}

