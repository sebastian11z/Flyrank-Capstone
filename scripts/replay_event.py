"""Replay an already-processed real Stripe test event without exposing its payload."""
import hashlib
import hmac
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx
import stripe
from dotenv import load_dotenv


def main():
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    if len(sys.argv) != 2 or not re.fullmatch(r"evt_[A-Za-z0-9_]+", sys.argv[1]):
        raise SystemExit("Usage: python scripts/replay_event.py evt_REAL_TEST_EVENT_ID")
    event_id = sys.argv[1]
    key = os.getenv("STRIPE_SECRET_KEY", "")
    secret = os.getenv("STRIPE_WEBHOOK_SECRET", "")
    api_key = os.getenv("DEMO_TENANT_A_KEY", "")
    if not key.startswith("sk_test_") or not secret.startswith("whsec_") or not api_key:
        raise SystemExit("Configure Stripe test key, webhook secret, and tenant A key in .env first")
    gateway = stripe.StripeClient(key, http_client=stripe.RequestsClient(timeout=10), max_network_retries=1)
    try:
        event = gateway.v1.events.retrieve(event_id).to_dict()
    except stripe.StripeError as error:
        raise SystemExit(f"Stripe retrieval failed ({type(error).__name__}); no payload logged") from None
    assert event["id"] == event_id and event.get("livemode") is False, "A real test event is required"
    payload = json.dumps(event, separators=(",", ":")).encode()
    base = os.getenv("BASE_URL", "http://127.0.0.1:8000")
    with httpx.Client(base_url=base, headers={"X-API-Key": api_key}, timeout=30) as client:
        status = client.get("/billing/status")
        status.raise_for_status()
        original = next((job for job in status.json()["jobs"] if job["event_id"] == event_id), None)
        if not original or original["status"] != "done":
            raise SystemExit("First wait for this tenant A event's initial CLI delivery to finish (latest 20 jobs only)")
        for _ in range(2):
            timestamp = str(int(time.time()))
            digest = hmac.new(secret.encode(), timestamp.encode() + b"." + payload, hashlib.sha256).hexdigest()
            result = client.post("/webhooks/stripe", content=payload,
                                 headers={"Stripe-Signature": f"t={timestamp},v1={digest}"})
            assert result.status_code == 200 and result.json() == {"status": "duplicate"}, "Replay not deduplicated"
            print(f"POST /webhooks/stripe {event_id}: 200 duplicate")
        after = client.get("/billing/status")
        after.raise_for_status()
        current = next(job for job in after.json()["jobs"] if job["event_id"] == event_id)
        assert current == original, "Replay changed processing state"
        print(json.dumps(current, indent=2))
        print("Real test event replayed twice; original processing state unchanged.")


if __name__ == "__main__":
    main()

