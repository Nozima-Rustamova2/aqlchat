"""Redis-backed job queue for Instagram comment processing - the webhook
(app/instagram/router.py) must return 200 to Meta immediately, so all
Graph API work happens in a separate worker process (app/instagram/worker.py)
that drains this queue.

Deliberately minimal: a Redis list (LPUSH here, BRPOP in the worker) plus
a sorted set for rate-limit deferral (score = epoch seconds when the job
becomes due again). No Celery/arq - Meta's private-reply window is 7 days,
so latency tolerance is enormous and a single worker is enough at pilot
scale. Jobs are just comment_events row ids; the row itself is the
source of truth the worker reloads.
"""

import time

import redis

COMMENTS_QUEUE_KEY = "ig:comments"
DEFERRED_SET_KEY = "ig:deferred"


def enqueue_comment(redis_client: redis.Redis, comment_event_id: str) -> None:
    redis_client.lpush(COMMENTS_QUEUE_KEY, comment_event_id)


def defer_comment(redis_client: redis.Redis, comment_event_id: str, retry_at_epoch: float) -> None:
    redis_client.zadd(DEFERRED_SET_KEY, {comment_event_id: retry_at_epoch})


def pop_due_deferred(redis_client: redis.Redis, now_epoch: float | None = None) -> list[str]:
    """Atomically claim every deferred job whose retry time has passed.

    ZRANGEBYSCORE + ZREM in a pipeline would race a second worker;
    ZPOPMIN-style claiming via ZRANGEBYSCORE then per-member ZREM keeps
    only-one-claimer semantics (ZREM returns 0 for the loser).
    """
    now = time.time() if now_epoch is None else now_epoch
    due = redis_client.zrangebyscore(DEFERRED_SET_KEY, "-inf", now)
    claimed = []
    for member in due:
        if redis_client.zrem(DEFERRED_SET_KEY, member):
            claimed.append(member)
    return claimed
