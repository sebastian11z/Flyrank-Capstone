import logging

import pytest

from app import jobs
from app.db import connect
from app.metering import usage
from app.payments import enqueue_event
from tests.fakes import FakeStripe, event, subscription


@pytest.fixture
def gateway(settings):
    fake = FakeStripe()
    fake.subscriptions["sub_a"] = subscription()
    with connect(settings.db_path, write=True) as db:
        db.execute("UPDATE tenants SET stripe_customer_id='cus_tenant_a' WHERE id='tenant_a'")
    return fake


def test_background_updates_payment_and_cancellation_from_current_truth(settings, gateway):
    for index, status in enumerate(["active", "past_due", "unpaid", "canceled"]):
        gateway.subscriptions["sub_a"]["status"] = status
        kind = "customer.subscription.deleted" if status == "canceled" else "customer.subscription.updated"
        enqueue_event(settings, event(f"evt_{index}", event_type=kind))
        assert jobs.run_once(settings, gateway)
        result = usage(settings.db_path, "tenant_a")
        assert result["plan"] == ("free" if status == "canceled" else "pro")
        assert result["subscription_status"] == ("free" if status == "canceled" else status)


def test_out_of_order_snapshot_cannot_resurrect_canceled_subscription(settings, gateway):
    snapshot = event(event_type="customer.subscription.updated")
    snapshot["data"]["object"]["status"] = "active"  # old snapshot
    gateway.subscriptions["sub_a"]["status"] = "canceled"  # current payment truth
    enqueue_event(settings, snapshot)
    jobs.run_once(settings, gateway)
    assert usage(settings.db_path, "tenant_a")["plan"] == "free"


def test_durable_job_retries_then_failure_alert_without_secrets(settings, gateway, caplog):
    enqueue_event(settings, event())
    def fail(_):
        raise RuntimeError("sk_test_secret-in-error-must-never-be-logged")
    gateway.retrieve_subscription = fail
    with caplog.at_level(logging.WARNING, logger="billing.worker"):
        assert jobs.run_once(settings, gateway, now=2_000_000_000)
        assert not jobs.run_once(settings, gateway, now=2_000_000_001)
        assert jobs.run_once(settings, gateway, now=2_000_000_002)
        assert jobs.run_once(settings, gateway, now=2_000_000_006)
    with connect(settings.db_path) as db:
        row = db.execute("SELECT * FROM webhook_jobs").fetchone()
        assert row["status"] == "failed" and row["attempts"] == 3
        assert row["last_error_type"] == "RuntimeError"
    assert "BACKGROUND_JOB_FAILED" in caplog.text
    assert "secret-in-error" not in caplog.text
    assert any(record.levelno == logging.CRITICAL for record in caplog.records)
    assert usage(settings.db_path, "tenant_a")["plan"] == "free"


def test_durable_job_recovers_after_restart_and_fences_stale_worker(settings, gateway):
    enqueue_event(settings, event())
    first = jobs.claim(settings.db_path, 2_000_000_000)
    assert first["attempts"] == 1
    assert jobs.claim(settings.db_path, 2_000_000_001) is None
    # A new worker using the same persisted database reclaims the crashed worker's job.
    assert jobs.run_once(settings, gateway, now=2_000_000_121)
    with connect(settings.db_path) as db:
        row = db.execute("SELECT * FROM webhook_jobs").fetchone()
        assert row["attempts"] == 2 and row["status"] == "done"
        assert row["lease_token"] != first["lease_token"]
    assert usage(settings.db_path, "tenant_a")["plan"] == "pro"


def test_stale_worker_cannot_commit_after_lease_is_reclaimed(settings, gateway):
    enqueue_event(settings, event())
    retrieve = gateway.retrieve_subscription
    def reclaim_during_fetch(sub_id):
        assert jobs.claim(settings.db_path, 2_000_000_121) is not None
        return retrieve(sub_id)
    gateway.retrieve_subscription = reclaim_during_fetch
    assert jobs.run_once(settings, gateway, now=2_000_000_000)
    assert usage(settings.db_path, "tenant_a")["plan"] == "free"


@pytest.mark.parametrize("change", ["wrong_customer", "live", "wrong_price", "wrong_quantity"])
def test_worker_rejects_wrong_customer_mode_or_plan(settings, gateway, change):
    if change == "wrong_customer":
        gateway.subscriptions["sub_a"]["customer"] = "cus_other"
    elif change == "live":
        gateway.subscriptions["sub_a"]["livemode"] = True
    elif change == "wrong_quantity":
        gateway.subscriptions["sub_a"]["items"]["data"][0]["quantity"] = 2
    else:
        gateway.subscriptions["sub_a"]["items"]["data"][0]["price"]["id"] = "price_wrong"
    enqueue_event(settings, event())
    jobs.run_once(settings, gateway, now=2_000_000_000)
    assert usage(settings.db_path, "tenant_a")["plan"] == "free"


def test_unknown_customer_is_ignored_and_deduplicated(settings, gateway):
    payload = event(customer="cus_stranger")
    assert enqueue_event(settings, payload) == {"status": "ignored"}
    assert enqueue_event(settings, payload) == {"status": "duplicate"}
    assert not jobs.run_once(settings, gateway)
    assert usage(settings.db_path, "tenant_b")["plan"] == "free"


def test_late_old_subscription_deletion_cannot_undo_new_active_one(settings, gateway):
    gateway.subscriptions["sub_a"]["status"] = "canceled"
    enqueue_event(settings, event("evt_old", event_type="customer.subscription.deleted"))
    jobs.run_once(settings, gateway)
    gateway.subscriptions["sub_new"] = subscription(sub_id="sub_new")
    enqueue_event(settings, event("evt_new", sub_id="sub_new"))
    jobs.run_once(settings, gateway)
    enqueue_event(settings, event("evt_late_old", event_type="customer.subscription.deleted"))
    jobs.run_once(settings, gateway)
    assert usage(settings.db_path, "tenant_a")["plan"] == "pro"
    with connect(settings.db_path) as db:
        assert db.execute("SELECT status FROM webhook_jobs WHERE event_id='evt_late_old'").fetchone()[0] == "ignored"


def test_three_crashed_attempts_alert_and_operator_retry(settings, gateway, caplog):
    enqueue_event(settings, event())
    for attempt in range(3):
        assert jobs.claim(settings.db_path, 2_000_000_000 + attempt * 121)["attempts"] == attempt + 1
    with caplog.at_level(logging.CRITICAL):
        assert jobs.claim(settings.db_path, 2_000_000_363) is None
    assert "LeaseExpired" in caplog.text
    assert jobs.retry_failed(settings.db_path, "evt_fixture")
    assert not jobs.retry_failed(settings.db_path, "evt_fixture")
    assert jobs.run_once(settings, gateway)
    assert not jobs.retry_failed(settings.db_path, "evt_fixture")
    assert usage(settings.db_path, "tenant_a")["plan"] == "pro"

