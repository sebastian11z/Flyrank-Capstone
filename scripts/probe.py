"""Live HTTP evidence without printing credentials. Run from the repository root."""
import json
import os
import sys
from pathlib import Path

import httpx
from dotenv import load_dotenv


def main():
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    key = os.getenv("DEMO_TENANT_A_KEY")
    if not key:
        raise SystemExit("Run python -m app.seed first")
    base = os.getenv("BASE_URL", "http://127.0.0.1:8000")
    command = sys.argv[1] if len(sys.argv) > 1 else "core"
    with httpx.Client(base_url=base, headers={"X-API-Key": key}, timeout=30) as client:
        if command == "core":
            from uuid import uuid4
            headers = {"Idempotency-Key": f"probe-{uuid4()}"}
            body = {"input_tokens": 1000, "cached_input_tokens": 400, "output_tokens": 500, "reasoning_tokens": 200}
            before = client.get("/usage")
            before.raise_for_status()
            first = client.post("/generate", headers=headers, json=body)
            second = client.post("/generate", headers=headers, json=body)
            first.raise_for_status()
            second.raise_for_status()
            assert first.content == second.content, "Retry response changed"
            after = client.get("/usage")
            after.raise_for_status()
            assert after.json()["used"]["events"] == before.json()["used"]["events"] + 1
            assert after.json()["cost_micro_usd"] == before.json()["cost_micro_usd"] + 6600
            print("POST /generate twice: 200, byte-identical responses, one new event, +6600 micro-USD")
            print(json.dumps(first.json(), indent=2))
            print(json.dumps(after.json(), indent=2))
        elif command in {"usage", "status"}:
            result = client.get("/usage" if command == "usage" else "/billing/status")
            result.raise_for_status()
            print(json.dumps(result.json(), indent=2))
        elif command == "checkout":
            result = client.post("/billing/checkout")
            if result.status_code != 200:
                raise SystemExit(f"Checkout returned {result.status_code}: {result.json()['message']}")
            # Checkout URLs act as bearer links; open locally but never include them in public evidence.
            import webbrowser
            webbrowser.open(result.json()["checkout_url"])
            print("Opened Stripe test Checkout in your default browser. No plan change until verified webhook.")
        else:
            raise SystemExit("Usage: python scripts/probe.py [core|usage|status|checkout]")


if __name__ == "__main__":
    main()

