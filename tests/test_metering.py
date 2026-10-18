from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import pytest

from app import metering
from app.config import Settings
from app.db import connect, key_hash, migrate, tenant_for_key
from app.models import DomainError, Generation
from app.seed import seed
from tests.conftest import KEY_A, KEY_B


def test_exact_pricing_and_monthly_rollup(settings):
    # 1000 API + (1000-400)*2 fresh + 400*1 cached + 500*8 output = 6600.
    request = Generation(input_tokens=1000, cached_input_tokens=400, output_tokens=500, reasoning_tokens=200)
    result = metering.record(settings.db_path, "tenant_a", request, "priced")
    assert result["cost_micro_usd"] == 6600
    assert result["cost_usd"] == "0.006600"
    assert result["ai_tokens"] == 1500
    summary = metering.usage(settings.db_path, "tenant_a")
    assert summary["cost_micro_usd"] == 6600
    assert summary["used"]["reasoning_tokens"] == 200
    assert summary["used"]["events"] == 1


def test_simultaneous_retries_create_one_event_and_original_response(settings):
    def send(_):
        return metering.record(settings.db_path, "tenant_a", Generation(output_tokens=20), "same")
    with ThreadPoolExecutor(max_workers=12) as pool:
        responses = list(pool.map(send, range(24)))
    assert all(response == responses[0] for response in responses)
    assert metering.usage(settings.db_path, "tenant_a")["used"]["events"] == 1
    with pytest.raises(DomainError) as conflict:
        metering.record(settings.db_path, "tenant_a", Generation(output_tokens=21), "same")
    assert conflict.value.status == 409


def test_exact_api_call_boundary_then_429(settings):
    for index in range(1000):
        metering.record(settings.db_path, "tenant_a", Generation(), str(index))
    assert metering.usage(settings.db_path, "tenant_a")["used"]["api_calls"] == 1000
    with pytest.raises(DomainError) as failure:
        metering.record(settings.db_path, "tenant_a", Generation(), "over")
    assert failure.value.status == 429
    assert failure.value.body["meter"] == "api_calls"
    assert int(failure.value.headers["Retry-After"]) > 0
    assert metering.usage(settings.db_path, "tenant_a")["used"]["events"] == 1000
    assert metering.record(settings.db_path, "tenant_a", Generation(), "999")["api_calls"] == 1


def test_token_boundary_and_concurrent_last_slot(settings):
    metering.record(settings.db_path, "tenant_a", Generation(input_tokens=99_999), "before")
    def send(index):
        try:
            metering.record(settings.db_path, "tenant_a", Generation(output_tokens=1), f"race-{index}")
            return 200
        except DomainError as error:
            return error.status
    with ThreadPoolExecutor(max_workers=10) as pool:
        statuses = list(pool.map(send, range(10)))
    assert statuses.count(200) == 1
    assert statuses.count(429) == 9
    assert metering.usage(settings.db_path, "tenant_a")["used"]["ai_tokens"] == 100_000
    # An action needing zero additional tokens is allowed at the token boundary.
    metering.record(settings.db_path, "tenant_a", Generation(), "api-only")


def test_402_and_retry_still_returns_original(settings):
    original = metering.record(settings.db_path, "tenant_a", Generation(), "already-done")
    with connect(settings.db_path, write=True) as db:
        db.execute("UPDATE subscriptions SET plan_id='pro', status='past_due' WHERE tenant_id='tenant_a'")
    with pytest.raises(DomainError) as failure:
        metering.record(settings.db_path, "tenant_a", Generation(), "new")
    assert failure.value.status == 402
    assert failure.value.body["subscription_status"] == "past_due"
    assert metering.record(settings.db_path, "tenant_a", Generation(), "already-done") == original


def test_utc_month_rollover_retains_old_idempotency(settings, monkeypatch):
    monkeypatch.setattr(metering, "utc_now", lambda: datetime(2026, 1, 31, 23, 59, tzinfo=timezone.utc))
    original = metering.record(settings.db_path, "tenant_a", Generation(input_tokens=100_000), "january")
    monkeypatch.setattr(metering, "utc_now", lambda: datetime(2026, 2, 1, tzinfo=timezone.utc))
    assert metering.usage(settings.db_path, "tenant_a")["used"]["api_calls"] == 0
    assert metering.record(settings.db_path, "tenant_a", Generation(input_tokens=100_000), "january") == original
    metering.record(settings.db_path, "tenant_a", Generation(input_tokens=100_000), "february")
    assert metering.usage(settings.db_path, "tenant_a")["used"]["ai_tokens"] == 100_000


def test_tenant_isolation_persistence_migrations_and_safe_reseed(settings):
    migrate(settings.db_path)
    metering.record(settings.db_path, "tenant_a", Generation(output_tokens=3), "shared-key")
    assert metering.usage(settings.db_path, "tenant_b")["used"]["events"] == 0
    metering.record(settings.db_path, "tenant_b", Generation(), "shared-key")
    seed(settings, {"tenant_a": KEY_A, "tenant_b": KEY_B})
    assert metering.usage(settings.db_path, "tenant_a")["used"]["events"] == 1
    assert tenant_for_key(settings.db_path, KEY_A) == "tenant_a"
    assert tenant_for_key(settings.db_path, "wrong") is None
    with connect(settings.db_path) as db:
        assert db.execute("SELECT api_key_hash FROM tenants WHERE id='tenant_a'").fetchone()[0] == key_hash(KEY_A)
        assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] >= 1
    with pytest.raises(ValueError, match="refuses to rotate"):
        seed(settings, {"tenant_a": "c" * 40, "tenant_b": KEY_B})


@pytest.mark.parametrize("data", [
    {"input_tokens": -1}, {"input_tokens": 1.0}, {"input_tokens": True},
    {"cached_input_tokens": 1}, {"reasoning_tokens": 1}, {"output_tokens": "2"},
    {"input_tokens": 1_000_000_001}, {"tenant_id": "tenant_b"},
])
def test_token_validation(data):
    with pytest.raises(ValueError):
        Generation(**data)


def test_live_key_rejected_without_echoing_it(tmp_path):
    key = "sk_live_do_not_echo_this"
    with pytest.raises(ValueError) as failure:
        Settings(db_path=tmp_path / "unused", stripe_secret=key)
    assert key not in str(failure.value)

