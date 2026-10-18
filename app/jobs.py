import logging
import threading
import time
from uuid import uuid4

from app.db import connect
from app.metering import utc_now

logger = logging.getLogger("billing.worker")
MAX_ATTEMPTS = 3
LEASE_SECONDS = 120


def claim(path, now):
    with connect(path, write=True) as db:
        # One in-flight sync globally; fencing below also protects lease recovery.
        if db.execute("SELECT 1 FROM webhook_jobs WHERE status='processing' AND lease_until > ?", (now,)).fetchone():
            return None
        row = db.execute(
            "SELECT * FROM webhook_jobs WHERE (status='pending' AND available_at <= ?) "
            "OR (status='processing' AND lease_until <= ?) ORDER BY (status='processing') DESC,available_at,event_id LIMIT 1", (now, now),
        ).fetchone()
        if not row:
            return None
        if row["attempts"] >= MAX_ATTEMPTS:
            db.execute("UPDATE webhook_jobs SET status='failed', last_error_type='LeaseExpired' WHERE event_id=?",
                       (row["event_id"],))
            logger.critical("BACKGROUND_JOB_FAILED event_id=%s error_type=LeaseExpired", row["event_id"])
            return None
        token = str(uuid4())
        db.execute("UPDATE webhook_jobs SET status='processing', attempts=attempts+1, lease_until=?,lease_token=? "
                   "WHERE event_id=?", (now + LEASE_SECONDS, token, row["event_id"]))
        return {**dict(row), "attempts": row["attempts"] + 1, "lease_token": token}


def run_once(settings, gateway, *, now=None):
    now = int(time.time()) if now is None else now
    job = claim(settings.db_path, now)
    if not job:
        return False
    try:
        subscription = gateway.retrieve_subscription(job["subscription_id"])
        if (subscription.get("livemode") is not False or subscription.get("customer") != job["customer_id"]
                or subscription.get("id") != job["subscription_id"]):
            raise ValueError("Subscription ownership or mode mismatch")
        items = subscription.get("items", {}).get("data", [])
        if (len(items) != 1 or items[0].get("price", {}).get("id") != settings.price_id
                or items[0].get("quantity") != 1):
            raise ValueError("Subscription is not the configured Pro plan")
        status = subscription.get("status")
        if status not in {"active", "trialing", "past_due", "unpaid", "incomplete", "incomplete_expired", "paused", "canceled"}:
            raise ValueError("Unsupported subscription status")
        plan_id = "free" if status in {"canceled", "incomplete_expired"} else "pro"
        local_status = "free" if plan_id == "free" else status
        with connect(settings.db_path, write=True) as db:
            owned = db.execute("SELECT 1 FROM webhook_jobs WHERE event_id=? AND status='processing' AND lease_token=?",
                               (job["event_id"], job["lease_token"])).fetchone()
            if not owned:
                return True
            current = db.execute("SELECT * FROM subscriptions WHERE tenant_id=?", (job["tenant_id"],)).fetchone()
            if current["stripe_subscription_id"] not in (None, subscription["id"]) and current["status"] != "free":
                if plan_id != "free":
                    raise ValueError("Another subscription is already assigned to this tenant")
                outcome = "ignored"  # A late deletion of an older subscription cannot undo a new one.
            else:
                db.execute("UPDATE subscriptions SET plan_id=?,status=?,stripe_subscription_id=?,updated_at=? WHERE tenant_id=?",
                           (plan_id, local_status, subscription["id"], utc_now().isoformat(), job["tenant_id"]))
                if plan_id == "free":
                    db.execute("DELETE FROM checkout_attempts WHERE tenant_id=?", (job["tenant_id"],))
                outcome = "done"
            db.execute("UPDATE webhook_jobs SET status=?,lease_until=NULL,last_error_type=NULL WHERE event_id=?",
                       (outcome, job["event_id"]))
        logger.info("BACKGROUND_JOB_DONE event_id=%s", job["event_id"])
    except Exception as error:
        terminal = job["attempts"] >= MAX_ATTEMPTS
        with connect(settings.db_path, write=True) as db:
            changed = db.execute(
                "UPDATE webhook_jobs SET status=?,available_at=?,lease_until=NULL,last_error_type=? "
                "WHERE event_id=? AND status='processing' AND lease_token=?",
                ("failed" if terminal else "pending", now + 2 ** job["attempts"], type(error).__name__,
                 job["event_id"], job["lease_token"]),
            ).rowcount
        if changed:
            # Exception text and payloads can contain credentials/PII: log only type and event ID.
            log = logger.critical if terminal else logger.warning
            log("%s event_id=%s error_type=%s", "BACKGROUND_JOB_FAILED" if terminal else "BACKGROUND_JOB_RETRY",
                job["event_id"], type(error).__name__)
    return True


def worker_loop(settings, gateway, stop: threading.Event):
    while not stop.is_set():
        try:
            worked = run_once(settings, gateway)
        except Exception as error:
            logger.error("BACKGROUND_WORKER_ERROR error_type=%s", type(error).__name__)
            worked = False
        stop.wait(0.1 if worked else 1)


def retry_failed(path, event_id):
    with connect(path, write=True) as db:
        return bool(db.execute("UPDATE webhook_jobs SET status='pending',attempts=0,available_at=?,"
                               "lease_until=NULL,lease_token=NULL,last_error_type=NULL "
                               "WHERE event_id=? AND status='failed'", (int(time.time()), event_id)).rowcount)


if __name__ == "__main__":
    import argparse
    from app.config import Settings
    parser = argparse.ArgumentParser(description="Retry a failed job after correcting its configuration/cause")
    parser.add_argument("event_id")
    args = parser.parse_args()
    if not retry_failed(Settings.from_env().db_path, args.event_id):
        parser.exit(1, "No failed job matched. Completed jobs are never reset.\n")
    print("Failed job queued for retry.")

