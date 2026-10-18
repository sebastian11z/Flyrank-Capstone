import hashlib
import hmac
import json
import time

import pytest
import stripe
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import connect
from app.jobs import run_once
from app.main import create_app
from app.metering import usage
from app.payments import StripeGateway, checkout
from tests.conftest import KEY_A, KEY_B
from tests.fakes import FakeStripe, event, subscription


def signed(payload, secret, timestamp=None):
    raw = json.dumps(payload, separators=(",", ":")).encode()
    stamp = int(time.time()) if timestamp is None else timestamp
    signature = hmac.new(secret.encode(), str(stamp).encode() + b"." + raw, hashlib.sha256).hexdigest()
    return raw, {"Stripe-Signature": f"t={stamp},v1={signature}"}


@pytest.fixture
def gateway():
    return FakeStripe()


@pytest.fixture
def client(settings, gateway):
    with TestClient(create_app(settings, gateway, start_worker=False)) as client:
        yield client


def test_http_idempotency_response_identity_and_isolation(client):
    headers = {"X-API-Key": KEY_A, "Idempotency-Key": "http-retry"}
    body = {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 500, "reasoning_tokens": 200}
    first = client.post("/generate", headers=headers, json=body)
    second = client.post("/generate", headers=headers, json=body)
    assert first.status_code == second.status_code == 200
    assert first.content == second.content
    assert first.json()["cost_micro_usd"] == 6600
    assert client.get("/usage", headers={"X-API-Key": KEY_A}).json()["used"]["events"] == 1
    assert client.get("/usage", headers={"X-API-Key": KEY_B}).json()["used"]["events"] == 0
    assert client.post("/generate", headers=headers, json={}).status_code == 409


def test_http_quota_and_payment_status_codes(client, settings):
    headers = {"X-API-Key": KEY_A, "Idempotency-Key": "limit"}
    assert client.post("/generate", headers=headers, json={"input_tokens": 100_000}).status_code == 200
    blocked = client.post("/generate", headers={**headers, "Idempotency-Key": "beyond"}, json={"output_tokens": 1})
    assert blocked.status_code == 429
    assert blocked.json()["used"] == blocked.json()["limit"] == 100_000
    assert blocked.json()["message"] and int(blocked.headers["Retry-After"]) > 0
    with connect(settings.db_path, write=True) as db:
        db.execute("UPDATE subscriptions SET plan_id='pro',status='past_due' WHERE tenant_id='tenant_a'")
    payment = client.post("/generate", headers={**headers, "Idempotency-Key": "unpaid"}, json={})
    assert payment.status_code == 402
    assert payment.json()["message"]


@pytest.mark.parametrize("body", [{"input_tokens": -1}, {"input_tokens": True}, {"output_tokens": 2.5},
                                 {"cached_input_tokens": 2}, {"tenant_id": "tenant_b"}, [1], None])
def test_bad_input_is_4xx_not_500(client, body):
    response = client.post("/generate", headers={"X-API-Key": KEY_A, "Idempotency-Key": "invalid"}, json=body)
    assert response.status_code == 422


def test_auth_headers_and_validation_do_not_echo_credentials(client):
    assert client.get("/usage").status_code == 401
    assert client.get("/usage", headers={"X-API-Key": KEY_A + "wrong"}).status_code == 401
    invalid_key = "secret-marker-" + "a" * 513
    response = client.get("/usage", headers={"X-API-Key": invalid_key})
    assert response.status_code == 422 and invalid_key not in response.text
    assert client.post("/generate", headers={"X-API-Key": KEY_A}, json={}).status_code == 422
    assert client.post("/generate", headers={"X-API-Key": KEY_A, "Idempotency-Key": "a b"}, json={}).status_code == 422


def test_checkout_and_signed_webhook_upgrade_then_replay(client, settings, gateway):
    headers = {"X-API-Key": KEY_A}
    first = client.post("/billing/checkout", headers=headers)
    assert first.status_code == 200
    assert client.post("/billing/checkout", headers=headers).json() == first.json()
    assert gateway.customers_created == 1
    assert len(gateway.checkout_attempt_ids) == 1
    assert client.get("/usage", headers=headers).json()["plan"] == "free"
    gateway.subscriptions["sub_a"] = subscription()
    payload, signature = signed(event(), settings.webhook_secret)
    assert client.post("/webhooks/stripe", content=payload, headers=signature).json() == {"status": "queued"}
    assert run_once(settings, gateway)
    upgraded = client.get("/usage", headers=headers).json()
    assert upgraded["plan"] == "pro"
    assert upgraded["limit"] == {"api_calls": 10_000, "ai_tokens": 1_000_000}
    assert client.post("/webhooks/stripe", content=payload, headers=signature).json() == {"status": "duplicate"}
    assert not run_once(settings, gateway)
    own = client.get("/billing/status", headers=headers).json()
    assert own["jobs"][0]["status"] == "done" and own["jobs"][0]["attempts"] == 1
    assert client.get("/billing/status", headers={"X-API-Key": KEY_B}).json()["jobs"] == []
    assert client.post("/billing/checkout", headers=headers).status_code == 409


def test_forged_stale_or_changed_signature_changes_nothing(client, settings):
    payload, signature = signed(event(), settings.webhook_secret)
    assert client.post("/webhooks/stripe", content=payload, headers={"Stripe-Signature": "bad"}).status_code == 400
    assert client.post("/webhooks/stripe", content=payload + b" ", headers=signature).status_code == 400
    payload, signature = signed(event(), settings.webhook_secret, int(time.time()) - 600)
    assert client.post("/webhooks/stripe", content=payload, headers=signature).status_code == 400
    assert usage(settings.db_path, "tenant_a")["plan"] == "free"
    with connect(settings.db_path) as db:
        assert db.execute("SELECT COUNT(*) FROM webhook_jobs").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["live", "missing_customer", "bad_data", "bad_object", "bad_id"])
def test_signed_but_invalid_webhook_returns_400(client, settings, change):
    data = event()
    if change == "live":
        data["livemode"] = True
    elif change == "missing_customer":
        del data["data"]["object"]["customer"]
    elif change == "bad_data":
        data["data"] = [1]
    elif change == "bad_object":
        data["data"]["object"] = "wrong"
    else:
        data["id"] = 123
    raw, headers = signed(data, settings.webhook_secret)
    assert client.post("/webhooks/stripe", content=raw, headers=headers).status_code == 400


def test_disabled_stripe_is_explicit_503(settings):
    disabled = Settings(db_path=settings.db_path)
    with TestClient(create_app(disabled, start_worker=False)) as client:
        assert client.post("/billing/checkout", headers={"X-API-Key": KEY_A}).status_code == 503
        assert client.post("/webhooks/stripe", content=b"{}").status_code == 503


def test_sdk_failure_is_sanitized(client, gateway, caplog):
    gateway.validate_price = lambda: (_ for _ in ()).throw(stripe.APIConnectionError("secret-never-print-me"))
    result = client.post("/billing/checkout", headers={"X-API-Key": KEY_A})
    assert result.status_code == 503
    assert "secret-never-print-me" not in result.text + caplog.text


def test_checkout_unknown_result_reuses_durable_attempt_after_network_failure(settings, gateway):
    original = gateway.create_checkout
    def fail_after_creation(*args):
        original(*args)
        raise stripe.APIConnectionError("connection dropped after provider created session")
    gateway.create_checkout = fail_after_creation
    with pytest.raises(stripe.APIConnectionError):
        checkout(settings, gateway, "tenant_a")
    gateway.create_checkout = original
    checkout(settings, gateway, "tenant_a")
    assert len(gateway.checkout_attempt_ids) == 2
    assert gateway.checkout_attempt_ids[0] == gateway.checkout_attempt_ids[1]
    assert len(gateway.sessions) == 1


def test_official_sdk_encodes_checkout_and_customer_contract(settings):
    class RecordingHTTPClient(stripe.HTTPClient):
        name = "capstone-fixture"
        def __init__(self):
            super().__init__()
            self.calls = []
        def request(self, method, url, headers, post_data=None, **kwargs):
            self.calls.append((method, url, headers, post_data))
            if "/prices/" in url:
                data = {"id": "price_pro", "object": "price", "active": True, "livemode": False,
                        "currency": "usd", "unit_amount": 1900,
                        "recurring": {"interval": "month", "interval_count": 1, "usage_type": "licensed"}}
            elif "/checkout/sessions" in url:
                data = {"id": "cs_test_fixture", "object": "checkout.session", "livemode": False, "url": "https://checkout.stripe.com/test"}
            else:
                data = {"id": "cus_fixture", "object": "customer"}
            return json.dumps(data), 200, {"request-id": "req_fixture"}
    gateway = StripeGateway(settings)
    http = RecordingHTTPClient()
    gateway.client = stripe.StripeClient(settings.stripe_secret, http_client=http)
    gateway.validate_price()
    gateway.create_customer("tenant_a", "stable_customer")
    gateway.create_checkout("tenant_a", "cus_fixture", "stable_attempt")
    from urllib.parse import parse_qs
    body = parse_qs(http.calls[-1][3])
    assert body["mode"] == ["subscription"]
    assert body["customer"] == ["cus_fixture"]
    assert body["line_items[0][price]"] == ["price_pro"]
    assert body["allowed_payment_method_types[0]"] == ["card"]
    assert "payment_method_types[0]" not in body
    assert body["subscription_data[metadata][tenant_id]"] == ["tenant_a"]
    assert http.calls[-1][2]["Idempotency-Key"] == "metering-checkout-stable_attempt"
    assert http.calls[-2][2]["Idempotency-Key"] == "metering-customer-stable_customer"


def test_worker_runs_off_http_path_and_health_exposes_exhausted_jobs(settings, gateway):
    import threading
    started, release = threading.Event(), threading.Event()
    gateway.subscriptions["sub_a"] = subscription()
    retrieve = gateway.retrieve_subscription
    def delayed(sub_id):
        started.set()
        assert release.wait(5)
        return retrieve(sub_id)
    gateway.retrieve_subscription = delayed
    with TestClient(create_app(settings, gateway, start_worker=True)) as client:
        try:
            assert client.post("/billing/checkout", headers={"X-API-Key": KEY_A}).status_code == 200
            raw, headers = signed(event(), settings.webhook_secret)
            assert client.post("/webhooks/stripe", content=raw, headers=headers).json() == {"status": "queued"}
            assert started.wait(3)
            assert client.get("/usage", headers={"X-API-Key": KEY_A}).json()["plan"] == "free"
            release.set()
            until = time.monotonic() + 5
            while time.monotonic() < until and usage(settings.db_path, "tenant_a")["plan"] != "pro":
                time.sleep(0.02)
            assert client.get("/usage", headers={"X-API-Key": KEY_A}).json()["plan"] == "pro"
            with connect(settings.db_path, write=True) as db:
                db.execute("UPDATE webhook_jobs SET status='failed' WHERE event_id='evt_fixture'")
            health = client.get("/health")
            assert health.status_code == 503 and health.json()["failed_background_jobs"] == 1
        finally:
            release.set()


@pytest.mark.parametrize("change", ["live", "inactive", "amount", "currency", "interval", "metered"])
def test_checkout_rejects_misconfigured_stripe_price(settings, change):
    from app.models import DomainError
    data = {"id": "price_pro", "object": "price", "active": True, "livemode": False,
            "currency": "usd", "unit_amount": 1900,
            "recurring": {"interval": "month", "interval_count": 1, "usage_type": "licensed"}}
    if change == "live":
        data["livemode"] = True
    elif change == "inactive":
        data["active"] = False
    elif change == "amount":
        data["unit_amount"] = 1901
    elif change == "currency":
        data["currency"] = "php"
    elif change == "interval":
        data["recurring"]["interval"] = "year"
    else:
        data["recurring"]["usage_type"] = "metered"
    gateway = StripeGateway(settings)
    gateway.client.v1.prices.retrieve = lambda _: stripe.Price.construct_from(data, settings.stripe_secret)
    with pytest.raises(DomainError) as error:
        gateway.validate_price()
    assert error.value.status == 503


def test_checkout_expiry_and_ambiguous_old_attempt(settings, gateway):
    from app.models import DomainError
    first = checkout(settings, gateway, "tenant_a")
    gateway.sessions[first["session_id"]]["status"] = "expired"
    with pytest.raises(DomainError) as error:
        checkout(settings, gateway, "tenant_a")
    assert error.value.body["error"] == "checkout_expired"
    second = checkout(settings, gateway, "tenant_a")
    assert second["session_id"] != first["session_id"]
    with connect(settings.db_path, write=True) as db:
        db.execute("UPDATE checkout_attempts SET session_id=NULL,created_at=? WHERE tenant_id='tenant_a'",
                   (int(time.time()) - 24 * 3600,))
    with pytest.raises(DomainError) as error:
        checkout(settings, gateway, "tenant_a")
    assert error.value.body["error"] == "checkout_needs_reconciliation"

