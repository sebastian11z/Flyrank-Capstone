import calendar
import hashlib
import json
from datetime import datetime, timedelta, timezone
from math import ceil
from pathlib import Path
from uuid import uuid4

from app.config import (
    API_CALL_MICRO_USD, CACHED_TOKEN_MICRO_USD, INPUT_TOKEN_MICRO_USD,
    OUTPUT_TOKEN_MICRO_USD, PRICING_VERSION,
)
from app.db import connect
from app.models import DomainError, Generation

UTC = timezone.utc
PAYMENT_BLOCKED = {"past_due", "unpaid", "incomplete", "incomplete_expired", "paused"}


def utc_now():
    return datetime.now(UTC)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def usd_string(micro_usd: int):
    return f"{micro_usd // 1_000_000}.{micro_usd % 1_000_000:06d}"


def cost_of(request: Generation):
    return (
        API_CALL_MICRO_USD
        + (request.input_tokens - request.cached_input_tokens) * INPUT_TOKEN_MICRO_USD
        + request.cached_input_tokens * CACHED_TOKEN_MICRO_USD
        + request.output_tokens * OUTPUT_TOKEN_MICRO_USD
    )


def plan_for(db, tenant_id):
    row = db.execute(
        "SELECT s.*, p.api_call_limit, p.token_limit FROM subscriptions s "
        "JOIN plans p ON p.id = s.plan_id WHERE s.tenant_id = ?", (tenant_id,),
    ).fetchone()
    if row is None:
        raise DomainError(404, "tenant_not_found", "Tenant subscription not found")
    return row


def rollup(db, tenant_id, period):
    return dict(db.execute(
        "SELECT COUNT(*) AS events, COALESCE(SUM(api_calls),0) AS api_calls, "
        "COALESCE(SUM(input_tokens),0) AS input_tokens, "
        "COALESCE(SUM(cached_input_tokens),0) AS cached_input_tokens, "
        "COALESCE(SUM(output_tokens),0) AS output_tokens, "
        "COALESCE(SUM(reasoning_tokens),0) AS reasoning_tokens, "
        "COALESCE(SUM(cost_micro_usd),0) AS cost_micro_usd "
        "FROM usage_events WHERE tenant_id = ? AND period = ?", (tenant_id, period),
    ).fetchone())


def record(path: Path, tenant_id: str, request: Generation, key: str):
    request_hash = hashlib.sha256(canonical(request.model_dump()).encode()).hexdigest()
    with connect(path, write=True) as db:
        prior = db.execute(
            "SELECT request_hash, response_json FROM usage_events "
            "WHERE tenant_id = ? AND idempotency_key = ?", (tenant_id, key),
        ).fetchone()
        if prior:
            if prior["request_hash"] != request_hash:
                raise DomainError(409, "idempotency_conflict", "This key belongs to a different request")
            return json.loads(prior["response_json"])

        now = utc_now()
        period = now.strftime("%Y-%m")
        plan = plan_for(db, tenant_id)
        if plan["status"] in PAYMENT_BLOCKED:
            raise DomainError(402, "payment_required", "Resolve your Stripe subscription payment before generating",
                              subscription_status=plan["status"])
        used = rollup(db, tenant_id, period)
        tokens = request.input_tokens + request.output_tokens
        for meter, current, requested, limit in (
            ("api_calls", used["api_calls"], 1, plan["api_call_limit"]),
            ("ai_tokens", used["input_tokens"] + used["output_tokens"], tokens, plan["token_limit"]),
        ):
            if current + requested > limit:
                last = calendar.monthrange(now.year, now.month)[1]
                reset = now.replace(day=last, hour=0, minute=0, second=0, microsecond=0)
                reset += timedelta(days=1)
                retry = max(1, ceil((reset - now).total_seconds()))
                raise DomainError(429, "quota_exceeded", f"Monthly {meter} quota exceeded; wait for reset or upgrade",
                                  headers={"Retry-After": str(retry)}, meter=meter, used=current,
                                  requested=requested, limit=limit, resets_at=reset.isoformat())

        cost = cost_of(request)
        result = {
            "event_id": str(uuid4()), "tenant_id": tenant_id, "period": period,
            "result": "Simulated generation completed", "api_calls": 1,
            **request.model_dump(), "ai_tokens": tokens,
            "cost_micro_usd": cost, "cost_usd": usd_string(cost), "pricing_version": PRICING_VERSION,
        }
        db.execute(
            "INSERT INTO usage_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (result["event_id"], tenant_id, key, request_hash, now.isoformat(), period, 1,
             request.input_tokens, request.cached_input_tokens, request.output_tokens,
             request.reasoning_tokens, cost, PRICING_VERSION, json.dumps(result, separators=(",", ":"))),
        )
        return result


def usage(path: Path, tenant_id: str):
    period = utc_now().strftime("%Y-%m")
    with connect(path) as db:
        plan = plan_for(db, tenant_id)
        used = rollup(db, tenant_id, period)
        return {
            "tenant_id": tenant_id, "period": period, "period_timezone": "UTC",
            "plan": plan["plan_id"], "subscription_status": plan["status"],
            "used": {**used, "ai_tokens": used["input_tokens"] + used["output_tokens"]},
            "limit": {"api_calls": plan["api_call_limit"], "ai_tokens": plan["token_limit"]},
            "cost_micro_usd": used["cost_micro_usd"], "cost_usd": usd_string(used["cost_micro_usd"]),
            "pricing": {"version": PRICING_VERSION, "currency": "USD", "unit": "micro-USD",
                        "api_call": API_CALL_MICRO_USD, "input_token": INPUT_TOKEN_MICRO_USD,
                        "cached_input_token": CACHED_TOKEN_MICRO_USD, "output_token": OUTPUT_TOKEN_MICRO_USD,
                        "reasoning": "included in output_tokens; no second charge"},
        }

