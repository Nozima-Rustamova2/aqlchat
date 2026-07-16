"""Per-merchant hourly cap on Instagram Graph API calls - Meta allows
~200 calls/hour per connected IG account and each matched comment costs
up to 2 (private reply + public reply). Cap at 180 for headroom; a capped
job is DEFERRED to the next hour (app/instagram/queue.py's sorted set),
never dropped - the 7-day reply window means late is fine, missing is not.
A 1,000-comment viral reel drains in ~10 hours, which is acceptable.

Same soft/approximate INCR-before-check design as app/llm/budget.py, and
the same safe direction of imprecision: the counter can only overcount,
so we back off slightly early rather than slightly late.
"""

import datetime as dt

import redis

HOURLY_CALL_CAP = 180
CALLS_PER_MATCH = 2

_KEY_TTL_SECONDS = 7200  # 2 hours - safety margin past the hour boundary


def _current_hour() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H")


def try_consume_calls(redis_client: redis.Redis, merchant_id: str, calls: int = CALLS_PER_MATCH) -> bool:
    key = f"ig_budget:{merchant_id}:{_current_hour()}"
    count = redis_client.incrby(key, calls)
    redis_client.expire(key, _KEY_TTL_SECONDS)
    return count <= HOURLY_CALL_CAP


def next_hour_epoch() -> float:
    """When a capped job becomes due again: the top of the next UTC hour."""
    now = dt.datetime.now(dt.timezone.utc)
    next_hour = (now + dt.timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
    return next_hour.timestamp()
