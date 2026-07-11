"""Per-customer and per-merchant daily caps on LLM fallback calls, so one
bored customer (or a competitor) spamming novel messages can't run up a
paid-API bill. Both caps are placeholders - "200 merchant / 10 customer
per day" - with no real traffic to calibrate against yet; expect to
retune after pilot.

Soft/approximate by design: both counters increment via Redis INCR before
either cap is checked, so a request that gets rejected by the
customer-level cap still counted against the merchant-level one. That's
the safe direction to be imprecise in (the budget depletes slightly
faster than true LLM-call count, never slower) and keeps this lock-free -
not worth a transaction for a soft daily cap.
"""

import datetime as dt

import redis

DAILY_MERCHANT_CALL_LIMIT = 200
DAILY_CUSTOMER_CALL_LIMIT = 10

_KEY_TTL_SECONDS = 172800  # 2 days - safety margin past the daily boundary


def _today() -> str:
    return dt.datetime.now(dt.timezone.utc).date().isoformat()


def check_and_consume_budget(redis_client: redis.Redis, merchant_id: str, customer_id: str) -> bool:
    today = _today()
    merchant_key = f"llm_budget:merchant:{merchant_id}:{today}"
    customer_key = f"llm_budget:customer:{merchant_id}:{customer_id}:{today}"

    merchant_count = redis_client.incr(merchant_key)
    redis_client.expire(merchant_key, _KEY_TTL_SECONDS)
    if merchant_count > DAILY_MERCHANT_CALL_LIMIT:
        return False

    customer_count = redis_client.incr(customer_key)
    redis_client.expire(customer_key, _KEY_TTL_SECONDS)
    if customer_count > DAILY_CUSTOMER_CALL_LIMIT:
        return False

    return True
